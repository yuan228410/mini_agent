"""FastAPI 应用入口"""
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .routes import chat, models, skills, config, commands, workspaces, files, team
from .deps import init_components, shutdown_mcp
from ..logger import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_components()
    
    # Web 端默认启用异步写入优化（可通过配置覆盖）
    from ..memory.history_db import HistoryDBPool
    from ..config import DATABASE
    
    # 如果配置中 async_write 为 None，Web 端默认启用
    async_write = DATABASE.get("history", {}).get("async_write", None)
    if async_write is None:
        HistoryDBPool.set_async_write_default(True)
        logger.info("[Web] 已启用 HistoryDB 异步写入优化（默认）")
    elif async_write:
        logger.info("[Web] 已启用 HistoryDB 异步写入优化（配置）")
    else:
        logger.info("[Web] 使用同步写入模式（配置）")
    
    yield

    # 中止所有活跃会话（让 run_tool_loop 尽快退出）
    from .routes.chat import abort_all_sessions
    abort_all_sessions()

    # 关闭线程池（cancel_futures 让阻塞的 run_tool_loop 线程尽快退出）
    from .routes.chat import _executor
    _executor.shutdown(wait=False, cancel_futures=True)

    # 关闭历史数据库连接池
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
