"""
FastAPI 应用入口
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.config import settings, validate_config
from app.exceptions import FirestoreIOError, MCPServiceUnavailableError, WorldGraphError
from app.routers import game_v2_router
from app.services.mcp_client_pool import MCPClientPool


@asynccontextmanager
async def lifespan(app: FastAPI):
    # === startup ===
    print("=" * 60)
    print("LLM 记忆系统启动中...")
    print("=" * 60)

    if validate_config():
        print("✓ 配置验证通过")
    else:
        print("✗ 配置验证失败，请检查环境变量")

    if settings.mcp_startup_fail_fast:
        pool = await MCPClientPool.get_instance()
        dependencies = await pool.probe_dependencies(
            timeout_seconds=settings.mcp_probe_timeout_seconds,
            server_types=[MCPClientPool.COMBAT],
        )
        failed = {
            name: detail
            for name, detail in dependencies.items()
            if not detail.get("ok")
        }
        if failed:
            print("✗ MCP 依赖检查失败:")
            for name, detail in failed.items():
                print(f"  - {name}: {detail.get('error') or detail}")
            raise RuntimeError(f"MCP dependencies unavailable: {failed}")
        print("✓ MCP 依赖检查通过")

    print(f"✓ API 文档: http://localhost:8000/docs")
    print("=" * 60)

    yield

    # === shutdown ===
    try:
        from app.dependencies import get_instance_manager
        instance_manager = get_instance_manager()
        await instance_manager.flush_all()
    except (FirestoreIOError, OSError, WorldGraphError) as exc:
        logging.getLogger(__name__).warning(
            "InstanceManager flush_all failed on shutdown: %s", exc
        )
    await MCPClientPool.shutdown()


# 创建 FastAPI 应用
app = FastAPI(
    title="LLM 记忆系统 API",
    description="基于 Firestore 的中断驱动上下文系统",
    version="0.1.0",
    lifespan=lifespan,
)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(game_v2_router, prefix=settings.api_prefix, tags=["Game V2"])


@app.get("/")
async def root():
    """根路径"""
    return {
        "message": "LLM 记忆系统 API",
        "version": "0.1.0",
        "docs": "/docs"
    }


@app.get("/health")
async def health_check():
    """健康检查"""
    try:
        pool = await MCPClientPool.get_instance()
        dependencies = await pool.probe_dependencies(
            timeout_seconds=settings.mcp_probe_timeout_seconds,
            server_types=[MCPClientPool.COMBAT],
        )
        healthy = all(item.get("ok") for item in dependencies.values())
        payload = {
            "status": "healthy" if healthy else "unhealthy",
            "dependencies": dependencies,
        }
        if healthy:
            return payload
        return JSONResponse(status_code=503, content=payload)
    except (MCPServiceUnavailableError, OSError, RuntimeError, asyncio.TimeoutError) as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "error": f"{type(exc).__name__}: {exc}",
            },
        )


@app.get(f"{settings.api_prefix}/admin/mcp/diagnostics")
async def mcp_diagnostics():
    """MCP transport diagnostics for streamable-http/sse/stdio troubleshooting."""
    try:
        pool = await MCPClientPool.get_instance()
        return await pool.get_diagnostics(
            include_probe=True,
            timeout_seconds=settings.mcp_probe_timeout_seconds,
        )
    except (MCPServiceUnavailableError, OSError, RuntimeError, asyncio.TimeoutError) as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
