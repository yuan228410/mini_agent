"""工具循环主逻辑

精简版工具循环，职责：
- 协调 state、executor、error_handler
- 管理循环生命周期
"""
import threading
from typing import Any, Callable

from .state import LoopState
from ..core.display_protocol import DisplayProtocol
from .executor import ToolExecutor
from .error_handler import ErrorHandler

# 相对导入父模块
from ..logger import logger
from ..config import RUNNER
from ..utils import now_ts
from ..llm.base import estimate_messages_tokens

_CONTEXT_USAGE_LIMIT = RUNNER.get("context_usage_limit", 0.88)
MAX_OVERFLOW_RETRIES = 3

def run_tool_loop(
    messages: list[dict],
    tools: list[dict],
    *,
    streaming: bool = False,
    display: DisplayProtocol | None = None,
    inject_fn=None,
    persist_fn=None,
    abort_event: threading.Event | None = None,
    max_turns: int = 0,
    context_length: int | None = None,
    context_usage_limit: float = _CONTEXT_USAGE_LIMIT,
    ctx=None,
    bus=None,
    compactor=None,
    tool_registry=None,
) -> tuple[dict | None, bool]:
    """统一工具循环
    
    Args:
        messages: 消息列表
        tools: 工具定义
        streaming: 是否流式输出
        display: 显示回调
        inject_fn: 消息注入函数
        persist_fn: 消息持久化函数
        abort_event: 中断事件
        max_turns: 最大轮次
        context_length: 上下文长度
        context_usage_limit: 上下文使用率阈值
        ctx: 请求上下文
        bus: 消息总线
        compactor: Compactor 实例（用于上下文溢出恢复和日常压缩）
    
    Returns:
        (final_msg, spawned_teammate)
    """
    # 初始化状态
    if max_turns <= 0:
        max_turns = RUNNER.get("max_turns", 20)
    
    state = LoopState(max_turns=max_turns)
    state.messages = messages
    
    # 初始化执行器和错误处理器
    executor = ToolExecutor(display=display, persist_fn=persist_fn, streaming=streaming)
    error_handler = ErrorHandler()
    
    # 主循环
    while state.should_continue():
        # 检查中断
        if abort_event and abort_event.is_set():
            # 🔧 修复：中断时保存已生成的内容
            if display:
                partial_content = display.text_end()  # 获取已流式输出的内容
                if partial_content:
                    partial_msg = {
                        "role": "assistant",
                        "content": partial_content,
                        "timestamp": now_ts(),
                    }
                    messages.append(partial_msg)
                    if executor.persist_fn:
                        executor.persist_fn(partial_msg)
                    logger.info(f"[runner] 中断时保存部分内容: {len(partial_content)} 字符")
            else:
                if display:
                    display.text_end()
            return None, state.spawned_teammate
        
        state.increment_turn()
        
        try:
            # 调用 LLM
            msg = executor.call_llm(messages, tools, ctx, abort_event)
            
            # 检查是否需要中断
            if abort_event and abort_event.is_set():
                # 🔧 修复：中断时保存已生成的内容
                if display:
                    partial_content = display.text_end()  # 获取已流式输出的内容
                    if partial_content:
                        partial_msg = {
                            "role": "assistant",
                            "content": partial_content,
                            "timestamp": now_ts(),
                        }
                        messages.append(partial_msg)
                        if executor.persist_fn:
                            executor.persist_fn(partial_msg)
                        logger.info(f"[runner] 中断时保存部分内容: {len(partial_content)} 字符")
                else:
                    if display:
                        display.text_end()
                return None, state.spawned_teammate
            
            # 无工具调用，返回最终响应
            if not msg or "tool_calls" not in msg:
                # 🔧 修复：如果是中断的消息，保存部分内容
                if msg and msg.get("interrupted"):
                    if msg.get("content"):
                        messages.append(msg)
                        if executor.persist_fn:
                            executor.persist_fn(msg)
                        logger.info(f"[runner] 中断时保存部分内容: {len(msg['content'])} 字符")
                    return None, state.spawned_teammate
                
                # 🔧 修复：如果是错误消息，检查是否为上下文溢出
                if msg and msg.get("error"):
                    # 上下文溢出：尝试 force_compact 恢复
                    if msg.get("is_context_overflow"):
                        logger.warning(f"[runner] 流式路径检测到上下文溢出: {msg.get('error', '')[:100]}")
                    if msg.get("is_context_overflow") and compactor:
                        if _recover_from_overflow(messages, compactor, ctx, state):
                            continue
                        logger.error("[runner] 上下文溢出恢复失败")
                    logger.error(f"[runner] LLM 返回错误: {msg.get('error')}")
                    return msg, state.spawned_teammate
                
                executor.finalize_response(msg, messages)
                return msg, state.spawned_teammate
            
            # 执行工具（优先使用会话级 ToolRegistry）
            if tool_registry is not None:
                tool_spawned = tool_registry.handle_tool_calls(msg, messages, display=display, persist_fn=executor.persist_fn)
            else:
                from ..tools import handle_tool_calls
                tool_spawned = handle_tool_calls(msg, messages, display=display, persist_fn=executor.persist_fn)
            
            # 检查工具错误
            # 注意：这里检测的是 execute_tools 内部处理的字符串格式错误（⚠ 开头）
            # 与 error_handler.py 的 ToolError 异常路径不同，二者不会重复触发
            if _has_recent_tool_error(messages):
                state.record_error()
                if state.consecutive_errors >= RUNNER.get("max_consecutive_errors", 3):
                    logger.warning(f"[runner] 连续 {state.consecutive_errors} 次工具错误，提前退出")
                    return _force_summary(messages, ctx, display, bus, state)
            else:
                state.clear_errors()
            
            if tool_spawned:
                state.mark_spawned()
            
            # 注入额外消息
            if inject_fn:
                inject_fn(messages)
            
            # 检查队友回禀
            if bus:
                _inject_teammate_reports(bus, messages)
            
            # 上下文压缩检查（提前预警）- 每 5 轮检查一次，减少性能开销
            if context_length and state.turn % 5 == 0:
                estimated_tokens = estimate_messages_tokens(messages)
                threshold = int(context_length * context_usage_limit)
                warning_threshold = int(threshold * 0.9)  # 90% 时预警
                
                if estimated_tokens > warning_threshold:
                    logger.warning(f"[runner] 上下文接近阈值: {estimated_tokens}/{threshold} tokens ({estimated_tokens/threshold*100:.1f}%)")
                
                if _check_context_usage(messages, context_length, context_usage_limit, ctx, compactor):
                    # 上下文超限（压缩后仍超限才走这条路径）
                    logger.info("[runner] 上下文超限，退出循环")
                    if display:
                        display.text_end()
                    return None, state.spawned_teammate
        
        except Exception as e:
            # 上下文溢出：尝试 force_compact 恢复后重试
            from ..exceptions import LLMError
            if isinstance(e, LLMError) and getattr(e, 'is_context_overflow', False):
                logger.warning(f"[runner] 非流式路径检测到上下文溢出: {str(e)[:100]}")
                if compactor and _recover_from_overflow(messages, compactor, ctx, state):
                    continue
                logger.error("[runner] 上下文溢出恢复失败")
                if display:
                    display.text_end()
                return {"role": "assistant", "content": "⚠ 上下文超限，压缩恢复失败，请缩减对话或新建会话"}, state.spawned_teammate
            
            user_msg = error_handler.handle(e, state)
            if user_msg:
                messages.append(user_msg)
            
            if error_handler.should_terminate(e):
                return None, state.spawned_teammate
    

            # 非终止性异常（如 429 限流），继续循环让 LLM 重试
            continue
    # 达到最大轮次
    return _force_summary(messages, ctx, display, bus, state)

def _has_recent_tool_error(messages: list[dict]) -> bool:
    """检查最近是否有工具错误（匹配明确的错误模式，避免误判正常工具结果中的 ⚠ 符号）"""
    tool_msgs = [m for m in messages[-5:] if m.get("role") == "tool"]
    if not tool_msgs:
        return False
    
    # 检查最后一条工具消息是否为错误
    last_msg = tool_msgs[-1].get("content", "")
    if last_msg.startswith("Error:"):
        return True
    # ⚠ 后跟错误关键词（失败/错误/不存在/无权限/不支持/缺少）
    if last_msg.startswith("⚠ ") and any(kw in last_msg[:20] for kw in ("失败", "错误", "不存在", "无权限", "不支持", "缺少")):
        return True
    return False

def _force_summary(messages: list[dict], ctx, display, bus, state: LoopState) -> tuple[dict | None, bool]:
    """强制生成总结

    在达到 max_turns 或连续错误过多时调用，
    尝试让 LLM 生成一个总结性回复。
    """
    from ..llm import chat as llm_chat

    # 记录最近的工具调用摘要（用于排查循环失控）
    tool_calls_summary = []
    for m in messages[-20:]:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                tool_calls_summary.append(f"{fn.get('name', '?')}({fn.get('arguments', '')[:50]}...)")
        elif m.get("role") == "tool":
            content = m.get("content", "")
            tool_calls_summary.append(f"→ {content[:100]}...")
    
    if tool_calls_summary:
        logger.info(f"[runner] 最近工具调用摘要: {tool_calls_summary[-10:]}")
    
    # 注入队友回禀（在生成总结前）
    if bus:
        _inject_teammate_reports(bus, messages, with_instruction=True)
    
    timestamp = now_ts()
    
    messages.append({
        "role": "user",
        "content": "⚠ 已达到最大轮次或连续错误过多，请总结当前进度并向用户说明。",
        "timestamp": timestamp
    })
    
    try:
        final = llm_chat(messages, tools=None, ctx=ctx)
        if final and final.get("content"):
            if display:
                display.text_chunk(final["content"])
                display.text_end(final["content"])
            return final, state.spawned_teammate
        else:
            logger.warning("[runner] 强制总结返回空内容")
    except Exception as e:
        logger.error(f"[runner] 强制总结失败: {e}", exc_info=True)
    
    # LLM 调用失败或返回空，确保 display 结束
    if display:
        display.text_end()
    
    return None, state.spawned_teammate

def _inject_teammate_reports(bus, messages: list[dict], with_instruction: bool = False) -> None:
    """注入队友回禀
    
    Args:
        bus: 消息总线
        messages: 消息列表
        with_instruction: 是否追加回复指引
    """
    from ..team.loop import format_inbox_messages
    
    inbox = bus.read_inbox("lead")
    if inbox:
        inbox_text = format_inbox_messages(inbox)
        messages.append({
            "role": "user",
            "content": inbox_text,
            "timestamp": now_ts()
        })
        
        # 追加回复指引
        if with_instruction:
            messages.append({
                "role": "user", 
                "content": "队友回禀已收到。请先 blackboard_read 获取队友写入黑板的结果，再基于回禀和黑板内容回复用户。",
                "timestamp": now_ts()
            })
        
        logger.info("[runner] 注入队友回禀")

def _check_context_usage(messages: list[dict], context_length: int, limit: float, ctx, compactor=None) -> bool:
    """检查上下文使用率，超限时触发裁剪+压缩
    
    Returns:
        True 表示上下文超限且压缩后仍超限，需要退出循环
        False 表示未超限或压缩成功，继续循环
    """
    estimated = estimate_messages_tokens(messages)
    threshold = int(context_length * limit)
    
    if estimated <= threshold:
        return False
    
    logger.info(f"[runner/compact] 上下文使用 {estimated} > {threshold} tokens，触发压缩")
    
    # 先裁剪工具结果（零开销），裁剪后可能已够用
    # ContextPruner.prune 返回新列表，不修改原 messages
    from ..memory.context_pruner import ContextPruner, PruneOptions
    pruned = ContextPruner.prune(messages, PruneOptions())
    pruned_tokens = estimate_messages_tokens(pruned)
    
    if pruned_tokens < threshold:
        messages[:] = pruned
        logger.info(f"[runner/compact] 裁剪后 tokens={pruned_tokens}，已低于阈值，跳过压缩")
        return False
    
    # 需要压缩
    if compactor is None:
        logger.warning("[runner/compact] 无 compactor，无法压缩，退出循环")
        return True
    
    from ..llm import chat as llm_chat
    try:
        new_messages = compactor.compact(llm_chat, pruned, ctx=ctx)
        messages[:] = new_messages
        after_tokens = estimate_messages_tokens(messages)
        logger.info(f"[runner/compact] 压缩完成: tokens {estimated} -> {after_tokens}")
        if after_tokens <= threshold:
            return False
        # compact 后仍超限，尝试 force_compact 渐进恢复
        logger.warning("[runner/compact] compact 后仍超限，尝试 force_compact")
        if compactor.force_compact(llm_chat, messages, ctx):
            # force_compact 成功 = tokens < 安全线(0.7*context_length)，无需再 estimate
            logger.info(f"[runner/compact] force_compact 恢复成功")
            return False
        return True
    except Exception as e:
        logger.error(f"[runner/compact] 压缩异常: {e}", exc_info=True)
        return True


def _recover_from_overflow(messages: list[dict], compactor, ctx, state: LoopState) -> bool:
    """上下文溢出恢复：force_compact 后可重试
    
    Returns:
        True = 恢复成功，可 continue 重试
        False = 恢复失败
    """
    if state.overflow_retries >= MAX_OVERFLOW_RETRIES:
        logger.warning(f"[runner] 上下文溢出恢复已达上限 {MAX_OVERFLOW_RETRIES} 次")
        return False
    
    state.overflow_retries += 1
    logger.info(f"[runner] 上下文溢出恢复 {state.overflow_retries}/{MAX_OVERFLOW_RETRIES}")
    
    from ..llm import chat as llm_chat
    try:
        ok = compactor.force_compact(llm_chat, messages, ctx)
        if ok:
            logger.info(f"[runner] 上下文溢出恢复成功: messages={len(messages)}, tokens={estimate_messages_tokens(messages)}")
            return True
        logger.warning("[runner] force_compact 所有级别耗尽")
        return False
    except Exception as e:
        logger.error(f"[runner] force_compact 异常: {e}", exc_info=True)
        return False

def run_agent(messages: list[dict], max_turns: int = 10, ctx=None, bus=None, abort_event: threading.Event | None = None, tool_names: list[str] | None = None, context_length: int = 128000, compactor=None, tool_registry=None) -> str | None:
    """轻量 agent 循环
    
    Args:
        messages: 消息列表
        max_turns: 最大轮次（默认 10，适用于子代理/队友）
        ctx: 请求上下文
        bus: 消息总线
        abort_event: 中断事件
        tool_names: 工具白名单（None 表示全部工具）
        context_length: 上下文长度限制（默认 128k）
        compactor: Compactor 实例

    Returns:
        最终响应文本
    """
    from ..tools import get_definitions

    try:
        definitions = tool_registry.get_definitions() if tool_registry is not None else get_definitions()
        if tool_names:
            # 根据工具名过滤
            tools = [d for d in definitions if d.get("function", {}).get("name") in tool_names]
        else:
            tools = definitions
        msg, _ = run_tool_loop(messages, tools, max_turns=max_turns, ctx=ctx, bus=bus, abort_event=abort_event, context_length=context_length, compactor=compactor, tool_registry=tool_registry)
    except Exception as e:
        logger.error(f"[run_agent] 异常: {e}", exc_info=True)
        # 将异常转换为错误消息返回，让调用方知道发生了什么
        return f"⚠ Agent 执行失败: {type(e).__name__}: {e}"
    
    if msg and msg.get("content"):
        return msg["content"]
    
    # 尝试从消息历史中找最后一条 assistant 消息
    for m in reversed(messages):
        if m.get("role") == "assistant" and m.get("content"):
            return m["content"]
    
    logger.warning("[run_agent] 未获取到有效响应，返回 None")
    return None
