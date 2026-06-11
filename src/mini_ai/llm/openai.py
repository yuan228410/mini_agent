"""LLM API 通信"""
import json
import time

import requests

from ..config import TIMEOUTS
from .base import (
    get_config, get_api_url, get_api_key, get_model, get_api_mode,
    get_temperature, get_max_tokens, get_top_p, get_reasoning_effort,
    get_usage, get_session, ensure_session_openai, detect_context_overflow,
    estimate_tokens, estimate_messages_tokens,
)
from ..logger import logger
from ..tools import get_definitions
from ..exceptions import LLMError
from .retry import RetryStrategy



def _msg_summary(m: dict) -> str:
    role = m.get("role", "?")
    content = m.get("content") or ""
    tc = m.get("tool_calls")
    if tc:
        parts = []
        for t in tc:
            fn = t["function"]
            parts.append(f"{fn['name']}({fn.get('arguments', '')})")
        return f"[{role}] tool_calls=[{', '.join(parts)}]"
    if role == "tool":
        tid = m.get("tool_call_id", "?")[:8]
        return f"[{role}] tid={tid} len={len(content)}"
    return f"[{role}] {content}"


def _apply_model_params(payload: dict, ctx=None):
    temperature = get_temperature(ctx)
    if temperature is not None:
        payload["temperature"] = temperature
    max_tokens = get_max_tokens(ctx)
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    top_p = get_top_p(ctx)
    if top_p is not None:
        payload["top_p"] = top_p
    effort = get_reasoning_effort(ctx)
    if effort is not None:
        payload["reasoning_effort"] = effort


def _attach_tools(payload: dict, tools) -> list[str] | None:
    tool_names = None
    if tools is True:
        defs = get_definitions()
        payload["tools"] = defs
        payload["tool_choice"] = "auto"
        tool_names = [d["function"]["name"] for d in defs]
    elif tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
        tool_names = [d["function"]["name"] for d in tools]
    return tool_names


def chat(messages, tools=True, ctx=None):
    ensure_session_openai(ctx)
    if get_api_mode(ctx) == "anthropic":
        from .anthropic import chat as anth_chat
        return anth_chat(messages, tools, ctx=ctx)
    from .base import _strip_internal_fields
    clean_msgs = _strip_internal_fields(messages)
    payload = {"model": get_model(ctx), "messages": clean_msgs}
    _apply_model_params(payload, ctx)
    tool_names = _attach_tools(payload, tools)

    logger.info(f"[LLM→] model={get_model(ctx)} msgs={len(messages)} tools={len(tool_names) if tool_names else 0}")
    if len(messages) <= 3:
        for m in messages:
            if m["role"] != "tool":
                logger.debug(f"  {_msg_summary(m)}")
    else:
        non_tool = [m for m in messages if m["role"] != "tool"]
        logger.debug(f"  [{len(messages) - len(non_tool)} 条工具结果省略]")
        for m in non_tool[-2:]:
            logger.debug(f"  {_msg_summary(m)}")
    if tool_names:
        logger.debug(f"  tool_list={tool_names}")

    max_retries = TIMEOUTS.get("llm_retries", 3)
    retry_delay = TIMEOUTS.get("llm_retry_delay", 2)
    
    # 使用智能重试策略
    strategy = RetryStrategy(
        max_retries=max_retries,
        base_delay=retry_delay,
        max_delay=60.0,
    )

    sess = get_session(ctx)
    t0 = time.monotonic()
    response = None
    last_error = None
    try:
        for attempt in range(max_retries + 1):
            try:
                connect_timeout = TIMEOUTS.get("llm_connect", 30)
                read_timeout = TIMEOUTS.get("llm", 120)
                response = sess.post(get_api_url(ctx), json=payload, timeout=(connect_timeout, read_timeout))
                if response.status_code >= 400:
                    err_msg = f"HTTP {response.status_code}: {response.text[:200]}"
                    
                    # 尝试解析错误详情
                    retry_after = None
                    try:
                        error_data = response.json()
                        if "error" in error_data:
                            err_detail = error_data["error"]
                            err_msg = f"HTTP {response.status_code}: {err_detail.get('message', err_msg)}"
                            # 提取 retry_after
                            if response.status_code == 429:
                                retry_after_header = response.headers.get("Retry-After")
                                if retry_after_header:
                                    try:
                                        retry_after = float(retry_after_header)
                                    except ValueError:
                                        pass
                                if retry_after is None and "retry_after" in err_detail:
                                    retry_after = err_detail["retry_after"]
                    except (ValueError, KeyError):
                        pass
                    
                    _is_overflow = detect_context_overflow(response.status_code, response.text[:500])
                    last_error = LLMError(err_msg, status_code=response.status_code, retry_after=retry_after, is_context_overflow=_is_overflow)
                    
                    # 上下文溢出：不重试，直接抛出让上层走 force_compact 路径
                    if _is_overflow:
                        logger.warning(f"[LLM✗] 上下文溢出: {err_msg}")
                        raise last_error
                    
                    # 判断是否重试
                    if strategy.should_retry(last_error, attempt):
                        delay = strategy.get_delay(attempt, last_error)
                        logger.warning(f"[LLM↻] 重试 {attempt+1}/{max_retries}: {err_msg}，{delay:.1f}s 后重试")
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(f"[LLM✗] 不可重试的错误: {err_msg}")
                        raise last_error
                    
                # 成功，跳出重试循环
                break
            except requests.RequestException as e:
                # 网络错误
                last_error = LLMError(str(e))
                
                if strategy.should_retry(last_error, attempt):
                    delay = strategy.get_delay(attempt, last_error)
                    logger.warning(f"[LLM↻] 重试 {attempt+1}/{max_retries}: {e}，{delay:.1f}s 后重试")
                    time.sleep(delay)
                else:
                    logger.error(f"[LLM✗] 请求异常(已重试{attempt}次): {e}")
                    raise last_error

        try:
            result = response.json()
        except ValueError as e:
            logger.error(f"[LLM✗] 响应解析失败: {response.text}")
            raise LLMError(f"响应解析失败: {e}", status_code=getattr(response, "status_code", 0))

        if "choices" not in result:
            err_msg = result.get("error", {}).get("message", str(result))
            logger.error(f"[LLM✗] API Error: {result}")
            raise LLMError(f"API 错误: {err_msg}", status_code=getattr(response, "status_code", 0))

        elapsed = time.monotonic() - t0
        choices = result.get("choices", [])
        if not choices:
            logger.error(f"[LLM✗] API 返回空 choices: {result}")
            raise LLMError("API 返回空 choices", status_code=getattr(response, "status_code", 0))
        msg = choices[0].get("message", {})
        if not msg:
            logger.error(f"[LLM✗] API 返回空 message: {result}")
            raise LLMError("API 返回空 message", status_code=getattr(response, "status_code", 0))
        reasoning = msg.pop("reasoning_content", None)
        if reasoning:
            msg["thinking"] = reasoning
        usage = result.get("usage") or {}
        p_tok = usage.get("prompt_tokens", 0)
        c_tok = usage.get("completion_tokens", 0)
        usage_store = get_usage()
        if p_tok:
            usage_store["prompt_tokens"] = p_tok
        else:
            usage_store["prompt_tokens"] = estimate_messages_tokens(messages)
        if c_tok:
            usage_store["completion_tokens"] += c_tok
        elif msg.get("content"):
            usage_store["completion_tokens"] += estimate_tokens(msg["content"])

        if "tool_calls" in msg:
            calls = msg["tool_calls"]
            summaries = []
            for tc in calls:
                fn = tc["function"]
                args_str = fn.get("arguments", "")
                summaries.append(f"{fn['name']}({args_str})")
            logger.info(f"[LLM←] tool_calls=[{', '.join(summaries)}] | {elapsed:.1f}s | tok={p_tok}+{c_tok}")
        else:
            text = msg.get('content') or ""
            logger.info(f"[LLM←] text={text} | {elapsed:.1f}s | tok={p_tok}+{c_tok}")

        return msg
    finally:
        # 确保响应连接被关闭
        if response:
            response.close()


def chat_stream(messages, tools=True, ctx=None, abort_event=None):
    ensure_session_openai(ctx)
    if get_api_mode(ctx) == "anthropic":
        from .anthropic import chat_stream as anth_stream
        yield from anth_stream(messages, tools, ctx=ctx)
        return
    from .base import _strip_internal_fields
    clean_msgs = _strip_internal_fields(messages)
    payload = {"model": get_model(ctx), "messages": clean_msgs, "stream": True, "stream_options": {"include_usage": True}}
    _apply_model_params(payload, ctx)
    tool_names = _attach_tools(payload, tools)

    logger.info(f"[LLM→] model={get_model(ctx)} msgs={len(messages)} tools={len(tool_names) if tool_names else 0} [stream]")
    if tool_names:
        logger.debug(f"  tool_list={tool_names}")

    sess = get_session(ctx)
    t0 = time.monotonic()
    max_retries = TIMEOUTS.get("llm_retries", 3)
    retry_delay = TIMEOUTS.get("llm_retry_delay", 2)
    
    # 使用智能重试策略
    strategy = RetryStrategy(
        max_retries=max_retries,
        base_delay=retry_delay,
        max_delay=60.0,
    )
    
    response = None
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            connect_timeout = TIMEOUTS.get("llm_connect", 30)
            read_timeout = TIMEOUTS.get("llm", 120)
            response = sess.post(get_api_url(ctx), json=payload, timeout=(connect_timeout, read_timeout), stream=True)
            
            # 检查 HTTP 状态码
            if response.status_code >= 400:
                err_msg = f"HTTP {response.status_code}: {response.text[:200] if response.text else ''}"
                
                # 尝试解析错误详情
                retry_after = None
                try:
                    error_data = response.json()
                    if "error" in error_data:
                        err_detail = error_data["error"]
                        err_msg = f"HTTP {response.status_code}: {err_detail.get('message', err_msg)}"
                        if response.status_code == 429:
                            retry_after_header = response.headers.get("Retry-After")
                            if retry_after_header:
                                try:
                                    retry_after = float(retry_after_header)
                                except ValueError:
                                    pass
                except (ValueError, KeyError):
                    pass
                
                _is_overflow = detect_context_overflow(response.status_code, (response.text or "")[:500])
                last_error = LLMError(err_msg, status_code=response.status_code, retry_after=retry_after, is_context_overflow=_is_overflow)
                
                # 上下文溢出：不重试，直接抛出让上层走 force_compact 路径
                if _is_overflow:
                    logger.warning(f"[LLM✗] 流式上下文溢出: {err_msg}")
                    yield {"type": "error", "error": err_msg, "is_context_overflow": True}
                    return
                
                # 判断是否重试
                if strategy.should_retry(last_error, attempt):
                    delay = strategy.get_delay(attempt, last_error)
                    logger.warning(f"[LLM↻] 流式重试 {attempt+1}/{max_retries}: {err_msg}，{delay:.1f}s 后重试")
                    time.sleep(delay)
                    continue
                else:
                    logger.error(f"[LLM✗] 流式请求不可重试: {err_msg}")
                    yield {"type": "error", "error": err_msg}
                    return
            
            # 成功，跳出重试循环
            response.raise_for_status()
            break
        except requests.RequestException as e:
            error_msg = str(e)
            status_code = 0
            # 提取 HTTP 状态码
            if isinstance(e, requests.HTTPError) and e.response is not None:
                status_code = e.response.status_code
            
            if "timed out" in error_msg.lower() or "timeout" in error_msg.lower():
                error_msg = f"请求超时（连接:{connect_timeout}s, 读取:{read_timeout}s）"
            
            last_error = LLMError(error_msg, status_code=status_code)
            
            if strategy.should_retry(last_error, attempt):
                delay = strategy.get_delay(attempt, last_error)
                logger.warning(f"[LLM↻] 流式重试 {attempt+1}/{max_retries}: {error_msg}，{delay:.1f}s 后重试")
                time.sleep(delay)
            else:
                logger.error(f"[LLM✗] 流式请求异常(已重试{attempt}次): {error_msg}")
                yield {"type": "error", "error": error_msg}
                return

    get_usage()["_prev_completion"] = get_usage()["completion_tokens"]
    collected_content = ""
    collected_thinking = ""
    in_thinking = False
    tool_call_buf: dict[int, dict] = {}

    try:
        response.encoding = "utf-8"
        for line in response.iter_lines(decode_unicode=True):
            if abort_event and abort_event.is_set():
                logger.debug("[LLM] 流式响应被中断")
                break
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                break
            try:
                data = json.loads(data_str)
            except (ValueError, json.JSONDecodeError):
                continue

            choices = data.get("choices", [])
            if not choices:
                continue
            delta = choices[0].get("delta", {})

            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if reasoning:
                if not in_thinking:
                    in_thinking = True
                    yield {"type": "thinking_start"}
                collected_thinking += reasoning
                yield {"type": "thinking", "content": reasoning}

            content_val = delta.get("content")
            if content_val:
                if in_thinking:
                    yield {"type": "thinking_end"}
                    in_thinking = False
                collected_content += content_val
                yield {"type": "text", "content": content_val}

            if "tool_calls" in delta:
                for tc in delta.get("tool_calls", []):
                    if not isinstance(tc, dict):
                        continue
                    idx = tc.get("index", 0)
                    if idx not in tool_call_buf:
                        tool_call_buf[idx] = {"id": tc.get("id", ""), "function": {"name": "", "arguments": ""}}
                    buf = tool_call_buf[idx]
                    if tc.get("id"):
                        buf["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        buf["function"]["name"] = fn["name"]
                    if "arguments" in fn and fn["arguments"] is not None:
                        buf["function"]["arguments"] += fn["arguments"]

            usage = data.get("usage") or {}
            if usage:
                usage_store = get_usage()
                p = usage.get("prompt_tokens", 0)
                c = usage.get("completion_tokens", 0)
                if p:
                    usage_store["prompt_tokens"] = p
                    usage_store["_api_prompt"] = True
                if c: usage_store["completion_tokens"] += c
                # 推送 usage 事件
                yield {"type": "usage", "prompt_tokens": p, "completion_tokens": c}
    finally:
        # 确保响应连接被关闭
        if response:
            response.close()

    elapsed = time.monotonic() - t0
    tool_calls = [{"id": buf.get("id", ""), "type": "function", "function": buf["function"]}
                  for _, buf in sorted(tool_call_buf.items())
                  if buf["function"].get("name")] if tool_call_buf else None

    msg = {"role": "assistant", "content": collected_content or None, "tool_calls": tool_calls} if tool_calls else {"role": "assistant", "content": collected_content or None}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if collected_thinking:
        msg["thinking"] = collected_thinking

    usage_store = get_usage()
    prev_comp = usage_store.get("_prev_completion", 0)
    call_comp = usage_store["completion_tokens"] - prev_comp
    if not usage_store.get("_api_prompt"):
        est_prompt = estimate_messages_tokens(messages)
        if tool_call_buf:
            for buf in tool_call_buf.values():
                est_prompt += estimate_tokens(buf["function"].get("arguments", ""))
        usage_store["prompt_tokens"] = est_prompt
    usage_store.pop("_api_prompt", None)
    if call_comp == 0:
        est_comp = estimate_tokens(collected_content)
        if collected_thinking:
            est_comp += estimate_tokens(collected_thinking)
        usage_store["completion_tokens"] = prev_comp + est_comp
    usage_store.pop("_prev_completion", None)
    p_tok = usage_store["prompt_tokens"]
    c_tok = usage_store["completion_tokens"]

    if tool_calls:
        summaries = [f"{tc['function']['name']}({tc['function']['arguments']})" for tc in tool_calls]
        logger.info(f"[LLM←] tool_calls=[{', '.join(summaries)}] (stream) | {elapsed:.1f}s | tok={p_tok}+{c_tok}")
    else:
        logger.info(f"[LLM←] text={collected_content} (stream) | {elapsed:.1f}s | tok={p_tok}+{c_tok}")

    yield {"type": "done", "msg": msg}
