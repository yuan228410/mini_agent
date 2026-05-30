"""网页抓取工具"""
import threading
import re
from html.parser import HTMLParser

import requests

from ..config import TIMEOUTS
from ..logger import logger

_MAX_CONSECUTIVE_FAILURES = 5
_consecutive_failures = 0
_fail_lock = threading.Lock()

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


def _detect_encoding(resp: requests.Response) -> str:
    """尽量获取正确的编码，避免乱码。"""
    # 1. 优先用 apparent_encoding（基于内容检测，如 <meta charset> 或 BOM）
    if resp.apparent_encoding:
        return resp.apparent_encoding
    # 2. 回退到 headers 声明的编码
    if resp.encoding:
        return resp.encoding
    # 3. 最后兜底
    return "utf-8"


def execute(args: dict) -> str:
    url = args.get("url")
    if not url:
        return "错误：缺少 url 参数"
    extract_mode = args.get("extract_mode", "text")
    max_chars = args.get("max_chars", 8000)
    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError):
        max_chars = 8000
    if max_chars <= 0:
        max_chars = 8000

    logger.info(f"[抓取→] {url} mode={extract_mode}")

    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=TIMEOUTS.get("web_fetch", 20))
        resp.raise_for_status()
        encoding = _detect_encoding(resp)
        resp.encoding = encoding
        raw = resp.text
    except Exception as e:
        logger.debug(f"[抓取✗] {url}: {e}")
        return f"Error fetching {url}: {e}"

    if extract_mode == "text":
        text = _strip_html(raw)
    else:
        text = raw

    # 追踪连续失败次数（异常或空内容均计）
    is_failure = text.strip().startswith("Error") or len(text.strip()) == 0
    with _fail_lock:
        if is_failure:
            _consecutive_failures += 1
        else:
            _consecutive_failures = 0
        failures = _consecutive_failures
    if failures >= _MAX_CONSECUTIVE_FAILURES:
        logger.warning(f"[抓取] 连续 {failures} 次抓取失败，建议换策略")
        return text[:max_chars] + f"\n\n⚠ 已连续 {failures} 次抓取失败，建议换用其他方式（如 run_command curl）或直接告知用户无法获取。"

    logger.debug(f"[抓取←] {url} chars={len(text)}")
    return text[:max_chars]
