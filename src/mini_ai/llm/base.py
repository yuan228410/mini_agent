"""LLM 共享基础设施 — config 读取、session 管理、usage 追踪"""
import threading

import requests

from ..config import MODEL_CONFIG
from ..core.messages import to_provider_messages
from ..core.runtime_types import MessageDict


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


# 内部标记字段（prune 增量裁剪用 + thinking 仅供前端显示），不发给 LLM API
_INTERNAL_FIELDS = frozenset(("_pruned", "_prune_level", "_is_summary", "thinking"))

def _strip_internal_fields(messages: list[MessageDict]) -> list[MessageDict]:
    """剥离内部标记字段，返回 provider-safe 消息列表（不修改原列表）。"""
    return to_provider_messages(messages)




def rebuild_tool_messages(messages: list[MessageDict]) -> list[MessageDict]:
    """重建消息结构：去掉 tool_calls 和 tool 消息，只保留对话正文。

    历史上下文不需要完整的工具调用细节（tool_calls + tool 结果），
    assistant 的 content 文本已包含最终回复。去掉可节省 20-40% context token。
    同时避免孤立 tool 消息导致 API 400 错误。
    """
    result = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            continue
        if role == "assistant" and m.get("tool_calls"):
            cleaned = {k: v for k, v in m.items() if k != "tool_calls"}
            if not cleaned.get("content"):
                tool_names = [tc.get("function", {}).get("name", "?") for tc in m["tool_calls"] if isinstance(tc, dict)]
                cleaned["content"] = f"[调用了工具: {', '.join(tool_names)}]"
            result.append(cleaned)
        else:
            result.append(m)
    return result

def estimate_tokens(text: str) -> int:
    return max(1, len(text.encode('utf-8')) // 3)


# estimate_messages_tokens 缓存：线程安全
_ESTIMATE_CACHE: dict[tuple, int] = {}
_ESTIMATE_CACHE_MAX = 256
_ESTIMATE_CACHE_LOCK = threading.Lock()

def _message_fingerprint(msg: MessageDict) -> tuple:
    content = msg.get("content") or ""
    if isinstance(content, str):
        content_sig = (len(content), hash(content[:256]), hash(content[-256:]))
    elif isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text") or block.get("type") or ""
                parts.append((block.get("type"), len(str(text)), hash(str(text)[:128])))
            else:
                text = str(block)
                parts.append(("str", len(text), hash(text[:128])))
        content_sig = tuple(parts)
    else:
        content_sig = (type(content).__name__, len(str(content)))

    tool_calls = msg.get("tool_calls") or []
    tool_sig = tuple(
        (call.get("id"), call.get("function", {}).get("name"), len(call.get("function", {}).get("arguments", "")))
        for call in tool_calls if isinstance(call, dict)
    )
    return (id(msg), msg.get("role"), msg.get("tool_call_id"), msg.get("name"), content_sig, tool_sig)


def estimate_messages_tokens(messages: list[MessageDict]) -> int:
    # 所有消息都参与轻量指纹，避免原地修改未采样中间消息时复用过期估算
    cache_key = (id(messages), len(messages), tuple(_message_fingerprint(m) for m in messages))
    with _ESTIMATE_CACHE_LOCK:
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

    with _ESTIMATE_CACHE_LOCK:
        if len(_ESTIMATE_CACHE) >= _ESTIMATE_CACHE_MAX:
            oldest = next(iter(_ESTIMATE_CACHE))
            del _ESTIMATE_CACHE[oldest]
        _ESTIMATE_CACHE[cache_key] = total

    return total


# ── Global usage store (thread-safe) ──
# P0#3: 全局原子计数器，多线程共享真实 usage 数据。
# openai.py/anthropic.py 在流式过程中通过 get_usage() 获取可变引用来累积，
# 最终调用 commit_usage() 原子提交到全局。

_global_usage_lock = threading.Lock()
_global_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}

# 线程局部可变 usage（供 openai.py/anthropic.py 在单次 LLM 调用中累积）
_local = threading.local()

def get_usage() -> dict:
    """返回线程局部 usage（可变引用，供 LLM 层累积）"""
    if not hasattr(_local, "last_usage"):
        _local.last_usage = {"prompt_tokens": 0, "completion_tokens": 0}
    return _local.last_usage

def commit_usage():
    """将线程局部 usage 原子提交到全局计数器，并重置线程局部值

    prompt_tokens 用 = 替换（API 返回的是当前请求的绝对值，非增量）；
    completion_tokens 用 += 累加（API 返回的是本次生成的增量）。
    多会话并发时 prompt_tokens 取最新提交的值。
    """
    if not hasattr(_local, "last_usage"):
        return
    with _global_usage_lock:
        # prompt_tokens: 替换语义（API 返回当前请求的 prompt token 数）
        _global_usage["prompt_tokens"] = _local.last_usage.get("prompt_tokens", 0)
        # completion_tokens: 累加语义（API 返回本次生成的增量）
        _global_usage["completion_tokens"] += _local.last_usage.get("completion_tokens", 0)
    _local.last_usage = {"prompt_tokens": 0, "completion_tokens": 0}

def get_global_usage() -> dict:
    """返回全局 usage 快照（供前端/WebDisplay 使用，线程安全）"""
    with _global_usage_lock:
        return dict(_global_usage)

def reset_usage():
    """重置线程局部 usage（保留全局累积）"""
    _local.last_usage = {"prompt_tokens": 0, "completion_tokens": 0}

def reset_global_usage():
    """重置全局 usage 计数器"""
    with _global_usage_lock:
        _global_usage["prompt_tokens"] = 0
        _global_usage["completion_tokens"] = 0


# ── Session management (thread-local for safety) ──

def get_session(ctx=None) -> requests.Session:
    if ctx:
        return ctx.http_session
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


def close_thread_session():
    """关闭当前线程持有的默认 HTTP session。"""
    sess = getattr(_local, "session", None)
    if sess is not None:
        sess.close()
        delattr(_local, "session")


def ensure_session_openai(ctx=None):
    cfg = get_config(ctx)
    sess = get_session(ctx)
    key = cfg["api_key"]

    auth_value = f"Bearer {key}"
    sess.headers.pop("x-api-key", None)
    sess.headers.pop("anthropic-version", None)
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
    sess.headers.pop("Authorization", None)
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
