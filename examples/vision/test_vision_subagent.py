"""测试 vision 子代理 - 使用正确的模型

运行方式:
    cd /Users/yuanzhixiang/yzx_code/yzx_agent
    python examples/vision/test_vision_subagent.py
"""
import sys
from pathlib import Path

# 添加项目根目录到 sys.path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root / "src"))

from mini_ai.config import init_config, MODEL_CONFIG, get_model_config, SUBAGENT_MODELS, RequestContext
from mini_ai.tools.dispatch_subagent import execute, configure as configure_dispatch
from mini_ai.subagents import SubagentLoader

# 加载配置
init_config()

# 打印配置信息
print("=" * 60)
print(f"主模型: {MODEL_CONFIG.get('model', 'unknown')}")
print(f"子代理模型映射: {SUBAGENT_MODELS}")
print("=" * 60)

# 检查 vision 映射的模型
vision_model_name = SUBAGENT_MODELS.get("vision")
if vision_model_name:
    config = get_model_config(vision_model_name)
    print(f"\nvision 子代理使用模型 '{vision_model_name}':")
    print(f"  model: {config.get('model')}")
    print(f"  api_url: {config.get('api_url')[:60]}...")

# 配置 dispatch_subagent
subagents_dir = project_root / "src" / "mini_ai" / "subagents"
loader = SubagentLoader(subagents_dir)
configure_dispatch(loader=loader, project_path=str(project_root))

# 测试图片路径（替换为你的图片路径）
image_path = "/Users/yuanzhixiang/Downloads/665c934f987e3f626a62fa81bad0df79.JPG"

# 调用 vision 子代理
result = execute({
    "type": "vision",
    "task": f"请分析这张图片：{image_path}"
})

print("\n" + "=" * 60)
print("分析结果:")
print("=" * 60)
print(result)
