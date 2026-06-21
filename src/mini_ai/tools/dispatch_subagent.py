"""子代理调度工具 — 支持 inputs 参数链式传递结果，支持图片路径自动处理"""
from ..core.runtime_types import ToolArgs, ToolDefinition
import base64
import copy
import io
import re
import threading
from pathlib import Path

from ..core.settings import ImageSettings, ModelSettings
from ..logger import logger
from ..utils import now_ts


# 支持的图片格式
IMAGE_FORMATS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

def get_project_path(project_path: str | None = None) -> str:
    return project_path or ""

def _is_image_path(path_str: str) -> bool:
    """检查是否是图片文件路径"""
    if not path_str:
        return False
    path = Path(path_str)
    return path.suffix.lower() in IMAGE_FORMATS and path.exists() and path.is_file()

def _load_image_as_data_url(path_or_url: str, image_settings: ImageSettings | None = None) -> str | None:
    """将图片文件或 URL 转换为 data URL 格式（自动压缩大图）
    
    Args:
        path_or_url: 图片文件路径或 URL
        
    Returns:
        data URL 格式字符串，失败返回 None
    """
    settings = image_settings or ImageSettings()

    # 检测是否是 URL
    if path_or_url.startswith('http://') or path_or_url.startswith('https://'):
        return _download_image_as_data_url(path_or_url, settings)
    
    # 本地文件处理
    try:
        path = Path(path_or_url)
        
        # 检查文件格式
        suffix = path.suffix.lower()
        if suffix not in IMAGE_FORMATS:
            logger.warning(f"[dispatch_subagent] 不支持的图片格式: {suffix}")
            return None
        
        mime_type = IMAGE_FORMATS[suffix]
        file_size = path.stat().st_size
        
        # 读取原图
        with open(path, "rb") as f:
            image_data = f.read()
        
        original_size = len(image_data)
        
        # 如果图片大于阈值，自动压缩
        if original_size > settings.compress_threshold:
            try:
                from PIL import Image
                
                img = Image.open(path)
                
                # 缩放到最大尺寸
                img.thumbnail((settings.compress_max_dimension, settings.compress_max_dimension))
                
                # 如果有透明通道，转换为 RGB
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')
                
                # 转换为 JPEG 格式
                buffer = io.BytesIO()
                img.save(buffer, format='JPEG', quality=settings.compress_quality)
                image_data = buffer.getvalue()
                mime_type = 'image/jpeg'  # 压缩后统一为 JPEG
                
                compressed_size = len(image_data)
                logger.info(
                    f"[dispatch_subagent] 图片压缩: {path.name} "
                    f"{original_size / 1024 / 1024:.1f}MB → {compressed_size / 1024:.1f}KB"
                )
            except ImportError:
                logger.warning("[dispatch_subagent] PIL 未安装，跳过压缩，使用原图")
            except Exception as e:
                logger.warning(f"[dispatch_subagent] 图片压缩失败，使用原图: {e}")
        
        b64_data = base64.b64encode(image_data).decode("utf-8")
        return f"data:{mime_type};base64,{b64_data}"
        
    except FileNotFoundError:
        logger.warning(f"[dispatch_subagent] 图片文件不存在: {path_or_url}")
        return None
    except Exception as e:
        logger.warning(f"[dispatch_subagent] 图片加载失败: {path_or_url}, {e}")
        return None


def _download_image_as_data_url(url: str, image_settings: ImageSettings | None = None) -> str | None:
    """下载网络图片并转换为 data URL
    
    Args:
        url: 图片 URL
        
    Returns:
        data URL 格式字符串，失败返回 None
    """
    import tempfile
    import time
    from urllib.parse import urlparse
    import requests
    
    try:
        # 从 URL 推断扩展名
        parsed_url = urlparse(url)
        path_lower = parsed_url.path.lower()
        ext = None
        for fmt in IMAGE_FORMATS:
            if path_lower.endswith(fmt):
                ext = fmt
                break
        
        if not ext:
            # 默认使用 .jpg
            ext = '.jpg'
        
        # 生成本地临时文件路径
        timestamp = int(time.time() * 1000)
        tmp_path = Path(tempfile.gettempdir()) / f"dispatch_img_{timestamp}{ext}"
        
        # 下载图片（使用 requests 替代 subprocess）
        logger.info(f"[dispatch_subagent] 下载图片: {url[:60]}...")
        
        response = requests.get(url, timeout=30, stream=True)
        if response.status_code != 200:
            logger.warning(f"[dispatch_subagent] 图片下载失败: {url} (status={response.status_code})")
            return None
        
        # 写入临时文件
        with open(tmp_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        # 递归调用 _load_image_as_data_url 处理本地文件
        data_url = _load_image_as_data_url(str(tmp_path), image_settings)
        
        # 清理临时文件
        try:
            tmp_path.unlink()
        except Exception:
            pass
        
        return data_url
        
    except requests.RequestException as e:
        logger.warning(f"[dispatch_subagent] 图片下载失败: {url}, {e}")
        return None
    except Exception as e:
        logger.warning(f"[dispatch_subagent] 图片下载失败: {url}, {e}")
        return None

def _build_image_message(image_paths: list[str], task: str, image_settings: ImageSettings | None = None) -> dict:
    """构建包含图片的消息（兼容 OpenAI 和 Anthropic 格式）"""
    content = []
    
    # 添加文字描述
    content.append({
        "type": "text",
        "text": task
    })
    
    # 添加图片
    for img_path in image_paths:
        data_url = _load_image_as_data_url(img_path, image_settings)
        if data_url:
            content.append({
                "type": "image_url",
                "image_url": {"url": data_url}
            })
    
    return {
        "role": "user",
        "content": content
    }

def _extract_image_paths(text: str, project_path: str = "") -> list[str]:
    """从文本中提取有效的图片文件路径和 URL
    
    Args:
        text: 包含可能图片路径/URL 的文本
        
    Returns:
        有效图片路径/URL 列表
    """
    image_paths = []
    seen = set()  # 避免重复
    
    # 1. 提取网络图片 URL（优先处理）
    # 匹配两种模式：
    # - 标准扩展名：https?://...[.png/.jpg/.jpeg/.gif/.webp/.bmp][?参数]
    # - 参数标识：https?://...&f=PNG/JPG... 或 fm=PNG/JPG...
    url_patterns = [
        r'https?://[^\s\'"<>]+?\.(?:png|jpg|jpeg|gif|webp|bmp)(?:\?[^\s\'"<>]*)?',
        r'https?://[^\s\'"<>]+?(?:\&|\?)[fg]=?(?:png|jpg|jpeg|gif|webp|bmp)[^\s\'"<>]*',
    ]
    
    for pattern in url_patterns:
        url_matches = re.findall(pattern, text, re.IGNORECASE)
        for url in url_matches:
            if url.lower() in seen:
                continue
            seen.add(url.lower())
            image_paths.append(url)
    
    # 2. 提取本地文件路径
    # 排除 URL（以 http:// 或 https:// 开头的）
    path_pattern = r'(?<!["\'])(?!https?://)([/\w\-\.]+\.(?:png|jpg|jpeg|gif|webp|bmp))(?!["\'])'
    path_matches = re.findall(path_pattern, text, re.IGNORECASE)
    
    for match in path_matches:
        if match.lower() in seen:
            continue
        seen.add(match.lower())
        
        path = Path(match)
        
        # 处理相对路径
        if not path.is_absolute():
            if project_path:
                path = Path(project_path) / match
        
        # 验证文件存在且是图片
        if path.exists() and path.is_file() and path.suffix.lower() in IMAGE_FORMATS:
            image_paths.append(str(path))
    
    return image_paths

_BASE_DEFINITION: ToolDefinition = {
    "type": "function",
    "function": {
        "name": "dispatch_subagent",
        "description": (
            "派遣子代理执行独立任务。子代理有独立的工具白名单。\n"
            "可用类型：\n{subagent_list}"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "description": "子代理类型"},
                "task": {"type": "string", "description": "任务描述"},
                "inputs": {
                    "type": "object",
                    "description": "可选：前置结果引用，在 task 中用 {key} 引用",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["type", "task"],
        },
    },
}

definition = copy.deepcopy(_BASE_DEFINITION)

def build_definition(subagent_list: str) -> ToolDefinition:
    d = copy.deepcopy(_BASE_DEFINITION)
    d["function"]["description"] = d["function"]["description"].format(
        subagent_list=subagent_list
    )
    return d

def execute_with_context(
    loader,
    args: ToolArgs,
    *,
    project_path: str = "",
    display=None,
    registry=None,
    abort_event: threading.Event | None = None,
    compactor=None,
    settings=None,
) -> str:
    from ..runner import run_agent
    from ..config import get_model_config
    from ..core.runtime_factory import build_child_request_context
    from ..core.settings import SettingsSnapshot

    subagent_type = args.get("type", "")
    spec = loader.get(subagent_type) if loader else None
    if not spec:
        names = ", ".join(getattr(loader, "specs", {}).keys())
        return f"未知子代理类型 '{args['type']}'，可用：{names}"

    task = args.get("task", "")
    inputs = args.get("inputs") or {}

    if inputs:
        for key, value in inputs.items():
            placeholder = "{" + key + "}"
            if placeholder in task:
                task = task.replace(placeholder, value)

    logger.info(f"[派遣→] {spec['name']}: {task[:200]}")
    if inputs:
        logger.info(f"[派遣→] inputs: {list(inputs.keys())}")

    system_prompt = spec["system_prompt"]
    if project_path:
        system_prompt += f"\n\n## 当前工作空间\n\n项目路径: {project_path}\n\n重要：执行命令时必须传 cwd=\"{project_path}\" 参数；搜索文件时使用绝对路径基于此目录。读写文件使用绝对路径。"

    image_settings = getattr(settings, "image", None)

    # 检测并处理图片路径
    image_paths = _extract_image_paths(task, project_path=project_path)
    if image_paths:
        logger.info(f"[派遣→] 检测到图片: {[Path(p).name for p in image_paths]}")

    _ts = now_ts()
    messages = [
        {"role": "system", "content": system_prompt},
    ]
    
    # 根据是否有图片构建不同的消息格式
    if image_paths:
        messages.append(_build_image_message(image_paths, task, image_settings))
    else:
        messages.append({"role": "user", "content": task, "timestamp": _ts})

    sub_display = None
    lead_display = display
    if lead_display:
        try:
            sub_display = lead_display.child(teammate=f"sub:{spec['name']}")
            sub_display.agent_start(
                agent_type=f"sub:{spec['name']}",
                task=task[:100] + "..." if len(task) > 100 else task,
                max_turns=spec.get("max_turns", 10),
            )
        except Exception as exc:
            logger.debug(f"[dispatch_subagent] 创建子 display 失败: {exc}")
            sub_display = None

    # 获取子代理专属模型配置；默认继承当前 session 的不可变运行时快照。
    runtime_settings = settings if isinstance(settings, SettingsSnapshot) else SettingsSnapshot(model=ModelSettings.from_dict(None))
    base_model_settings = runtime_settings.model
    model_config = base_model_settings.to_dict()
    model_name = runtime_settings.subagent_models.get(subagent_type)
    if model_name:
        custom_config = get_model_config(str(model_name))
        if custom_config:
            model_config = custom_config
            logger.info(f"[派遣→] {spec['name']} 使用模型: {model_name}")
        else:
            logger.warning(f"[派遣→] 模型 '{model_name}' 未找到，继承当前会话模型")

    ctx = build_child_request_context(runtime_settings, model_config=model_config, display=sub_display)

    try:
        result = run_agent(
            messages,
            max_turns=spec["max_turns"],
            tool_names=spec["tool_names"],
            ctx=ctx,
            abort_event=abort_event,
            context_length=int(model_config.get("context_length") or base_model_settings.context_length),
            compactor=compactor,
            tool_registry=registry,
        )
        logger.debug(f"[派遣←] {spec['name']}: {result or 'None'}")
        return result or f"[{spec['name']}] 超出轮次限制或执行失败"
    except Exception as e:
        logger.error(f"[派遣✗] {spec['name']} 异常: {e}", exc_info=True)
        return f"⚠ 子代理 [{spec['name']}] 执行失败: {type(e).__name__}: {e}"


def execute(args: ToolArgs, abort_event: threading.Event | None = None) -> str:
    return "Error: dispatch_subagent 需要 session-local SubagentLoader"
