"""Anthropic Claude 适配层 — 对外暴露 chat() / chat_stream()，签名对齐 llm.py"""
import json
import threading
import time

import requests

from .config import MODEL_CONFIG, TIMEOUTS, THINKING
from .logger import logger


def _api_url():
    return MODEL_CONFIG["api_url"]

def _api_key():
    return MODEL_CONFIG["api_key"]

def _model():
    return MODEL_CONFIG["model"]

_local = threading.local()
_local.last_usage = {"prompt_tokens": 0, "completion_tokens": 0}


def _get_usage() -> dict:
    if not hasattr(_local, "last_usage"):
        _local.last_usage = {"prompt_tokens": 0, "completion_tokens": 0}
    return _local.last_usage

_session = requests.Session()

def _ensure_session():
    key = _api_key()
    if _session.headers.get("x-api-key") != key:
        _session.headers.update({
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        })
        custom_headers = MODEL_CONFIG.get("headers", {})
        if custom_headers:
            _session.headers.update(custom_headers)


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
            result.append({"role": "user", "content": content or ""})

        elif role == "assistant":
            tc = m.get("tool_calls")
            if tc:
                blocks = []
                if content:
                    blocks.append({"type": "text", "text": content})
                for t in tc:
                    fn = t["function"]
                    blocks.append({
                        "type": "tool_use",
                        "id": t["id"],
                        "name": fn["name"],
                        "input": json.loads(fn["arguments"]) if fn.get("arguments") else {},
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
        btype = block.get("type", "")
        if btype == "thinking":
            thinking_parts.append(block.get("thinking", ""))
        elif btype == "text":
            text_parts.append(block["text"])
        elif btype == "tool_use":
            tool_calls.append({
                "id": block["id"],
                "type": "function",
                "function": {
                    "name": block["name"],
                    "arguments": json.dumps(block["input"], ensure_ascii=False),
                },
            })

    content = "\n".join(text_parts) if text_parts else None
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if thinking_parts:
        msg["thinking"] = "\n".join(thinking_parts)
    return msg


def chat(messages, tools=True):
    """非流式请求，返回 OpenAI 格式的 msg dict"""
    from .tools import get_definitions

    system_text, ant_msgs = _openai_to_anthropic(messages)

    payload = {"model": _model(), "messages": ant_msgs, "max_tokens": 4096}
    if system_text:
        payload["system"] = system_text

    if THINKING.get("enabled"):
        budget = THINKING.get("budget_tokens", 10000)
        payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
        payload["max_tokens"] = budget + 4096

    if tools is True:
        payload["tools"] = _tools_openai_to_anthropic(get_definitions())
    elif tools:
        payload["tools"] = _tools_openai_to_anthropic(tools)

    logger.info(f"[Anth→] model={_model()} msgs={len(ant_msgs)} tools={len(payload.get('tools', []))}")

    t0 = time.monotonic()
    try:
        _ensure_session()
        response = _session.post(_api_url(), json=payload, timeout=TIMEOUTS["llm"])
    except requests.RequestException as e:
        logger.error(f"[Anth✗] 请求异常: {e}")
        return None

    if response.status_code != 200:
        logger.error(f"[Anth✗] HTTP {response.status_code}: {response.text[:500]}")
        return None

    data = response.json()
    usage = data.get("usage", {})
    us = _get_usage()
    us["prompt_tokens"] = usage.get("input_tokens", 0)
    us["completion_tokens"] = usage.get("output_tokens", 0)

    msg = _anthropic_to_openai_msg(data.get("content", []), data.get("stop_reason", ""))

    elapsed = time.monotonic() - t0
    if "tool_calls" in msg:
        names = [tc["function"]["name"] for tc in msg["tool_calls"]]
        logger.info(f"[Anth←] tool_calls={names} | {elapsed:.1f}s")
    else:
        text = (msg.get("content") or "")[:100]
        logger.info(f"[Anth←] text={text} | {elapsed:.1f}s")

    return msg


def chat_stream(messages, tools=True):
    """流式请求，yield {"type": "text"|"done", ...} 对齐 llm.py"""
    from .tools import get_definitions

    system_text, ant_msgs = _openai_to_anthropic(messages)

    payload = {"model": _model(), "messages": ant_msgs, "max_tokens": 4096, "stream": True}
    if system_text:
        payload["system"] = system_text

    if THINKING.get("enabled"):
        budget = THINKING.get("budget_tokens", 10000)
        payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
        payload["max_tokens"] = budget + 4096

    if tools is True:
        payload["tools"] = _tools_openai_to_anthropic(get_definitions())
    elif tools:
        payload["tools"] = _tools_openai_to_anthropic(tools)

    logger.info(f"[Anth→] model={_model()} msgs={len(ant_msgs)} tools={len(payload.get('tools', []))} [stream]")

    t0 = time.monotonic()
    try:
        _ensure_session()
        response = _session.post(_api_url(), json=payload, timeout=TIMEOUTS["llm"], stream=True)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"[Anth✗] 流式请求异常: {e}")
        yield {"type": "error", "error": str(e)}
        return

    blocks = []
    current_block = None
    input_tokens = output_tokens = 0

    for line in response.iter_lines(decode_unicode=True):
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
            if dtype == "thinking_delta":
                chunk_text = delta.get("thinking", "")
                current_block["thinking"] += chunk_text
                yield {"type": "thinking", "content": chunk_text}
            elif dtype == "text_delta":
                text = delta["text"]
                current_block["text"] += text
                yield {"type": "text", "content": text}
            elif dtype == "input_json_delta":
                current_block["input_json"] += delta.get("partial_json", "")

        elif evt_type == "content_block_stop":
            if current_block:
                if current_block["type"] == "thinking":
                    yield {"type": "thinking_end"}
                if current_block["type"] == "tool_use" and current_block["input_json"]:
                    try:
                        current_block["input"] = json.loads(current_block["input_json"])
                    except (ValueError, json.JSONDecodeError):
                        pass
                blocks.append(current_block)
                current_block = None

        elif evt_type == "message_delta":
            usage = data.get("usage", {})
            output_tokens = usage.get("output_tokens", 0)

        elif evt_type == "message_start":
            usage = data.get("message", {}).get("usage", {})
            input_tokens = usage.get("input_tokens", 0)

    us = _get_usage()
    us["prompt_tokens"] = input_tokens
    us["completion_tokens"] = output_tokens

    msg = _anthropic_to_openai_msg(blocks, "")
    elapsed = time.monotonic() - t0
    logger.info(f"[Anth←] (stream) | {elapsed:.1f}s | tok={input_tokens}+{output_tokens}")
    yield {"type": "done", "msg": msg}
