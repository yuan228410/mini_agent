"""网页抓取工具"""
import re
import urllib.request
from html.parser import HTMLParser

from config import TIMEOUTS
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

_STRIP_TAGS = {"style", "script", "noscript", "svg", "head"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._text = []
        self._skip_depth = 0
        self._skip_tag = None

    def handle_starttag(self, tag, attrs):
        if tag in _STRIP_TAGS and self._skip_depth == 0:
            self._skip_depth = 1
            self._skip_tag = tag

    def handle_endtag(self, tag):
        if self._skip_depth > 0 and tag == self._skip_tag:
            self._skip_depth = 0
            self._skip_tag = None

    def handle_data(self, data):
        if self._skip_depth > 0:
            return
        self._text.append(data)

    def get_text(self):
        raw = " ".join(self._text)
        return _collapse_ws(raw).strip()


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _strip_html(raw: str) -> str:
    parser = _TextExtractor()
    parser.feed(raw)
    return parser.get_text()


def execute(args: dict) -> str:
    url = args.get("url")
    if not url:
        return "错误：缺少 url 参数"
    extract_mode = args.get("extract_mode", "text")
    max_chars = args.get("max_chars", 8000)

    logger.info(f"[抓取→] {url} mode={extract_mode}")

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUTS["web_fetch"]) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        logger.debug(f"[抓取✗] {url}: {e}")
        return f"Error fetching {url}: {e}"

    if extract_mode == "text":
        text = _strip_html(raw)
    else:
        text = raw

    logger.debug(f"[抓取←] {url} chars={len(text)}")
    return text[:max_chars]
