"""
配置管理模块
"""
import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel
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
    
    # Gemini API 配置
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_flash_model: str = "gemini-2.0-flash-exp"
    gemini_main_model: str = "gemini-2.0-flash-exp"
    
    # Cloudflare 配置
    cloudflare_account_id: str = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
    cloudflare_api_token: str = os.getenv("CLOUDFLARE_API_TOKEN", "")
    cloudflare_embedding_model: str = "@cf/baai/bge-base-en-v1.5"
    
    # 路由配置
    embedding_threshold: float = 0.85  # 直接路由的相似度阈值
    max_candidate_topics: int = 3      # 最大候选主题数
    
    # 上下文配置
    max_history_messages: int = 50     # 最大历史消息数
    
    # API 配置
    api_prefix: str = "/api"
    cors_origins: list = ["*"]
    
    class Config:
        env_file = ".env"
        case_sensitive = False


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
    
    if not settings.cloudflare_account_id or not settings.cloudflare_api_token:
        print("警告: 未设置 Cloudflare 配置")
        return False
    
    return True
