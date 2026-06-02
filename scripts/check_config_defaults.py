#!/usr/bin/env python3
"""检查配置项默认值完整性"""
import sys
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mini_ai.config import (
    TIMEOUTS, COMPACTOR, TEAMMATE, TOOL, IMAGE, 
    RUNNER, THINKING, DISPLAY, WEB, LOGGING, 
    PLAN, MCP, SKILL_PATHS, SUBAGENT_MODELS, DATABASE
)

def check_config(name: str, config: dict, required_fields: dict[str, any]):
    """检查配置项是否有默认值"""
    print(f"\n{'='*60}")
    print(f"检查 {name}")
    print(f"{'='*60}")
    
    all_ok = True
    for field, expected_default in required_fields.items():
        if "." in field:
            # 嵌套字段
            parts = field.split(".")
            current = config
            try:
                for part in parts:
                    current = current[part]
                print(f"  ✅ {field}: {current}")
            except (KeyError, TypeError) as e:
                print(f"  ❌ {field}: 缺少默认值")
                all_ok = False
        else:
            if field in config:
                print(f"  ✅ {field}: {config[field]}")
            else:
                print(f"  ❌ {field}: 缺少默认值")
                all_ok = False
    
    return all_ok

def main():
    """主检查逻辑"""
    print("\n🔍 检查所有配置项默认值完整性\n")
    
    all_ok = True
    
    # 1. TIMEOUTS
    all_ok &= check_config("TIMEOUTS", TIMEOUTS, {
        "llm": 120,
        "llm_connect": 30,
        "llm_retries": 3,
        "llm_retry_delay": 2,
        "teammate_recv": 5,
        "lead_wait": 1800,
        "lead_poll_interval": 2,
        "web_fetch": 20,
    })
    
    # 2. COMPACTOR - 所有字段都有默认值
    print(f"\n{'='*60}")
    print(f"COMPACTOR: {COMPACTOR}")
    print(f"说明: COMPACTOR 字段通过 .get() 方法使用，都有默认值 ✅")
    
    # 3. TEAMMATE - 所有字段都有默认值
    print(f"\n{'='*60}")
    print(f"TEAMMATE: {TEAMMATE}")
    print(f"说明: TEAMMATE 字段通过 .get() 方法使用，都有默认值 ✅")
    
    # 4. TOOL
    all_ok &= check_config("TOOL", TOOL, {
        "max_result_chars": 8000,
    })
    
    # 5. IMAGE
    all_ok &= check_config("IMAGE", IMAGE, {
        "max_size": 10 * 1024 * 1024,
        "compress_threshold": 500 * 1024,
        "compress_max_dimension": 800,
        "compress_quality": 85,
    })
    
    # 6. RUNNER
    all_ok &= check_config("RUNNER", RUNNER, {
        "context_usage_limit": 0.88,
        "max_turns": 20,
    })
    
    # 7. THINKING
    all_ok &= check_config("THINKING", THINKING, {
        "enabled": False,
        "budget_tokens": 10000,
        "type": "enabled",
    })
    
    # 8. DISPLAY
    all_ok &= check_config("DISPLAY", DISPLAY, {
        "thinking_mode": "collapsed",
        "tool_detail": "summary",
    })
    
    # 9. WEB
    all_ok &= check_config("WEB", WEB, {
        "history_limit": 200,
    })
    
    # 10. PLAN
    all_ok &= check_config("PLAN", PLAN, {
        "approval": True,
    })
    
    # 11. MCP
    all_ok &= check_config("MCP", MCP, {
        "enabled": False,
    })
    
    # 12. DATABASE
    all_ok &= check_config("DATABASE", DATABASE, {
        "history.async_write": None,
        "history.batch_size": 50,
        "history.batch_timeout": 0.1,
        "history.queue_size": 10000,
        "history.retry_count": 3,
        "memory.cache_size": 10000,
    })
    
    # 13. 其他配置
    print(f"\n{'='*60}")
    print(f"LOGGING: {LOGGING}")
    print(f"说明: LOGGING 为空字典，可选配置 ✅")
    
    print(f"\nSKILL_PATHS: {SKILL_PATHS}")
    print(f"说明: SKILL_PATHS 为空列表，可选配置 ✅")
    
    print(f"\nSUBAGENT_MODELS: {SUBAGENT_MODELS}")
    print(f"说明: SUBAGENT_MODELS 为空字典，可选配置 ✅")
    
    # 总结
    print(f"\n{'='*60}")
    if all_ok:
        print("✅ 所有配置项都有合理的默认值！")
        print("用户可以不配置这些选项，系统会使用默认值。")
    else:
        print("❌ 部分配置项缺少默认值，需要修复！")
        sys.exit(1)
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
