"""结构化日志系统

支持两种格式：
- 文本格式（默认）：人类可读
- JSON 格式：便于 ELK 分析
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any


class StructuredFormatter(logging.Formatter):
    """结构化日志格式化器
    
    支持两种输出模式：
    - text（默认）：人类可读格式
    - json：结构化 JSON，便于日志分析系统
    """
    
    def __init__(self, json_mode: bool = False, include_extra: bool = True):
        """
        Args:
            json_mode: 是否输出 JSON 格式
            include_extra: 是否包含 extra 字段
        """
        self.json_mode = json_mode
        self.include_extra = include_extra
        super().__init__()
    
    def format(self, record: logging.LogRecord) -> str:
        """格式化日志记录
        
        Args:
            record: 日志记录
        
        Returns:
            格式化后的字符串
        """
        # 基础字段
        base: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # 合并 extra 字段
        if self.include_extra and hasattr(record, "extra_data"):
            base.update(record.extra_data)
        
        # 异常信息
        if record.exc_info:
            base["exception"] = self.formatException(record.exc_info)
        
        # JSON 模式
        if self.json_mode:
            return json.dumps(base, ensure_ascii=False)
        
        # 文本模式（人类可读）
        return self._format_text(base, record)
    
    def _format_text(self, data: dict, record: logging.LogRecord) -> str:
        """格式化为人类可读文本
        
        Args:
            data: 日志数据
            record: 日志记录
        
        Returns:
            格式化后的文本
        """
        parts = []
        
        # 时间戳
        parts.append(f"[{data['timestamp']}]")
        
        # 日志级别（带颜色）
        level_color = {
            "DEBUG": "\033[36m",    # 青色
            "INFO": "\033[32m",     # 绿色
            "WARNING": "\033[33m",  # 黄色
            "ERROR": "\033[31m",    # 红色
            "CRITICAL": "\033[35m", # 紫色
        }.get(data["level"], "")
        
        if level_color:
            parts.append(f"{level_color}[{data['level']}]\033[0m")
        else:
            parts.append(f"[{data['level']}]")
        
        # 特殊字段（如 tool_name, event）
        if "tool_name" in data:
            parts.append(f"[{data['tool_name']}]")
        if "event" in data:
            parts.append(f"[{data['event']}]")
        if "teammate" in data:
            parts.append(f"[{data['teammate']}]")
        
        # 消息
        parts.append(data["message"])
        
        # 额外字段（排除已处理的）
        extra_fields = {k: v for k, v in data.items() 
                       if k not in ("timestamp", "level", "logger", "message", 
                                   "tool_name", "event", "teammate", "exception")}
        if extra_fields:
            extra_str = " ".join(f"{k}={v}" for k, v in extra_fields.items())
            parts.append(f"({extra_str})")
        
        # 异常
        if "exception" in data:
            parts.append(f"\n{data['exception']}")
        
        return " ".join(parts)


class ExtraLogger(logging.LoggerAdapter):
    """支持 extra 字段的日志适配器
    
    使用方式：
        logger = ExtraLogger(logging.getLogger("mini_ai"), {})
        logger.info("message", extra={"extra_data": {"key": "value"}})
    """
    
    def process(self, msg: str, kwargs: dict) -> tuple[str, dict]:
        """处理日志消息
        
        Args:
            msg: 原始消息
            kwargs: 关键字参数
        
        Returns:
            (处理后的消息, 处理后的参数)
        """
        # 合并 extra
        if "extra" in kwargs:
            kwargs["extra"] = {**self.extra, **kwargs["extra"]}
        else:
            kwargs["extra"] = self.extra.copy()
        
        return msg, kwargs


def setup_logging(config: dict | None = None) -> logging.Logger:
    """初始化日志系统
    
    Args:
        config: 日志配置，支持：
            - format: "text" 或 "json"（默认 "text"）
            - level: 日志级别（默认 "INFO"）
    
    Returns:
        配置好的 logger
    """
    config = config or {}
    
    json_mode = config.get("format") == "json"
    level = getattr(logging, config.get("level", "INFO").upper())
    
    # 创建 handler
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter(json_mode=json_mode))
    
    # 配置 logger
    logger = logging.getLogger("mini_ai")
    logger.setLevel(level)
    logger.handlers = [handler]
    
    # 减少第三方库的日志
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    
    return logger


# 示例：结构化日志使用
if __name__ == "__main__":
    # 文本模式
    logger = setup_logging({"format": "text", "level": "DEBUG"})
    
    logger.debug("调试信息")
    logger.info("普通信息")
    logger.info("结构化日志", extra={"extra_data": {"event": "tool_call", "tool_name": "read_file"}})
    logger.warning("警告信息", extra={"extra_data": {"user": "alice"}})
    logger.error("错误信息", extra={"extra_data": {"error_code": "E001", "recoverable": True}})
    
    print("\n" + "=" * 60 + "\n")
    
    # JSON 模式
    logger = setup_logging({"format": "json", "level": "DEBUG"})
    
    logger.info("普通信息")
    logger.info("结构化日志", extra={"extra_data": {"event": "tool_call", "tool_name": "read_file"}})
    logger.error("错误信息", extra={"extra_data": {"error_code": "E001"}})
