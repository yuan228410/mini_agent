"""LLM API 通信"""
import requests

from config import MODEL_CONFIG
from logger import logger
from tools import get_definitions

API_URL = MODEL_CONFIG["api_url"]
API_KEY = MODEL_CONFIG["api_key"]
MODEL = MODEL_CONFIG["model"]


def chat(messages, tools=True):
    """发送请求，tools=True=全部工具，tools=list=自定义工具列表，tools=False=无工具"""
    payload = {"model": MODEL, "messages": messages}
    if tools is True:
        payload["tools"] = get_definitions()
        payload["tool_choice"] = "auto"
    elif tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    try:
        response = requests.post(API_URL, json=payload, headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }, timeout=60)
    except requests.RequestException as e:
        logger.error(f"请求异常: {e}")
        return None

    try:
        result = response.json()
    except ValueError:
        logger.error(f"响应解析失败: {response.text[:200]}")
        return None

    if "choices" not in result:
        logger.error(f"API Error: {result}")
        return None

    return result["choices"][0]["message"]