"""网页抓取工具"""
import urllib.request
from html.parser import HTMLParser

from logger import logger

definition = {
    "type": "function",
    "function": {
        "name": "web_fetch",
        "description": "抓取网页URL内容并提取纯文本",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要抓取的网页URL"},
                "extract_mode": {"type": "string", "enum": ["text", "html"], "description": "text=提取纯文本, html=原始HTML"},
                "max_chars": {"type": "integer", "description": "最大返回字符数，默认8000"}
            },
            "required": ["url"]
        }
    }
}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._text = []

    def handle_data(self, data):
        self._text.append(data)

    def get_text(self):
        return " ".join(self._text).strip()


def execute(args: dict) -> str:
    url = args["url"]
    extract_mode = args.get("extract_mode", "text")
    max_chars = args.get("max_chars", 8000)

    logger.info(f"[抓取] {url}")

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return f"Error fetching {url}: {e}"

    if extract_mode == "text":
        parser = _TextExtractor()
        parser.feed(raw)
        text = parser.get_text()
    else:
        text = raw

    return text[:max_chars]