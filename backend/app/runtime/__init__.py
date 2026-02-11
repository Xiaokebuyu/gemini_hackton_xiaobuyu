"""
V4 Runtime Layer — 世界模型与管线分离架构。

核心组件：
- GameRuntime: 全局单例，管理 WorldInstance 生命周期
- WorldInstance: 世界级静态数据注册表（角色/区域/章节/实体）
- SessionRuntime: 会话级状态统一层（玩家/队伍/时间/叙事）
- AreaRuntime: 区域生命周期管理（事件状态机/NPC 上下文/访问摘要）
- ContextAssembler: 分层上下文组装器（Layer 0-4 + Memory）
"""

from app.runtime.game_runtime import GameRuntime
from app.runtime.world_instance import WorldInstance
from app.runtime.session_runtime import SessionRuntime
from app.runtime.area_runtime import AreaRuntime
from app.runtime.context_assembler import ContextAssembler

__all__ = [
    "GameRuntime",
    "WorldInstance",
    "SessionRuntime",
    "AreaRuntime",
    "ContextAssembler",
]
