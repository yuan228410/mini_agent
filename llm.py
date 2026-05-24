"""LLM API 通信"""
import requests

from config import MODEL_CONFIG
from logger import logger
from tools import get_definitions

API_URL = MODEL_CONFIG["api_url"]
API_KEY = MODEL_CONFIG["api_key"]
MODEL = MODEL_CONFIG["model"]


def chat(messages, tools=True):
    payload = {"model": MODEL, "messages": messages}
    if tools:
        payload["tools"] = get_definitions()
        payload["tool_choice"] = "auto"
    response = requests.post(API_URL, json=payload, headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    })
    result = response.json()

    if "choices" not in result:
        logger.error(f"API Error: {result}")
        return None

    return result["choices"][0]["message"]