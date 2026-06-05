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

**重要**：
1. 必须先完整保留旧记忆的所有内容
2. 然后在末尾追加新增内容
3. 格式：[旧记忆内容]\n\n## 新增 [日期]\n[新增内容]
4. 如果只是补充信息，无需更新则写"(无需更新)"
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
            # 🔧 修复：改为追加模式，而非完全覆盖
            existing_memory = self.memory.read_memory()
            if existing_memory and existing_memory.strip() != "# 长期记忆":
                # 🔧 改进：使用行级比较，避免子串匹配误判
                # 计算旧记忆和新记忆的行交集比例，判断 LLM 是否保留了旧记忆
                old_lines = set(line.strip() for line in existing_memory.strip().split('\n') if line.strip() and not line.startswith('#'))
                new_lines = set(line.strip() for line in new_memory.strip().split('\n') if line.strip() and not line.startswith('#'))
                
                # 计算保留比例
                retention_ratio = len(old_lines & new_lines) / len(old_lines) if old_lines else 1.0
                
                # 🔧 阈值说明：50% 是保守阈值
                # - 如果 LLM 大幅改写但保留核心语义，可能低于 50% 导致手动追加（重复）
                # - 这是保守策略：宁可重复不可丢失
                # - 可根据实际效果调整阈值（如 0.3 或 0.7）
                if retention_ratio > 0.5:
                    logger.debug(f"[记忆更新] LLM 保留了 {retention_ratio*100:.1f}% 的旧记忆，直接写入")
                    self.memory.write_memory(new_memory)
                else:
                    # LLM 未保留旧记忆，手动追加
                    combined = f"{existing_memory.rstrip()}\n\n{new_memory.strip()}\n"
                    logger.warning(f"[记忆更新] LLM 仅保留 {retention_ratio*100:.1f}% 旧记忆，手动追加（旧:{len(existing_memory)}字 + 新:{len(new_memory)}字）")
                    self.memory.write_memory(combined)
            else:
                # 无旧记忆，直接写入
                self.memory.write_memory(new_memory)
        else:
            logger.debug("[记忆更新] 无长期记忆更新")
        if new_user and new_user != "(无需更新)":
            logger.info(f"[记忆更新] 写入用户画像: {len(new_user)} 字")
            self.memory.write_user(new_user)
        else:
            logger.debug("[记忆更新] 无用户画像更新")
        logger.info("[记忆更新] 完成")
