"""记忆更新器：根据压缩摘要更新三层记忆（episode / memory / user）。"""
from ..logger import logger
from ._utils import extract_tag as _extract
from .store import MemoryStore

COMPACT_PROMPT = """根据对话轮次摘要，更新长期记忆和用户画像：

<episode>
今日关键记录（事实、结论、待办），用于每日回顾。保持简洁。
</episode>

<updated_memory>
如果产生了值得长期记住的信息（目标、决策、偏好、项目背景），更新长期记忆。
先写保留的旧记忆，再写新增内容。无需更新则写"(无需更新)"。
</updated_memory>

<updated_user>
如果更了解了用户的偏好、习惯、背景，更新用户画像。无需更新则写"(无需更新)"。
</updated_user>

当前长期记忆：
{current_memory}

当前用户画像：
{current_user}

今天的已有记录：
{today_episode}

各轮次摘要：
{round_summaries}"""


class MemoryUpdater:
    """根据压缩摘要调用 LLM 更新三层记忆。

    职责单一：接收轮次摘要，调用 chat_fn 获取 LLM 输出，解析并写入 MemoryStore。
    """

    def __init__(self, memory_store: MemoryStore):
        self.memory = memory_store

    def update(self, chat_fn, round_summaries: list[str], ctx=None) -> None:
        """调用 LLM 更新 episode / memory / user。"""
        if not round_summaries:
            return

        prompt = COMPACT_PROMPT.format(
            current_memory=self.memory.read_memory() or "(空)",
            current_user=self.memory.read_user() or "(空)",
            today_episode=self.memory.read_today() or "(空)",
            round_summaries="\n\n".join(round_summaries),
        )

        result = chat_fn([{"role": "user", "content": prompt}], tools=None, ctx=ctx)
        if not result:
            return

        text = result.get("content", "")
        episode = _extract("episode", text)
        new_memory = _extract("updated_memory", text)
        new_user = _extract("updated_user", text)

        if episode:
            self.memory.append_today(episode)
        if new_memory and new_memory != "(无需更新)":
            self.memory.write_memory(new_memory)
        if new_user and new_user != "(无需更新)":
            self.memory.write_user(new_user)
        logger.info("[压缩] 记忆更新完成")
