"""FastAPI 应用入口"""
import asyncio
import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .routes import chat, models, skills, config, commands, workspaces, files, team
from .deps import init_components, shutdown_mcp
from ..logger import logger

# 资源监控配置
MONITOR_INTERVAL = 60  # 监控间隔（秒）
FILE_DESCRIPTOR_WARNING_THRESHOLD = 150  # 告警阈值
FILE_DESCRIPTOR_CRITICAL_THRESHOLD = 200  # 严重告警阈值

async def _monitor_resources():
    """后台任务：定期监控资源使用情况"""
    while True:
        try:
            await asyncio.sleep(MONITOR_INTERVAL)
            
            # 获取当前进程打开的文件描述符数量
            try:
                import resource
                soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
                
                # 尝试获取当前打开的文件数（仅 Unix）
                try:
                    pid = os.getpid()
                    fd_dir = f"/proc/{pid}/fd"
                    if os.path.exists(fd_dir):
                        open_fds = len(os.listdir(fd_dir))
                    else:
                        # macOS 使用 lsof
                        import subprocess
                        result = subprocess.run(['lsof', '-p', str(pid)], capture_output=True, text=True, timeout=5)
                        open_fds = len(result.stdout.strip().split('\n')) - 1 if result.stdout else 0
                except Exception:
                    open_fds = -1  # 无法获取
                
                # 记录日志
                logger.debug(f"[资源监控] 文件描述符: {open_fds}/{soft} (硬限制: {hard})")
                
                # 告警
                if open_fds > FILE_DESCRIPTOR_CRITICAL_THRESHOLD:
                    logger.error(f"[资源监控] ⚠️ 严重告警：打开文件数 {open_fds} 超过临界阈值 {FILE_DESCRIPTOR_CRITICAL_THRESHOLD}")
                elif open_fds > FILE_DESCRIPTOR_WARNING_THRESHOLD:
                    logger.warning(f"[资源监控] ⚠️ 告警：打开文件数 {open_fds} 超过警告阈值 {FILE_DESCRIPTOR_WARNING_THRESHOLD}")
                
                # 会话缓存统计
                from .routes.chat import _SESSION_ACCESS, _SESSION_COMPONENTS
                logger.debug(f"[资源监控] 会话缓存: {len(_SESSION_ACCESS)} 个, 组件缓存: {len(_SESSION_COMPONENTS)} 个")
                
            except Exception as e:
                logger.debug(f"[资源监控] 获取资源信息失败: {e}")
                
        except asyncio.CancelledError:
            logger.info("[资源监控] 监控任务已停止")
            break
        except Exception as e:
            logger.warning(f"[资源监控] 监控异常: {e}")


_monitor_task = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _monitor_task
    init_components()
    
    # 启动资源监控
    _monitor_task = asyncio.create_task(_monitor_resources())
    logger.info("[资源监控] 已启动资源监控任务")
    
    yield
    
    # 停止监控
    if _monitor_task:
        _monitor_task.cancel()
        try:
            await _monitor_task
        except asyncio.CancelledError:
            pass
    
    # 关闭历史数据库连接池
    from ..memory.history_db import HistoryDBPool
    HistoryDBPool.close_all()
    
    shutdown_mcp()

def create_app() -> FastAPI:
    app = FastAPI(title="mini_ai", docs_url=None, redoc_url=None, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(chat.router, prefix="/api")
    app.include_router(models.router, prefix="/api")
    app.include_router(skills.router, prefix="/api")
    app.include_router(commands.router, prefix="/api")
    app.include_router(config.router, prefix="/api")
    app.include_router(workspaces.router, prefix="/api")
    app.include_router(files.router, prefix="/api")
    app.include_router(team.router, prefix="/api")

    dist_dir = Path(__file__).parent.parent.parent.parent / "web" / "dist"
    if dist_dir.exists():
        app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="static")

    return app
