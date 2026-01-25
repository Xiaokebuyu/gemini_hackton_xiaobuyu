"""
配置管理模块
"""
import os
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()


class Settings(BaseModel):
    """应用配置"""
    
    # Firebase 配置
    google_application_credentials: str = os.getenv(
        "GOOGLE_APPLICATION_CREDENTIALS", 
        "./firebase-credentials.json"
    )
    firestore_database: str = os.getenv("FIRESTORE_DATABASE", "(default)")
    
    # Gemini API 配置 (全面使用 Gemini 3 系列)
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_flash_model: str = os.getenv("GEMINI_FLASH_MODEL", "gemini-3-flash-preview")
    gemini_main_model: str = os.getenv("GEMINI_MAIN_MODEL", "gemini-3-flash-preview")
    gemini_pro_model: str = os.getenv("GEMINI_PRO_MODEL", "gemini-3-pro-preview")
    gemini_embedding_model: str = os.getenv("GEMINI_EMBEDDING_MODEL", "text-embedding-004")
    
    # Gemini 3 思考配置
    # thinking_level: 思考层级 - "lowest", "low", "medium", "high"
    thinking_enabled: bool = True
    thinking_level: Literal["lowest", "low", "medium", "high"] = "medium"
    include_thoughts: bool = True  # 是否返回思考摘要
    
    # 热记忆配置
    active_window_size: int = 20
    archive_threshold: int = 40
    max_context_retry: int = 3
    context_request_pattern: str = r'\[NEED_CONTEXT:\s*(.+?)\]'

    # MCP 记忆网关配置
    memory_window_tokens: int = int(os.getenv("MEMORY_WINDOW_TOKENS", "120000"))
    memory_insert_budget_tokens: int = int(os.getenv("MEMORY_INSERT_BUDGET_TOKENS", "20000"))
    memory_max_threads: int = int(os.getenv("MEMORY_MAX_THREADS", "3"))
    memory_max_raw_messages: int = int(os.getenv("MEMORY_MAX_RAW_MESSAGES", "12"))
    memory_session_ttl_seconds: int = int(os.getenv("MEMORY_SESSION_TTL_SECONDS", "3600"))
    memory_stream_load_limit: int = int(os.getenv("MEMORY_STREAM_LOAD_LIMIT", "2000"))
    embedding_provider: Literal["gemini", "cloudflare"] = os.getenv("EMBEDDING_PROVIDER", "gemini")
    
    # 冷记忆配置（暂不启用）
    cloudflare_account_id: str = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
    cloudflare_api_token: str = os.getenv("CLOUDFLARE_API_TOKEN", "")
    cloudflare_embedding_model: str = "@cf/baai/bge-base-en-v1.5"
    
    # API 配置
    api_prefix: str = "/api"
    cors_origins: list = ["*"]
    
    model_config = ConfigDict(env_file=".env", case_sensitive=False)


# 全局配置实例
settings = Settings()


def validate_config() -> bool:
    """
    验证配置是否完整
    
    Returns:
        bool: 配置是否有效
    """
    if not settings.gemini_api_key:
        print("警告: 未设置 GEMINI_API_KEY")
        return False
    
    if not Path(settings.google_application_credentials).exists():
        print(f"警告: Firebase 凭证文件不存在: {settings.google_application_credentials}")
        return False
    
    return True
