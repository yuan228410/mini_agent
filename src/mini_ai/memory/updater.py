"""记忆更新器：根据压缩摘要更新三层记忆（episode / memory / user）。"""
import time
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

    def __init__(self, memory_store: MemoryStore, max_retries: int = 3):
        self.memory = memory_store
        self.max_retries = max_retries

    def update(self, chat_fn, round_summaries: list[str], ctx=None) -> None:
        """调用 LLM 更新 episode / memory / user。"""
        if not round_summaries:
            logger.debug("[记忆更新] 无摘要，跳过")
            return
        logger.info(f"[记忆更新] 开始: {len(round_summaries)} 条摘要")

        prompt = COMPACT_PROMPT.format(
            current_memory=self.memory.read_memory() or "(空)",
            current_user=self.memory.read_user() or "(空)",
            today_episode=self.memory.read_today() or "(空)",
            round_summaries="\n\n".join(round_summaries),
        )

        # 带重试的 LLM 调用
        result = None
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                result = chat_fn([{"role": "user", "content": prompt}], tools=None, ctx=ctx)
                if result:
                    break
                logger.warning(f"[记忆更新] LLM 返回为空，尝试 {attempt}/{self.max_retries}")
            except Exception as e:
                last_error = e
                logger.warning(f"[记忆更新] LLM 调用失败: {e}，尝试 {attempt}/{self.max_retries}")
            
            if attempt < self.max_retries:
                time.sleep(1 * attempt)  # 递增等待：1s, 2s, 3s

        if not result:
            logger.error(f"[记忆更新] 重试 {self.max_retries} 次后仍失败，跳过记忆更新")
            if last_error:
                logger.error(f"[记忆更新] 最后错误: {last_error}")
            return

        text = result.get("content", "")
        logger.debug(f"[记忆更新] LLM 响应: {len(text)} 字")
        episode = _extract("episode", text)
        new_memory = _extract("updated_memory", text)
        new_user = _extract("updated_user", text)
        logger.debug(f"[记忆更新] 解析结果: episode={'有' if episode else '无'}, memory={'有' if new_memory else '无'}, user={'有' if new_user else '无'}")

        if episode:
            logger.info(f"[记忆更新] 写入情景: {len(episode)} 字")
            self.memory.append_today(episode)
        else:
            logger.debug("[记忆更新] 无情景更新")
        if new_memory and new_memory != "(无需更新)":
            logger.info(f"[记忆更新] 写入长期记忆: {len(new_memory)} 字")
            self.memory.write_memory(new_memory)
        else:
            logger.debug("[记忆更新] 无长期记忆更新")
        if new_user and new_user != "(无需更新)":
            logger.info(f"[记忆更新] 写入用户画像: {len(new_user)} 字")
            self.memory.write_user(new_user)
        else:
            logger.debug("[记忆更新] 无用户画像更新")
        logger.info("[记忆更新] 完成")
