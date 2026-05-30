"""内部工具函数，供 compactor / updater 共享。"""
import re


def extract_tag(tag: str, text: str) -> str | None:
    """从文本中提取 <tag>...</tag> 内容。"""
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    return m.group(1).strip() if m else None
