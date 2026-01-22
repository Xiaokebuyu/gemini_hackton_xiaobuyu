"""
FastAPI 应用入口
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings, validate_config
from app.routers import topics_router, graphs_router, flash_router, pro_router, gm_router, game_router

# 创建 FastAPI 应用
app = FastAPI(
    title="LLM 记忆系统 API",
    description="基于 Firestore 的中断驱动上下文系统",
    version="0.1.0",
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
app.include_router(topics_router, prefix=settings.api_prefix, tags=["主题"])
app.include_router(graphs_router, prefix=settings.api_prefix, tags=["图谱"])
app.include_router(flash_router, prefix=settings.api_prefix, tags=["Flash"])
app.include_router(pro_router, prefix=settings.api_prefix, tags=["Pro"])
app.include_router(gm_router, prefix=settings.api_prefix, tags=["GM"])
app.include_router(game_router, prefix=settings.api_prefix, tags=["Game"])


@app.on_event("startup")
async def startup_event():
    """应用启动时的初始化"""
    print("=" * 60)
    print("LLM 记忆系统启动中...")
    print("=" * 60)
    
    if validate_config():
        print("✓ 配置验证通过")
    else:
        print("✗ 配置验证失败，请检查环境变量")
    
    print(f"✓ API 文档: http://localhost:8000/docs")
    print("=" * 60)


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
    return {"status": "healthy"}
