"""
Context Window Service.

200K 上下文窗口管理器，负责：
- Token 计数（使用 tiktoken）
- 消息管理
- 满载检测与图谱化触发
"""
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import tiktoken
    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _TIKTOKEN_AVAILABLE = False

from app.models.context_window import (
    AddMessageResult,
    ContextWindowSnapshot,
    ContextWindowState,
    GraphizeRequest,
    RemoveGraphizedResult,
    WindowMessage,
)
from app.models.npc_instance import GraphizeTrigger


def count_tokens(text: str) -> int:
    """
    计算文本的 token 数

    Args:
        text: 输入文本

    Returns:
        token 数量
    """
    if not text:
        return 0

    if _TIKTOKEN_AVAILABLE:
        try:
            encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        except Exception:
            pass

    # 降级：按字符估算
    # 中文约 2 字符 = 1 token，英文约 4 字符 = 1 token
    # 使用保守估计
    chinese_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_count = len(text) - chinese_count
    return chinese_count // 2 + other_count // 4 + 1


class ContextWindow:
    """
    200K 上下文窗口管理器

    功能：
    1. 维护消息列表，追踪 token 使用
    2. 检测满载并触发图谱化
    3. 移除已图谱化的消息，释放空间
    """

    def __init__(
        self,
        npc_id: str,
        world_id: str,
        max_tokens: int = 200_000,
        graphize_threshold: float = 0.9,
        keep_recent_tokens: int = 50_000,
    ):
        """
        初始化上下文窗口

        Args:
            npc_id: NPC ID
            world_id: 世界 ID
            max_tokens: 最大 token 容量（默认 200K）
            graphize_threshold: 图谱化阈值（默认 90%）
            keep_recent_tokens: 图谱化后保留的 token 数（默认 50K）
        """
        self.npc_id = npc_id
        self.world_id = world_id

        # 配置
        self.max_tokens = max_tokens
        self.graphize_threshold = graphize_threshold
        self.keep_recent_tokens = keep_recent_tokens

        # 状态
        self._messages: List[WindowMessage] = []
        self._current_tokens: int = 0
        self._system_prompt_tokens: int = 0
        self._system_prompt: str = ""

        # 统计
        self._total_messages_processed: int = 0
        self._total_messages_graphized: int = 0

        # 时间戳
        self._created_at = datetime.now()
        self._updated_at = datetime.now()

    # ==================== 属性 ====================

    @property
    def current_tokens(self) -> int:
        """当前使用的 token 数"""
        return self._current_tokens + self._system_prompt_tokens

    @property
    def usage_ratio(self) -> float:
        """Token 使用率"""
        if self.max_tokens == 0:
            return 0.0
        return self.current_tokens / self.max_tokens

    @property
    def available_tokens(self) -> int:
        """剩余可用 token 数"""
        return max(0, self.max_tokens - self.current_tokens)

    @property
    def should_graphize(self) -> bool:
        """是否需要触发图谱化"""
        return self.usage_ratio >= self.graphize_threshold

    @property
    def message_count(self) -> int:
        """消息数量"""
        return len(self._messages)

    @property
    def messages(self) -> List[WindowMessage]:
        """获取消息列表的副本"""
        return self._messages.copy()

    # ==================== 系统提示词 ====================

    def set_system_prompt(self, prompt: str) -> int:
        """
        设置系统提示词

        Args:
            prompt: 系统提示词

        Returns:
            系统提示词的 token 数
        """
        self._system_prompt = prompt
        self._system_prompt_tokens = count_tokens(prompt)
        self._updated_at = datetime.now()
        return self._system_prompt_tokens

    def get_system_prompt(self) -> str:
        """获取系统提示词"""
        return self._system_prompt

    # ==================== 消息管理 ====================

    def add_message(
        self,
        role: str,
        content: str,
        message_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AddMessageResult:
        """
        添加消息到窗口

        Args:
            role: 消息角色（user/assistant/system）
            content: 消息内容
            message_id: 可选的消息 ID
            metadata: 可选的元数据

        Returns:
            AddMessageResult 包含添加结果和是否需要图谱化
        """
        # 生成消息 ID
        if message_id is None:
            message_id = f"msg_{uuid.uuid4().hex[:12]}"

        # 计算 token 数
        token_count = count_tokens(content)

        # 创建消息
        message = WindowMessage(
            id=message_id,
            role=role,
            content=content,
            timestamp=datetime.now(),
            token_count=token_count,
            metadata=metadata or {},
        )

        # 添加到列表
        self._messages.append(message)
        self._current_tokens += token_count
        self._total_messages_processed += 1
        self._updated_at = datetime.now()

        # 检查是否需要图谱化
        should_graphize = self.should_graphize
        messages_to_graphize_count = 0
        if should_graphize:
            messages_to_graphize = self._select_messages_for_graphize()
            messages_to_graphize_count = len(messages_to_graphize)

        return AddMessageResult(
            message_id=message_id,
            token_count=token_count,
            current_tokens=self.current_tokens,
            usage_ratio=self.usage_ratio,
            should_graphize=should_graphize,
            messages_to_graphize_count=messages_to_graphize_count,
        )

    def get_message(self, message_id: str) -> Optional[WindowMessage]:
        """根据 ID 获取消息"""
        for msg in self._messages:
            if msg.id == message_id:
                return msg
        return None

    def get_recent_messages(self, count: int) -> List[WindowMessage]:
        """获取最近 N 条消息"""
        return self._messages[-count:] if count > 0 else []

    def get_all_messages(self) -> List[WindowMessage]:
        """获取所有消息"""
        return self._messages.copy()

    # ==================== 图谱化触发 ====================

    def check_graphize_trigger(self) -> GraphizeTrigger:
        """
        检查是否需要图谱化

        Returns:
            GraphizeTrigger 包含触发信息
        """
        if not self.should_graphize:
            return GraphizeTrigger(
                should_graphize=False,
                messages_to_graphize=[],
                urgency=self.usage_ratio,
                reason="",
            )

        messages_to_graphize = self._select_messages_for_graphize()

        return GraphizeTrigger(
            should_graphize=True,
            messages_to_graphize=messages_to_graphize,
            urgency=self.usage_ratio,
            reason=f"Token 使用率达到 {self.usage_ratio:.1%}，超过阈值 {self.graphize_threshold:.0%}",
        )

    def _select_messages_for_graphize(self) -> List[WindowMessage]:
        """
        选择需要图谱化的旧消息

        策略：保留最近 keep_recent_tokens 的消息，其余图谱化
        """
        if not self._messages:
            return []

        # 从最新消息往前累计，找到要保留的消息
        keep_messages = []
        accumulated_tokens = 0

        for msg in reversed(self._messages):
            if accumulated_tokens + msg.token_count > self.keep_recent_tokens:
                break
            keep_messages.insert(0, msg)
            accumulated_tokens += msg.token_count

        # 要保留的消息 ID 集合
        keep_ids = {msg.id for msg in keep_messages}

        # 其余消息需要图谱化（排除已图谱化的）
        to_graphize = [
            msg for msg in self._messages
            if msg.id not in keep_ids and not msg.is_graphized
        ]

        return to_graphize

    def get_graphize_request(
        self,
        conversation_summary: Optional[str] = None,
        current_scene: Optional[str] = None,
        game_day: int = 1,
    ) -> GraphizeRequest:
        """
        获取图谱化请求

        Args:
            conversation_summary: 对话摘要
            current_scene: 当前场景
            game_day: 游戏日

        Returns:
            GraphizeRequest 包含需要图谱化的消息
        """
        messages = self._select_messages_for_graphize()
        return GraphizeRequest(
            npc_id=self.npc_id,
            world_id=self.world_id,
            messages=messages,
            conversation_summary=conversation_summary,
            current_scene=current_scene,
            game_day=game_day,
        )

    # ==================== 图谱化后处理 ====================

    def mark_messages_graphized(self, message_ids: List[str]) -> None:
        """
        标记消息为已图谱化

        Args:
            message_ids: 已图谱化的消息 ID 列表
        """
        graphized_at = datetime.now()
        message_id_set = set(message_ids)

        for msg in self._messages:
            if msg.id in message_id_set:
                msg.is_graphized = True
                msg.graphized_at = graphized_at

        self._updated_at = datetime.now()

    def remove_graphized_messages(self) -> RemoveGraphizedResult:
        """
        移除已图谱化的消息，释放 token 空间

        Returns:
            RemoveGraphizedResult 包含移除结果
        """
        # 找出要移除的消息
        to_remove = [msg for msg in self._messages if msg.is_graphized]

        if not to_remove:
            return RemoveGraphizedResult(
                removed_count=0,
                tokens_freed=0,
                current_tokens=self.current_tokens,
                usage_ratio=self.usage_ratio,
            )

        # 计算释放的 token 数
        tokens_freed = sum(msg.token_count for msg in to_remove)

        # 移除消息
        self._messages = [msg for msg in self._messages if not msg.is_graphized]
        self._current_tokens -= tokens_freed
        self._total_messages_graphized += len(to_remove)
        self._updated_at = datetime.now()

        return RemoveGraphizedResult(
            removed_count=len(to_remove),
            tokens_freed=tokens_freed,
            current_tokens=self.current_tokens,
            usage_ratio=self.usage_ratio,
        )

    # ==================== 上下文组装 ====================

    def build_context(self) -> List[Dict[str, str]]:
        """
        构建 LLM API 格式的上下文

        Returns:
            符合 LLM API 格式的消息列表
        """
        messages = []

        # 添加系统提示词
        if self._system_prompt:
            messages.append({
                "role": "system",
                "content": self._system_prompt,
            })

        # 添加对话消息
        for msg in self._messages:
            messages.append({
                "role": msg.role,
                "content": msg.content,
            })

        return messages

    def build_context_with_injection(
        self,
        memory_injection: Optional[str] = None,
        additional_context: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """
        构建带记忆注入的上下文

        Args:
            memory_injection: 来自 Flash 的记忆注入
            additional_context: 额外的上下文（如场景描述）

        Returns:
            符合 LLM API 格式的消息列表
        """
        messages = []

        # 组装系统提示词
        system_content = self._system_prompt

        if additional_context:
            system_content += f"\n\n{additional_context}"

        if memory_injection:
            system_content += f"\n\n## 相关记忆\n{memory_injection}"

        if system_content:
            messages.append({
                "role": "system",
                "content": system_content,
            })

        # 添加对话消息
        for msg in self._messages:
            messages.append({
                "role": msg.role,
                "content": msg.content,
            })

        return messages

    # ==================== 序列化 ====================

    def to_state(self) -> ContextWindowState:
        """转换为状态模型"""
        return ContextWindowState(
            npc_id=self.npc_id,
            world_id=self.world_id,
            max_tokens=self.max_tokens,
            graphize_threshold=self.graphize_threshold,
            keep_recent_tokens=self.keep_recent_tokens,
            current_tokens=self._current_tokens,
            system_prompt_tokens=self._system_prompt_tokens,
            messages=self._messages.copy(),
            total_messages_processed=self._total_messages_processed,
            total_messages_graphized=self._total_messages_graphized,
            created_at=self._created_at,
            updated_at=self._updated_at,
        )

    def to_snapshot(self) -> ContextWindowSnapshot:
        """转换为快照（用于持久化，不包含完整消息内容）"""
        return ContextWindowSnapshot(
            npc_id=self.npc_id,
            world_id=self.world_id,
            max_tokens=self.max_tokens,
            graphize_threshold=self.graphize_threshold,
            keep_recent_tokens=self.keep_recent_tokens,
            current_tokens=self._current_tokens,
            system_prompt_tokens=self._system_prompt_tokens,
            message_count=len(self._messages),
            message_ids=[msg.id for msg in self._messages],
            total_messages_processed=self._total_messages_processed,
            total_messages_graphized=self._total_messages_graphized,
        )

    @classmethod
    def from_state(cls, state: ContextWindowState) -> "ContextWindow":
        """从状态模型恢复"""
        window = cls(
            npc_id=state.npc_id,
            world_id=state.world_id,
            max_tokens=state.max_tokens,
            graphize_threshold=state.graphize_threshold,
            keep_recent_tokens=state.keep_recent_tokens,
        )
        window._messages = state.messages.copy()
        window._current_tokens = state.current_tokens
        window._system_prompt_tokens = state.system_prompt_tokens
        window._total_messages_processed = state.total_messages_processed
        window._total_messages_graphized = state.total_messages_graphized
        window._created_at = state.created_at
        window._updated_at = state.updated_at
        return window

    # ==================== 统计信息 ====================

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "npc_id": self.npc_id,
            "world_id": self.world_id,
            "max_tokens": self.max_tokens,
            "current_tokens": self.current_tokens,
            "usage_ratio": self.usage_ratio,
            "message_count": self.message_count,
            "system_prompt_tokens": self._system_prompt_tokens,
            "graphize_threshold": self.graphize_threshold,
            "should_graphize": self.should_graphize,
            "total_messages_processed": self._total_messages_processed,
            "total_messages_graphized": self._total_messages_graphized,
        }

    def __repr__(self) -> str:
        return (
            f"ContextWindow(npc_id={self.npc_id}, "
            f"tokens={self.current_tokens}/{self.max_tokens}, "
            f"messages={self.message_count})"
        )
