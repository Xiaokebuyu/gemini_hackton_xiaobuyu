"""
话题状态管理器

职责：
- 维护当前活跃的话题 ID
- AI 调用检索工具时自动切换
- 不调用则保持当前话题
"""

from typing import Optional, Dict, Any
from datetime import datetime
from dataclasses import dataclass, field


@dataclass
class TopicStateData:
    """
    话题状态数据
    
    追踪当前活跃的话题信息
    """
    current_topic_id: Optional[str] = None
    current_thread_id: Optional[str] = None
    last_retrieval_at: Optional[datetime] = None
    retrieval_count: int = 0
    
    def is_active(self) -> bool:
        """是否有活跃话题"""
        return self.current_thread_id is not None
    
    def clear(self) -> None:
        """清除状态"""
        self.current_topic_id = None
        self.current_thread_id = None
        self.last_retrieval_at = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "current_topic_id": self.current_topic_id,
            "current_thread_id": self.current_thread_id,
            "last_retrieval_at": self.last_retrieval_at.isoformat() if self.last_retrieval_at else None,
            "retrieval_count": self.retrieval_count,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TopicStateData":
        """从字典创建"""
        last_retrieval = data.get("last_retrieval_at")
        if isinstance(last_retrieval, str):
            last_retrieval = datetime.fromisoformat(last_retrieval)
        
        return cls(
            current_topic_id=data.get("current_topic_id"),
            current_thread_id=data.get("current_thread_id"),
            last_retrieval_at=last_retrieval,
            retrieval_count=data.get("retrieval_count", 0),
        )


class TopicStateManager:
    """
    话题状态管理器
    
    核心功能：
    1. 追踪当前活跃话题
    2. AI 调用工具时自动更新状态
    3. 支持被动式话题连续性
    
    话题切换规则：
    - AI 调用 retrieve_thread_history 时：切换到检索的话题
    - AI 调用 list_topics 时：不改变状态
    - AI 不调用任何工具时：保持当前话题
    """
    
    # 会触发话题切换的工具列表
    TOPIC_SWITCH_TOOLS = {"retrieve_thread_history", "get_insight_evolution"}
    
    def __init__(self):
        """初始化状态管理器"""
        self._state = TopicStateData()
    
    # ========== 状态访问 ==========
    
    def get_current_topic_id(self) -> Optional[str]:
        """
        获取当前主题 ID
        
        Returns:
            当前主题 ID 或 None
        """
        return self._state.current_topic_id
    
    def get_current_thread_id(self) -> Optional[str]:
        """
        获取当前话题 ID
        
        Returns:
            当前话题 ID 或 None
        """
        return self._state.current_thread_id
    
    def is_active(self) -> bool:
        """
        是否有活跃话题
        
        Returns:
            True 如果有当前话题
        """
        return self._state.is_active()
    
    def get_retrieval_count(self) -> int:
        """
        获取检索次数
        
        Returns:
            检索次数
        """
        return self._state.retrieval_count
    
    def get_last_retrieval_time(self) -> Optional[datetime]:
        """
        获取最后一次检索时间
        
        Returns:
            最后检索时间或 None
        """
        return self._state.last_retrieval_at
    
    # ========== 状态更新 ==========
    
    def on_tool_call(
        self, 
        tool_name: str, 
        params: Dict[str, Any],
        topic_id: Optional[str] = None
    ) -> bool:
        """
        AI 调用工具时更新状态
        
        Args:
            tool_name: 工具名称
            params: 工具参数
            topic_id: 话题所属的主题 ID（可选，如果不提供需要后续查询）
            
        Returns:
            True 如果话题发生了切换
        """
        if tool_name not in self.TOPIC_SWITCH_TOOLS:
            return False
        
        thread_id = params.get("thread_id")
        if not thread_id:
            return False
        
        # 检查是否真的切换了
        switched = thread_id != self._state.current_thread_id
        
        # 更新状态
        self._state.current_thread_id = thread_id
        self._state.current_topic_id = topic_id
        self._state.last_retrieval_at = datetime.now()
        self._state.retrieval_count += 1
        
        return switched
    
    def set_topic(self, topic_id: str, thread_id: str) -> None:
        """
        手动设置当前话题
        
        Args:
            topic_id: 主题 ID
            thread_id: 话题 ID
        """
        self._state.current_topic_id = topic_id
        self._state.current_thread_id = thread_id
        self._state.last_retrieval_at = datetime.now()
    
    def clear(self) -> None:
        """清除状态（新会话时）"""
        self._state.clear()
        self._state.retrieval_count = 0
    
    # ========== 序列化 ==========
    
    def get_state(self) -> TopicStateData:
        """
        获取状态数据
        
        Returns:
            状态数据副本
        """
        return TopicStateData(
            current_topic_id=self._state.current_topic_id,
            current_thread_id=self._state.current_thread_id,
            last_retrieval_at=self._state.last_retrieval_at,
            retrieval_count=self._state.retrieval_count,
        )
    
    def set_state(self, state: TopicStateData) -> None:
        """
        设置状态数据
        
        Args:
            state: 状态数据
        """
        self._state = state
    
    def to_dict(self) -> Dict[str, Any]:
        """
        转换为字典
        
        Returns:
            状态字典
        """
        return self._state.to_dict()
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TopicStateManager":
        """
        从字典创建
        
        Args:
            data: 状态字典
            
        Returns:
            TopicStateManager 实例
        """
        manager = cls()
        manager._state = TopicStateData.from_dict(data)
        return manager
    
    # ========== 辅助方法 ==========
    
    def get_context_info(self) -> str:
        """
        获取状态信息（用于日志或调试）
        
        Returns:
            状态描述文本
        """
        if not self._state.is_active():
            return "无活跃话题"
        
        return (
            f"当前话题: {self._state.current_thread_id} "
            f"(主题: {self._state.current_topic_id}), "
            f"检索次数: {self._state.retrieval_count}"
        )
    
    def __repr__(self) -> str:
        """字符串表示"""
        return f"TopicStateManager(thread={self._state.current_thread_id}, topic={self._state.current_topic_id})"
