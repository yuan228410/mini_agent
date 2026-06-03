"""Anthropic Claude 适配层 — 对外暴露 chat() / chat_stream()，签名对齐 llm.py"""
import json
import re
import time

import requests

from ..config import TIMEOUTS, THINKING as _GLOBAL_THINKING
from .base import (
    get_api_url, get_api_key, get_model,
    get_temperature, get_max_tokens, get_top_p, get_reasoning_effort,
    get_usage, update_usage, get_session, ensure_session_anthropic,
    estimate_tokens, estimate_messages_tokens,
)
from ..logger import logger
from ..exceptions import LLMError
from .retry import RetryStrategy


def _get_thinking(ctx=None):
    if ctx and ctx.model_config and 'thinking' in ctx.model_config:
        return {**_GLOBAL_THINKING, **ctx.model_config['thinking']}
    return _GLOBAL_THINKING


def _openai_to_anthropic(messages: list[dict]) -> tuple[str, list[dict]]:
    """返回 (system_text, anthropic_messages)"""
    system_text = ""
    result = []
    for m in messages:
        role = m["role"]
        content = m.get("content")

        if role == "system":
            system_text = content or ""
            continue

        if role == "user":
            # 处理图片消息（content 可能是列表）
            if isinstance(content, list):
                blocks = []
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            blocks.append({"type": "text", "text": item.get("text", "")})
                        elif item.get("type") == "image_url":
                            # OpenAI 格式转 Anthropic 格式
                            image_url = item.get("image_url", {})
                            url = image_url.get("url", "")
                            if url.startswith("data:"):
                                # data:image/jpeg;base64,xxx -> 提取信息
                                match = re.match(r"data:([^;]+);base64,(.+)", url)
                                if match:
                                    media_type = match.group(1)  # image/jpeg
                                    data = match.group(2)  # base64 数据
                                    # Anthropic 原生格式
                                    blocks.append({
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": media_type,
                                            "data": data
                                        }
                                    })
                            else:
                                # URL 图片，Anthropic 也支持直接传 URL
                                blocks.append({
                                    "type": "image",
                                    "source": {"type": "url", "url": url}
                                })
                    elif isinstance(item, str):
                        blocks.append({"type": "text", "text": item})
                result.append({"role": "user", "content": blocks})
            else:
                result.append({"role": "user", "content": content or ""})

        elif role == "assistant":
            tc = m.get("tool_calls")
            if tc:
                blocks = []
                if content:
                    blocks.append({"type": "text", "text": content})
                for t in tc:
                    fn = t["function"]
                    try:
                        parsed = json.loads(fn["arguments"]) if fn.get("arguments") else {}
                    except (json.JSONDecodeError, TypeError):
                        parsed = {}
                        logger.warning(f"[Anth] tool_call 参数解析失败: {fn.get('arguments', '')[:200]}")
                    blocks.append({
                        "type": "tool_use",
                        "id": t["id"],
                        "name": fn["name"],
                        "input": parsed,
                    })
                result.append({"role": "assistant", "content": blocks})
            else:
                result.append({"role": "assistant", "content": content or ""})

        elif role == "tool":
            result.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": m["tool_call_id"],
                    "content": content or "",
                }],
            })

    return system_text, result


def _tools_openai_to_anthropic(tools: list[dict]) -> list[dict]:
    result = []
    for t in tools:
        fn = t["function"]
        result.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return result


def _anthropic_to_openai_msg(ant_content: list[dict], stop_reason: str) -> dict:
    """Anthropic 响应 content 数组 → OpenAI 格式 msg dict"""
    text_parts = []
    tool_calls = []
    thinking_parts = []

    for block in ant_content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")
        if btype == "thinking":
            thinking_parts.append(block.get("thinking", ""))
        elif btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "tool_use":
            tool_calls.append({
                "id": block.get("id", ""),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                },
            })

    content = "\n".join(text_parts) if text_parts else None
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if thinking_parts:
        msg["thinking"] = "\n".join(thinking_parts)
    return msg


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
    if effort and not _get_thinking(ctx).get("enabled"):
        payload["thinking"] = {"type": "adaptive"}
        payload["output_config"] = {"effort": effort}


def chat(messages, tools=True, ctx=None):
    """非流式请求，返回 OpenAI 格式的 msg dict"""
    from ..tools import get_definitions

    system_text, ant_msgs = _openai_to_anthropic(messages)

    max_tokens = get_max_tokens(ctx) or 4096
    payload = {"model": get_model(ctx), "messages": ant_msgs, "max_tokens": max_tokens}
    _apply_model_params(payload, ctx)
    if system_text:
        payload["system"] = system_text

    thinking_type = _get_thinking(ctx).get("type", "enabled")
    if _get_thinking(ctx).get("enabled"):
        budget = _get_thinking(ctx).get("budget_tokens", 10000)
        if thinking_type == "adaptive":
            payload["thinking"] = {"type": "adaptive"}
        else:
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
        payload["max_tokens"] = budget + 4096
        logger.debug(f"[Anth] thinking模式: max_tokens={budget + 4096}, temperature=1 (覆盖用户配置)")
        payload["temperature"] = 1

    if tools is True:
        payload["tools"] = _tools_openai_to_anthropic(get_definitions())
    elif tools:
        payload["tools"] = _tools_openai_to_anthropic(tools)

    logger.info(f"[Anth→] model={get_model(ctx)} msgs={len(ant_msgs)} tools={len(payload.get('tools', []))} thinking={thinking_type if _get_thinking(ctx).get('enabled') else 'off'}")

    max_retries = TIMEOUTS.get("llm_retries", 3)
    retry_delay = TIMEOUTS.get("llm_retry_delay", 2)
    
    # 使用智能重试策略
    strategy = RetryStrategy(
        max_retries=max_retries,
        base_delay=retry_delay,
        max_delay=60.0,
    )
    
    t0 = time.monotonic()
    response = None
    last_error = None
    
    for attempt in range(max_retries + 1):
        try:
            ensure_session_anthropic(ctx)
            sess = get_session(ctx)
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
                
                last_error = LLMError(err_msg, status_code=response.status_code, retry_after=retry_after)
                
                # 判断是否重试
                if strategy.should_retry(last_error, attempt):
                    delay = strategy.get_delay(attempt, last_error)
                    logger.warning(f"[Anth↻] 重试 {attempt+1}/{max_retries}: {err_msg}，{delay:.1f}s 后重试")
                    time.sleep(delay)
                    continue
                else:
                    logger.error(f"[Anth✗] 不可重试的错误: {err_msg}")
                    raise last_error
            
            # 成功，跳出重试循环
            break
            
        except requests.RequestException as e:
            error_msg = str(e)
            if "timed out" in error_msg.lower() or "timeout" in error_msg.lower():
                error_msg = f"请求超时（连接:{connect_timeout}s, 读取:{read_timeout}s）"
            last_error = LLMError(error_msg)
            
            if strategy.should_retry(last_error, attempt):
                delay = strategy.get_delay(attempt, last_error)
                logger.warning(f"[Anth↻] 重试 {attempt+1}/{max_retries}: {error_msg}，{delay:.1f}s 后重试")
                time.sleep(delay)
            else:
                logger.error(f"[Anth✗] 请求异常(已重试{attempt}次): {error_msg}")
                raise last_error

    try:
        data = response.json()
    except ValueError as e:
        logger.error(f"[Anth✗] 响应解析失败: {response.text[:500]}")
        raise LLMError(f"响应解析失败: {e}", status_code=getattr(response, "status_code", 0))
    if not isinstance(data, dict):
        logger.error(f"[Anth✗] 响应格式异常: {type(data)}")
        raise LLMError(f"响应格式异常: {type(data).__name__}", status_code=getattr(response, "status_code", 0))
    usage = data.get("usage", {})
    us = get_usage()
    inp = usage.get("input_tokens", 0)
    out = usage.get("output_tokens", 0)
    if inp:
        us["prompt_tokens"] = inp
    else:
        us["prompt_tokens"] = estimate_messages_tokens(messages)
    if out:
        us["completion_tokens"] += out
    else:
        content_text = data.get("content", [])
        est = sum(estimate_tokens(b.get("text", "")) for b in content_text if isinstance(b, dict) and b.get("type") == "text")
        if est:
            us["completion_tokens"] += est

    msg = _anthropic_to_openai_msg(data.get("content", []), data.get("stop_reason", ""))

    elapsed = time.monotonic() - t0
    if "tool_calls" in msg:
        names = [tc["function"]["name"] for tc in msg["tool_calls"]]
        logger.info(f"[Anth←] tool_calls={names} | {elapsed:.1f}s")
    else:
        text = (msg.get("content") or "")[:100]
        logger.info(f"[Anth←] text={text} | {elapsed:.1f}s")

    return msg


def chat_stream(messages, tools=True, ctx=None, abort_event=None):
    """流式请求，yield {"type": "text"|"done", ...} 对齐 llm.py"""
    from ..tools import get_definitions

    system_text, ant_msgs = _openai_to_anthropic(messages)

    max_tokens = get_max_tokens(ctx) or 4096
    payload = {"model": get_model(ctx), "messages": ant_msgs, "max_tokens": max_tokens, "stream": True}
    _apply_model_params(payload, ctx)
    if system_text:
        payload["system"] = system_text

    thinking_type = _get_thinking(ctx).get("type", "enabled")
    if _get_thinking(ctx).get("enabled"):
        budget = _get_thinking(ctx).get("budget_tokens", 10000)
        if thinking_type == "adaptive":
            payload["thinking"] = {"type": "adaptive"}
        else:
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
        payload["max_tokens"] = budget + 4096
        logger.debug(f"[Anth] thinking模式: max_tokens={budget + 4096}, temperature=1 (覆盖用户配置)")
        payload["temperature"] = 1

    if tools is True:
        payload["tools"] = _tools_openai_to_anthropic(get_definitions())
    elif tools:
        payload["tools"] = _tools_openai_to_anthropic(tools)

    logger.info(f"[Anth→] model={get_model(ctx)} msgs={len(ant_msgs)} tools={len(payload.get('tools', []))} thinking={thinking_type if _get_thinking(ctx).get('enabled') else 'off'} [stream]")

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
            ensure_session_anthropic(ctx)
            sess = get_session(ctx)
            # 流式请求：timeout=(连接超时, 读取超时)
            connect_timeout = TIMEOUTS.get("llm_connect", 30)
            read_timeout = TIMEOUTS.get("llm", 120)
            response = sess.post(get_api_url(ctx), json=payload, timeout=(connect_timeout, read_timeout), stream=True)
            response.raise_for_status()
            break
        except requests.RequestException as e:
            error_msg = str(e)
            if "timed out" in error_msg.lower() or "timeout" in error_msg.lower():
                error_msg = f"请求超时（连接:{connect_timeout}s, 读取:{read_timeout}s）"
            last_error = LLMError(error_msg)
            
            if strategy.should_retry(last_error, attempt):
                delay = strategy.get_delay(attempt, last_error)
                logger.warning(f"[Anth↻] 流式重试 {attempt+1}/{max_retries}: {error_msg}，{delay:.1f}s 后重试")
                time.sleep(delay)
            else:
                logger.error(f"[Anth✗] 流式请求异常(已重试{attempt}次): {error_msg}")
                yield {"type": "error", "error": error_msg}
                return

    get_usage()["_prev_completion"] = get_usage()["completion_tokens"]
    blocks = []
    current_block = None
    input_tokens = output_tokens = 0

    # 使用 iter_content 替代 iter_lines，支持读取超时
    buffer = ""
    response.encoding = "utf-8"
    try:
        for chunk in response.iter_content(chunk_size=None, decode_unicode=True):
            if not chunk:
                continue
            buffer += chunk
            
            # 按行处理
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                
                event_type = None
                data_str = None
                if line.startswith("event: "):
                    event_type = line[7:]
                    continue
                if line.startswith("data: "):
                    data_str = line[6:]

                if not data_str:
                    continue
                try:
                    data = json.loads(data_str)
                except (ValueError, json.JSONDecodeError):
                    continue

                evt_type = event_type or data.get("type")
                if evt_type == "content_block_start":
                    cb = data.get("content_block", {})
                    current_block = {"type": cb["type"], "index": data.get("index", 0)}
                    if cb["type"] == "text":
                        current_block["text"] = ""
                    elif cb["type"] == "thinking":
                        current_block["thinking"] = ""
                        yield {"type": "thinking_start"}
                    elif cb["type"] == "tool_use":
                        current_block["id"] = cb.get("id", "")
                        current_block["name"] = cb.get("name", "")
                        current_block["input"] = {}
                        current_block["input_json"] = ""

                elif evt_type == "content_block_delta":
                    delta = data.get("delta", {})
                    dtype = delta.get("type", "")
                    if not current_block:
                        current_block = {"type": "text", "index": 0, "text": ""}
                    if dtype == "thinking_delta":
                        chunk_text = delta.get("thinking", "")
                        if "thinking" not in current_block:
                            current_block["thinking"] = ""
                        current_block["thinking"] += chunk_text
                        yield {"type": "thinking", "content": chunk_text}
                    elif dtype == "text_delta":
                        text = delta.get("text", "")
                        if "text" not in current_block:
                            current_block["text"] = ""
                        current_block["text"] += text
                        yield {"type": "text", "content": text}
                    elif dtype == "input_json_delta":
                        if "input_json" not in current_block:
                            current_block["input_json"] = ""
                        current_block["input_json"] += delta.get("partial_json", "")

                elif evt_type == "content_block_stop":
                    if current_block:
                        if current_block.get("type") == "thinking":
                            yield {"type": "thinking_end"}
                        if current_block.get("type") == "tool_use" and current_block.get("input_json"):
                            try:
                                current_block["input"] = json.loads(current_block["input_json"])
                            except (ValueError, json.JSONDecodeError):
                                pass
                        blocks.append(current_block)
                        current_block = None

                elif evt_type == "message_delta":
                    usage = data.get("usage") or {}
                    output_tokens = usage.get("output_tokens", 0)

                elif evt_type == "message_start":
                    usage = data.get("message", {}).get("usage") or {}
                    input_tokens = usage.get("input_tokens", 0)
                    
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
        error_msg = f"读取响应流超时: {e}"
        logger.error(f"[Anth✗] {error_msg}")
        yield {"type": "error", "error": error_msg}
        return
    except Exception as e:
        error_msg = f"流式响应处理异常: {e}"
        logger.error(f"[Anth✗] {error_msg}", exc_info=True)
        yield {"type": "error", "error": error_msg}
        return
    finally:
        # 确保响应连接被关闭
        if response:
            response.close()

    us = get_usage()
    prev_comp = us.get("_prev_completion", 0)
    call_comp = us["completion_tokens"] - prev_comp
    if input_tokens:
        us["prompt_tokens"] = input_tokens
        us["_api_prompt"] = True
    elif not us.get("_api_prompt") and messages:
        est = estimate_messages_tokens(messages)
        us["prompt_tokens"] = est
    us.pop("_api_prompt", None)
    if output_tokens:
        us["completion_tokens"] = prev_comp + output_tokens
    elif call_comp == 0 and blocks:
        est = 0
        for b in blocks:
            if b.get("text"): est += estimate_tokens(b["text"])
            if b.get("thinking"): est += estimate_tokens(b["thinking"])
        us["completion_tokens"] = prev_comp + est
    us.pop("_prev_completion", None)

    msg = _anthropic_to_openai_msg(blocks, "")
    elapsed = time.monotonic() - t0
    logger.info(f"[Anth←] (stream) | {elapsed:.1f}s | tok={input_tokens}+{output_tokens}")
    yield {"type": "done", "msg": msg}
