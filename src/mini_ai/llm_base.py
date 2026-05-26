"""LLM 共享基础设施 — config 读取、session 管理、usage 追踪"""
import threading

import requests

from .config import MODEL_CONFIG


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
    return get_config(ctx).get("context_length", 128000)


# ── Thread-local usage store ──

_local = threading.local()


def get_usage() -> dict:
    if not hasattr(_local, "last_usage"):
        _local.last_usage = {"prompt_tokens": 0, "completion_tokens": 0}
    return _local.last_usage


# ── Session management ──

_fallback_session = requests.Session()


def get_session(ctx=None) -> requests.Session:
    return ctx.http_session if ctx else _fallback_session


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
