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


def get_temperature(ctx=None):
    return get_config(ctx).get("temperature")


def get_max_tokens(ctx=None):
    return get_config(ctx).get("max_tokens")


def get_top_p(ctx=None):
    return get_config(ctx).get("top_p")


def get_reasoning_effort(ctx=None):
    return get_config(ctx).get("reasoning_effort")


# 内部标记字段（prune 增量裁剪用），不发给 LLM API
_INTERNAL_FIELDS = frozenset(("_pruned", "_prune_level", "_is_summary"))

def _strip_internal_fields(messages: list[dict]) -> list[dict]:
    """剥离内部标记字段，返回清理后的消息列表（不修改原列表）"""
    needs_strip = False
    for m in messages:
        if _INTERNAL_FIELDS & m.keys():
            needs_strip = True
            break
    if not needs_strip:
        return messages
    return [{k: v for k, v in m.items() if k not in _INTERNAL_FIELDS} for m in messages]


def estimate_tokens(text: str) -> int:
    # 字节长度近似：CJK 3 字节 ≈ 1 token，ASCII 1 字节 ≈ 0.33 token
    # bytes // 3 对 CJK 精确，对 ASCII 高估 ~33%（阈值判断偏保守，安全）
    return max(1, len(text.encode('utf-8')) // 3)


# estimate_messages_tokens 缓存：key = (id, len, last_msg_hash)
_ESTIMATE_CACHE: dict[tuple, int] = {}
_ESTIMATE_CACHE_MAX = 10

def estimate_messages_tokens(messages: list[dict]) -> int:
    # 缓存 key：消息列表 id + 长度 + 首尾消息 id（检测原地替换 messages[:]=new）
    # 首尾 id 同时变化才能确认内容已变，避免 messages[:]=pruned 后缓存误命中
    cache_key = (id(messages), len(messages),
                 id(messages[0]) if messages else 0,
                 id(messages[-1]) if messages else 0)
    cached = _ESTIMATE_CACHE.get(cache_key)
    if cached is not None:
        return cached

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

    # LRU 淘汰
    if len(_ESTIMATE_CACHE) >= _ESTIMATE_CACHE_MAX:
        oldest = next(iter(_ESTIMATE_CACHE))
        del _ESTIMATE_CACHE[oldest]
    _ESTIMATE_CACHE[cache_key] = total

    return total


# ── Thread-local usage store ──

_local = threading.local()


def get_usage() -> dict:
    if not hasattr(_local, "last_usage"):
        _local.last_usage = {"prompt_tokens": 0, "completion_tokens": 0}
    return _local.last_usage



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
