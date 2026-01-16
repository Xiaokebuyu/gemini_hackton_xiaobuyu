"""
Embedding 工具函数
当前状态: 热记忆阶段暂不使用
未来用途: BigQuery 冷记忆的语义检索

"""
import httpx
import numpy as np
from typing import List
from app.config import settings


async def get_cloudflare_embedding(text: str) -> List[float]:
    """
    使用 Cloudflare Workers AI 生成文本的 embedding
    
    Args:
        text: 输入文本
        
    Returns:
        List[float]: 768 维向量
        
    Raises:
        Exception: 如果 API 调用失败
    """
    url = f"https://api.cloudflare.com/client/v4/accounts/{settings.cloudflare_account_id}/ai/run/{settings.cloudflare_embedding_model}"
    
    headers = {
        "Authorization": f"Bearer {settings.cloudflare_api_token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "text": [text]  # Cloudflare API 接受文本数组
    }
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            
            result = response.json()
            
            # Cloudflare API 返回格式: {"result": {"shape": [1, 768], "data": [[...]]}}
            if "result" in result and "data" in result["result"]:
                embedding = result["result"]["data"][0]  # 获取第一个文本的 embedding
                return embedding
            else:
                raise Exception(f"Unexpected response format: {result}")
                
        except httpx.HTTPError as e:
            raise Exception(f"Cloudflare Embedding API error: {str(e)}")
        except Exception as e:
            raise Exception(f"Failed to get embedding: {str(e)}")


def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """
    计算两个向量的余弦相似度
    
    Args:
        vec1: 第一个向量
        vec2: 第二个向量
        
    Returns:
        float: 余弦相似度 (范围 -1 到 1)
    """
    if not vec1 or not vec2:
        return 0.0
    
    if len(vec1) != len(vec2):
        raise ValueError(f"Vector dimensions must match: {len(vec1)} vs {len(vec2)}")
    
    # 转换为 numpy 数组
    v1 = np.array(vec1)
    v2 = np.array(vec2)
    
    # 计算余弦相似度
    dot_product = np.dot(v1, v2)
    norm_v1 = np.linalg.norm(v1)
    norm_v2 = np.linalg.norm(v2)
    
    # 避免除以零
    if norm_v1 == 0 or norm_v2 == 0:
        return 0.0
    
    similarity = dot_product / (norm_v1 * norm_v2)
    
    return float(similarity)


def batch_cosine_similarity(query_vec: List[float], vectors: List[List[float]]) -> List[float]:
    """
    批量计算查询向量与多个向量的余弦相似度
    
    Args:
        query_vec: 查询向量
        vectors: 向量列表
        
    Returns:
        List[float]: 相似度列表
    """
    if not query_vec or not vectors:
        return []
    
    similarities = []
    for vec in vectors:
        try:
            sim = cosine_similarity(query_vec, vec)
            similarities.append(sim)
        except Exception:
            # 如果某个向量计算失败，返回0
            similarities.append(0.0)
    
    return similarities
