"""FastAPI 应用入口"""
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .routes import chat, models, skills, config, commands, workspaces, files
from .deps import init_components, shutdown_mcp

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_components()
    yield
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

    dist_dir = Path(__file__).parent.parent.parent.parent / "web" / "dist"
    if dist_dir.exists():
        app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="static")

    return app
