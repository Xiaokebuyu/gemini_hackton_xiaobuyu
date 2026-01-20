"""
标准 API 消息流管理器

职责：
- 维护当前 session 的完整消息列表
- 提供末尾 32k tokens 的活跃窗口
- 提供溢出部分的提取（供归档组件使用）
- 消息格式符合标准 API 格式
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid

from .models import APIMessage, count_tokens


class MessageStream:
    """
    标准 API 消息流管理器
    
    核心功能：
    1. 维护完整的消息列表（只追加，不修改）
    2. 提供活跃窗口（末尾 32k tokens）
    3. 提供溢出部分（超出 32k 的消息，需要被归档）
    
    Token 计算规则：
    - 32k 只针对原始消息流
    - 组装的上下文（系统提示、总结、检索历史）不计入 32k
    """
    
    ACTIVE_WINDOW_TOKENS = 32000  # 活跃窗口大小：32k tokens
    
    def __init__(self, session_id: str, active_window_tokens: Optional[int] = None):
        """
        初始化消息流
        
        Args:
            session_id: 会话ID
            active_window_tokens: 活跃窗口 token 上限
        """
        self.session_id = session_id
        self.active_window_tokens = active_window_tokens or self.ACTIVE_WINDOW_TOKENS
        self._messages: List[APIMessage] = []
        self._total_tokens: int = 0
        self._archived_message_ids: set = set()  # 已归档的消息ID集合
    
    # ========== 基础操作 ==========
    
    def append(self, message: APIMessage) -> None:
        """
        追加消息到流末尾
        
        Args:
            message: API消息对象
        """
        self._messages.append(message)
        self._total_tokens += message.token_count
    
    def append_user_message(self, content: str) -> APIMessage:
        """
        追加用户消息（便捷方法）
        
        Args:
            content: 消息内容
            
        Returns:
            创建的消息对象
        """
        message = APIMessage(
            message_id=self._generate_message_id(),
            role="user",
            content=content,
            timestamp=datetime.now(),
        )
        self.append(message)
        return message
    
    def append_assistant_message(self, content: str) -> APIMessage:
        """
        追加助手消息（便捷方法）
        
        Args:
            content: 消息内容
            
        Returns:
            创建的消息对象
        """
        message = APIMessage(
            message_id=self._generate_message_id(),
            role="assistant",
            content=content,
            timestamp=datetime.now(),
        )
        self.append(message)
        return message
    
    def get_all(self) -> List[APIMessage]:
        """
        获取完整消息列表
        
        Returns:
            消息列表的副本
        """
        return self._messages.copy()
    
    def get_message_by_id(self, message_id: str) -> Optional[APIMessage]:
        """
        根据ID获取消息
        
        Args:
            message_id: 消息ID
            
        Returns:
            消息对象或None
        """
        for msg in self._messages:
            if msg.message_id == message_id:
                return msg
        return None
    
    @property
    def total_tokens(self) -> int:
        """当前总 token 数"""
        return self._total_tokens
    
    @property
    def message_count(self) -> int:
        """消息数量"""
        return len(self._messages)
    
    @property
    def is_empty(self) -> bool:
        """是否为空"""
        return len(self._messages) == 0
    
    # ========== 窗口操作 ==========
    
    def get_active_window(self) -> List[APIMessage]:
        """
        获取活跃窗口（末尾 32k tokens）
        
        从最新消息往前累计，直到达到 32k tokens
        
        Returns:
            活跃窗口内的消息列表
        """
        if self._total_tokens <= self.active_window_tokens:
            return self._messages.copy()
        
        result = []
        accumulated = 0
        
        # 从最新消息往前遍历
        for msg in reversed(self._messages):
            if accumulated + msg.token_count > self.active_window_tokens:
                break
            result.insert(0, msg)
            accumulated += msg.token_count
        
        return result
    
    def get_active_window_tokens(self) -> int:
        """
        获取活跃窗口的 token 数
        
        Returns:
            活跃窗口的 token 数量
        """
        active_window = self.get_active_window()
        return sum(msg.token_count for msg in active_window)
    
    def get_overflow(self) -> List[APIMessage]:
        """
        获取溢出部分（超出 32k 的消息）
        
        这些消息需要被归档到 Firestore
        
        Returns:
            溢出的消息列表
        """
        if self._total_tokens <= self.active_window_tokens:
            return []
        
        active_window = self.get_active_window()
        active_ids = {msg.message_id for msg in active_window}
        
        return [msg for msg in self._messages if msg.message_id not in active_ids]
    
    def get_unarchived_overflow(self) -> List[APIMessage]:
        """
        获取未归档的溢出部分
        
        过滤掉已经归档的消息
        
        Returns:
            未归档的溢出消息列表
        """
        overflow = self.get_overflow()
        return [
            msg for msg in overflow 
            if msg.message_id not in self._archived_message_ids
        ]
    
    def has_overflow(self) -> bool:
        """
        是否有溢出
        
        Returns:
            True 如果总 token 数超过活跃窗口大小
        """
        return self._total_tokens > self.active_window_tokens
    
    def get_overflow_tokens(self) -> int:
        """
        获取溢出的 token 数
        
        Returns:
            溢出的 token 数量
        """
        return max(0, self._total_tokens - self.active_window_tokens)
    
    # ========== 归档标记 ==========
    
    def mark_as_archived(self, message_ids: List[str]) -> None:
        """
        标记消息为已归档
        
        Args:
            message_ids: 已归档的消息ID列表
        """
        self._archived_message_ids.update(message_ids)
    
    def is_archived(self, message_id: str) -> bool:
        """
        检查消息是否已归档
        
        Args:
            message_id: 消息ID
            
        Returns:
            True 如果消息已归档
        """
        return message_id in self._archived_message_ids
    
    # ========== 序列化 ==========
    
    def to_dict(self) -> Dict[str, Any]:
        """
        转换为字典格式（用于持久化）
        
        Returns:
            包含所有状态的字典
        """
        return {
            "session_id": self.session_id,
            "active_window_tokens": self.active_window_tokens,
            "messages": [msg.to_dict() for msg in self._messages],
            "total_tokens": self._total_tokens,
            "archived_message_ids": list(self._archived_message_ids),
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MessageStream":
        """
        从字典创建实例
        
        Args:
            data: 状态字典
            
        Returns:
            MessageStream 实例
        """
        stream = cls(
            session_id=data["session_id"],
            active_window_tokens=data.get("active_window_tokens"),
        )
        stream._messages = [
            APIMessage.from_dict(msg_data) 
            for msg_data in data.get("messages", [])
        ]
        stream._total_tokens = data.get("total_tokens", 0)
        stream._archived_message_ids = set(data.get("archived_message_ids", []))
        
        # 重新计算 token 数（如果未提供）
        if stream._total_tokens == 0 and stream._messages:
            stream._total_tokens = sum(msg.token_count for msg in stream._messages)
        
        return stream
    
    def to_api_format(self) -> List[Dict[str, str]]:
        """
        转换为 LLM API 格式（仅活跃窗口）
        
        Returns:
            符合 API 格式的消息列表
        """
        active_window = self.get_active_window()
        return [
            {"role": msg.role, "content": msg.content}
            for msg in active_window
        ]
    
    # ========== 统计信息 ==========
    
    def get_stats(self) -> Dict[str, Any]:
        """
        获取统计信息
        
        Returns:
            包含各项统计数据的字典
        """
        active_window = self.get_active_window()
        overflow = self.get_overflow()
        
        return {
            "session_id": self.session_id,
            "total_messages": self.message_count,
            "total_tokens": self._total_tokens,
            "active_window_messages": len(active_window),
            "active_window_tokens": sum(msg.token_count for msg in active_window),
            "overflow_messages": len(overflow),
            "overflow_tokens": sum(msg.token_count for msg in overflow),
            "archived_count": len(self._archived_message_ids),
            "has_overflow": self.has_overflow(),
            "active_window_limit": self.active_window_tokens,
        }
    
    # ========== 私有方法 ==========
    
    def _generate_message_id(self) -> str:
        """生成消息ID"""
        return f"msg_{uuid.uuid4().hex[:12]}"
    
    def __len__(self) -> int:
        """返回消息数量"""
        return len(self._messages)
    
    def __repr__(self) -> str:
        """字符串表示"""
        return (
            f"MessageStream(session_id={self.session_id}, "
            f"messages={self.message_count}, "
            f"tokens={self._total_tokens})"
        )
