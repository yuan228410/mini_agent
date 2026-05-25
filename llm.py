"""LLM API 通信"""
import threading
import time

import requests

from config import MODEL_CONFIG, TIMEOUTS

API_MODE = MODEL_CONFIG.get("api_mode", "openai")
from logger import logger
from tools import get_definitions

API_URL = MODEL_CONFIG["api_url"]
API_KEY = MODEL_CONFIG["api_key"]
MODEL = MODEL_CONFIG["model"]
CONTEXT_LENGTH = MODEL_CONFIG.get("context_length", 128000)

_local = threading.local()
_local.last_usage = {"prompt_tokens": 0, "completion_tokens": 0}


def _get_usage() -> dict:
    if not hasattr(_local, "last_usage"):
        _local.last_usage = {"prompt_tokens": 0, "completion_tokens": 0}
    return _local.last_usage

_session = requests.Session()
_session.headers.update({
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "comate_custom_header": '{"username":"yuanzhixiang","source":"openclaw"}'
})


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


def chat(messages, tools=True):
    if API_MODE == "anthropic":
        from anthropic import chat as anth_chat
        return anth_chat(messages, tools)
    """发送请求，tools=True=全部工具，tools=list=自定义工具列表，tools=False=无工具"""
    payload = {"model": MODEL, "messages": messages}
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

    logger.info(f"[LLM→] model={MODEL} msgs={len(messages)} tools={len(tool_names) if tool_names else 0}")
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

    t0 = time.monotonic()
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            response = _session.post(API_URL, json=payload, timeout=TIMEOUTS["llm"])
            break
        except requests.RequestException as e:
            last_error = e
            if attempt < max_retries:
                delay = retry_delay * (attempt + 1)
                logger.warning(f"[LLM↻] 重试 {attempt+1}/{max_retries}: {e}，{delay}s 后重试")
                time.sleep(delay)
            else:
                logger.error(f"[LLM✗] 请求异常(已重试{max_retries}次): {e}")
                return None

    try:
        result = response.json()
    except ValueError:
        logger.error(f"[LLM✗] 响应解析失败: {response.text}")
        return None

    if "choices" not in result:
        logger.error(f"[LLM✗] API Error: {result}")
        return None

    elapsed = time.monotonic() - t0
    msg = result["choices"][0]["message"]
    reasoning = msg.pop("reasoning_content", None)
    if reasoning:
        msg["thinking"] = reasoning
    usage = result.get("usage", {})
    p_tok = usage.get("prompt_tokens", 0)
    c_tok = usage.get("completion_tokens", 0)
    usage_store = _get_usage()
    usage_store["prompt_tokens"] = p_tok
    usage_store["completion_tokens"] = c_tok

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


def chat_stream(messages, tools=True):
    if API_MODE == "anthropic":
        from anthropic import chat_stream as anth_stream
        yield from anth_stream(messages, tools)
        return
    """流式发送请求，yield delta chunks，最后 yield 完整 msg。

    用法:
        for chunk in chat_stream(messages, tools=tools):
            if chunk["type"] == "text":
                print(chunk["content"], end="", flush=True)
            elif chunk["type"] == "done":
                msg = chunk["msg"]  # 完整消息，等价于 chat() 返回值
    """
    payload = {"model": MODEL, "messages": messages, "stream": True}
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

    logger.info(f"[LLM→] model={MODEL} msgs={len(messages)} tools={len(tool_names) if tool_names else 0} [stream]")
    if tool_names:
        logger.debug(f"  tool_list={tool_names}")

    t0 = time.monotonic()
    try:
        response = _session.post(API_URL, json=payload, timeout=TIMEOUTS["llm"], stream=True)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"[LLM✗] 流式请求异常: {e}")
        yield {"type": "error", "error": str(e)}
        return

    collected_content = ""
    collected_thinking = ""
    in_thinking = False
    tool_call_buf: dict[int, dict] = {}  # index -> {"id","name","arguments"}

    for line in response.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        data_str = line[6:]
        if data_str == "[DONE]":
            break
        try:
            data = __import__("json").loads(data_str)
        except (ValueError, __import__("json").JSONDecodeError):
            continue

        delta = data.get("choices", [{}])[0].get("delta", {})

        reasoning = delta.get("reasoning_content")
        if reasoning:
            if not in_thinking:
                in_thinking = True
                yield {"type": "thinking_start"}
            collected_thinking += reasoning
            yield {"type": "thinking", "content": reasoning}

        if "content" in delta and delta["content"]:
            if in_thinking:
                yield {"type": "thinking_end"}
                in_thinking = False
            collected_content += delta["content"]
            yield {"type": "text", "content": delta["content"]}

        if "tool_calls" in delta:
            for tc in delta["tool_calls"]:
                idx = tc.get("index", 0)
                if idx not in tool_call_buf:
                    tool_call_buf[idx] = {"id": tc.get("id", ""), "function": {"name": "", "arguments": ""}}
                buf = tool_call_buf[idx]
                if "id" in tc and tc["id"]:
                    buf["id"] = tc["id"]
                fn = tc.get("function", {})
                if "name" in fn and fn["name"]:
                    buf["function"]["name"] = fn["name"]
                if "arguments" in fn:
                    buf["function"]["arguments"] += fn["arguments"]

        usage = data.get("usage")
        if usage:
            usage_store = _get_usage()
            usage_store["prompt_tokens"] = usage.get("prompt_tokens", 0)
            usage_store["completion_tokens"] = usage.get("completion_tokens", 0)

    elapsed = time.monotonic() - t0
    tool_calls = [{"id": buf["id"], "type": "function", "function": buf["function"]}
                  for buf in sorted(tool_call_buf.values(), key=lambda b: b.get("index", 0))
                  if buf["function"]["name"]] if tool_call_buf else None

    msg = {"role": "assistant", "content": collected_content or None}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if collected_thinking:
        msg["thinking"] = collected_thinking

    usage_store = _get_usage()
    p_tok = usage_store["prompt_tokens"]
    c_tok = usage_store["completion_tokens"]

    if tool_calls:
        summaries = [f"{tc['function']['name']}({tc['function']['arguments']})" for tc in tool_calls]
        logger.info(f"[LLM←] tool_calls=[{', '.join(summaries)}] (stream) | {elapsed:.1f}s | tok={p_tok}+{c_tok}")
    else:
        logger.info(f"[LLM←] text={collected_content} (stream) | {elapsed:.1f}s | tok={p_tok}+{c_tok}")

    yield {"type": "done", "msg": msg}
