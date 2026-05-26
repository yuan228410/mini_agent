"""LLM 通信子包 — 多协议适配（OpenAI / Anthropic）"""
from .base import get_usage, get_config, get_api_url, get_api_key, get_model, get_api_mode, get_session
from .openai import chat, chat_stream, _get_usage
