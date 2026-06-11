"""LLM 共享基础设施 — config 读取、session 管理、usage 追踪"""
import threading

import requests

from ..config import MODEL_CONFIG


def get_config(ctx=None):
    return ctx.model_config if ctx else MODEL_CONFIG


def get_api_url(ctx=None):
    return get_config(ctx)["api_url"]


def get_api_key(ctx=None):
    return get_config(ctx)["api_key"]


def get_model(ctx=None):
    return get_config(ctx)["model"]


def get_api_mode(ctx=None):
    return get_config(ctx).get("api_mode", "openai")


def get_context_length(ctx=None):
    return get_config(ctx).get("context_length", 256000)

def get_temperature(ctx=None):
    return get_config(ctx).get("temperature")


def get_max_tokens(ctx=None):
    return get_config(ctx).get("max_tokens")


def get_top_p(ctx=None):
    return get_config(ctx).get("top_p")


def get_reasoning_effort(ctx=None):
    return get_config(ctx).get("reasoning_effort")


def estimate_tokens(text: str) -> int:
    cjk = 0
    other = 0
    for ch in text:
        cp = ord(ch)
        if (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or
            0x20000 <= cp <= 0x2A6DF or 0xF900 <= cp <= 0xFAFF or
            0x3000 <= cp <= 0x303F or 0x3040 <= cp <= 0x309F or
            0x30A0 <= cp <= 0x30FF or 0xAC00 <= cp <= 0xD7AF):
            cjk += 1
        else:
            other += 1
    return int(cjk / 1.0 + other / 4.0)


def estimate_messages_tokens(messages: list[dict]) -> int:
    total = 0
    for msg in messages:
        content = msg.get("content") or ""
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    total += estimate_tokens(block.get("text", ""))
                elif isinstance(block, str):
                    total += estimate_tokens(block)
        tc = msg.get("tool_calls")
        if tc:
            for call in tc:
                args = call.get("function", {}).get("arguments", "")
                if args:
                    total += estimate_tokens(args)
        total += 4
    return total


# ── Thread-local usage store ──

_local = threading.local()


def get_usage() -> dict:
    if not hasattr(_local, "last_usage"):
        _local.last_usage = {"prompt_tokens": 0, "completion_tokens": 0}
    return _local.last_usage


def update_usage(prompt_tokens: int = 0, completion_tokens: int = 0):
    usage = get_usage()
    usage["prompt_tokens"] += prompt_tokens
    usage["completion_tokens"] += completion_tokens


def reset_usage():
    _local.last_usage = {"prompt_tokens": 0, "completion_tokens": 0}


# ── Session management (thread-local for safety) ──


def get_session(ctx=None) -> requests.Session:
    if ctx:
        return ctx.http_session
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


def ensure_session_openai(ctx=None):
    cfg = get_config(ctx)
    sess = get_session(ctx)
    key = cfg["api_key"]
    mode = get_api_mode(ctx)

    if mode == "anthropic":
        if sess.headers.get("x-api-key") != key:
            sess.headers.update({
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            })
            custom_headers = cfg.get("headers", {})
            if custom_headers:
                sess.headers.update(custom_headers)
    else:
        auth_value = f"Bearer {key}"
        if sess.headers.get("Authorization") != auth_value:
            sess.headers.update({
                "Authorization": auth_value,
                "Content-Type": "application/json",
            })
            custom_headers = cfg.get("headers", {})
            if custom_headers:
                sess.headers.update(custom_headers)


def ensure_session_anthropic(ctx=None):
    cfg = get_config(ctx)
    sess = get_session(ctx)
    key = cfg["api_key"]
    if sess.headers.get("x-api-key") != key:
        sess.headers.update({
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        })
        custom_headers = cfg.get("headers", {})
        if custom_headers:
            sess.headers.update(custom_headers)

# ── Context overflow detection ──

_OVERFLOW_KEYWORDS = ("context_length", "prompt is too long", "request too large", "input is too long")

def detect_context_overflow(status: int, body: str) -> bool:
    """检测 API 返回是否为上下文溢出错误（status==400 且 body 含关键词）"""
    if status != 400:
        return False
    lower = body.lower()
    return any(kw in lower for kw in _OVERFLOW_KEYWORDS)
