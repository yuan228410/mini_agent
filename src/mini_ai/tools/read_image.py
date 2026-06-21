"""图片读取工具 — 读取本地图片并转换为 base64（自动压缩大图）"""
from ..core.runtime_types import ToolArgs, ToolDefinition
import base64
import io
from pathlib import Path

from ..core.settings import ImageSettings
from ..logger import logger

definition: ToolDefinition = {
    "type": "function",
    "function": {
        "name": "read_image",
        "description": "读取图片转 base64。支持 PNG/JPEG/GIF/WebP/BMP，大图（>500KB）自动压缩，最大 10MB。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "图片路径"},
            },
            "required": ["path"],
        },
    },
}

# 支持的图片格式
SUPPORTED_FORMATS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def execute_with_settings(args: ToolArgs, image_settings: ImageSettings | None = None) -> str:
    """读取图片并转换为 data URL 格式
    
    Args:
        args: {"path": "图片路径"}
        
    Returns:
        data URL 格式字符串或错误信息
    """
    path_str = args.get("path", "")
    if not path_str or not isinstance(path_str, str):
        return "⚠ read_image 缺少 path 参数，请提供图片文件路径"
    
    path = Path(path_str)
    
    # 检查文件存在
    if not path.exists():
        return f"⚠ 图片文件不存在: {path}"
    
    if not path.is_file():
        return f"⚠ 不是文件: {path}"
    
    # 检查文件格式
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_FORMATS:
        supported = ", ".join(SUPPORTED_FORMATS.keys())
        return f"⚠ 不支持的图片格式: {suffix}，支持: {supported}"
    
    settings = image_settings or ImageSettings()
    max_size = settings.max_size
    compress_threshold = settings.compress_threshold
    compress_max_dim = settings.compress_max_dimension
    compress_quality = settings.compress_quality
    
    # 检查文件大小
    file_size = path.stat().st_size
    if file_size > max_size:
        max_mb = max_size // 1024 // 1024
        return f"⚠ 图片文件过大 ({file_size / 1024 / 1024:.1f}MB)，最大支持 {max_mb}MB"
    
    try:
        with open(path, "rb") as f:
            image_data = f.read()
        
        mime_type = SUPPORTED_FORMATS[suffix]
        original_size = file_size
        
        # 大图自动压缩
        if file_size > compress_threshold:
            try:
                from PIL import Image
                
                img = Image.open(path)
                img.thumbnail((compress_max_dim, compress_max_dim))
                
                # 透明通道转 RGB
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')
                
                buffer = io.BytesIO()
                img.save(buffer, format='JPEG', quality=compress_quality)
                image_data = buffer.getvalue()
                mime_type = 'image/jpeg'
                
                compressed_size = len(image_data)
                logger.info(
                    f"[read_image] {path.name} 压缩: "
                    f"{original_size / 1024 / 1024:.1f}MB → {compressed_size / 1024:.1f}KB"
                )
            except ImportError:
                logger.warning("[read_image] PIL 未安装，跳过压缩，使用原图")
            except Exception as e:
                logger.warning(f"[read_image] 压缩失败，使用原图: {e}")
        else:
            logger.info(f"[read_image] {path.name} ({file_size / 1024:.1f}KB)")
        
        # 转换为 data URL
        b64_data = base64.b64encode(image_data).decode("utf-8")
        return f"data:{mime_type};base64,{b64_data}"
        
    except PermissionError:
        return f"⚠ 无权限读取文件: {path}"
    except Exception as e:
        logger.error(f"[read_image] 读取失败: {e}", exc_info=True)
        return f"⚠ 读取图片失败: {type(e).__name__}: {e}"


def execute(args: ToolArgs) -> str:
    return execute_with_settings(args)
