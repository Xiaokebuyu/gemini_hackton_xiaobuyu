"""
战斗会话数据模型
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from .combatant import Combatant
from .action import ActionResult


class CombatState(str, Enum):
    """战斗状态"""

    IDLE = "idle"  # 空闲（未开始）
    INITIALIZED = "initialized"  # 已初始化（先攻已骰）
    IN_PROGRESS = "in_progress"  # 进行中
    WAITING_PLAYER_INPUT = "waiting_player_input"  # 等待玩家输入
    ENDED = "ended"  # 已结束


class CombatEndReason(str, Enum):
    """战斗结束原因"""

    VICTORY = "victory"  # 胜利
    DEFEAT = "defeat"  # 失败
    FLED = "fled"  # 逃跑成功
    SPECIAL = "special"  # 特殊结束（预留）


@dataclass
class CombatLogEntry:
    """战斗日志条目"""

    round: int
    actor_id: str
    message: str
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {"round": self.round, "actor": self.actor_id, "message": self.message}


@dataclass
class CombatLogEvent:
    """结构化战斗事件"""

    seq: int
    round: int
    actor_id: str
    event_type: str
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    action_id: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "round": self.round,
            "actor": self.actor_id,
            "event_type": self.event_type,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "action_id": self.action_id,
            "payload": self.payload,
        }


@dataclass
class TurnRequest:
    """回合请求（用于外部调度LLM）"""

    seq: int
    round: int
    actor_id: str
    status: str = "pending"
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "round": self.round,
            "actor": self.actor_id,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class CombatSession:
    """
    战斗会话

    包含一场战斗的所有状态和历史
    """

    # ===== 基础信息 =====
    combat_id: str
    state: CombatState = CombatState.IDLE

    # ===== 战斗单位 =====
    combatants: List[Combatant] = field(default_factory=list)

    # ===== 行动顺序 =====
    turn_order: List[str] = field(default_factory=list)  # 按先攻排序的ID列表
    current_turn_index: int = 0
    current_round: int = 1

    # ===== 环境（预留） =====
    environment: Optional[Dict[str, Any]] = None
    # 示例：{"type": "cave", "modifiers": {"darkness": -2}}

    # ===== 距离系统 =====
    spatial: Optional[Any] = field(default=None, repr=False, compare=False)

    # ===== 战斗日志 =====
    combat_log: List[CombatLogEntry] = field(default_factory=list)
    event_log: List[CombatLogEvent] = field(default_factory=list)
    event_seq: int = 0
    event_sink: Optional[Callable[[CombatLogEvent], None]] = field(
        default=None, repr=False, compare=False
    )

    # ===== 回合请求队列 =====
    turn_requests: List[TurnRequest] = field(default_factory=list)
    turn_request_seq: int = 0
    turn_actor_id: Optional[str] = None

    # ===== 战斗结果（结束后填充） =====
    end_reason: Optional[CombatEndReason] = None

    # ===== 便捷方法 =====

    def get_combatant(self, combatant_id: str) -> Optional[Combatant]:
        """根据ID获取战斗单位"""
        for combatant in self.combatants:
            if combatant.id == combatant_id:
                return combatant
        return None

    def get_current_actor(self) -> Optional[Combatant]:
        """获取当前回合的行动者"""
        if not self.turn_order:
            return None
        actor_id = self.turn_order[self.current_turn_index]
        return self.get_combatant(actor_id)

    def get_alive_combatants(self, combatant_type=None) -> List[Combatant]:
        """
        获取存活的战斗单位

        Args:
            combatant_type: 过滤类型（可选）
        """
        alive = [c for c in self.combatants if c.is_alive]

        if combatant_type:
            alive = [c for c in alive if c.combatant_type == combatant_type]

        return alive

    def get_player(self) -> Optional[Combatant]:
        """获取玩家（假设只有一个玩家）"""
        from .combatant import CombatantType

        players = self.get_alive_combatants(CombatantType.PLAYER)
        return players[0] if players else None

    def get_enemies(self) -> List[Combatant]:
        """获取所有存活的敌人"""
        from .combatant import CombatantType

        return self.get_alive_combatants(CombatantType.ENEMY)

    def advance_turn(self):
        """推进到下一个回合"""
        self.current_turn_index += 1

        # 如果所有人都行动完毕，进入下一轮
        if self.current_turn_index >= len(self.turn_order):
            self.current_turn_index = 0
            self.current_round += 1
            self._on_round_end()

        # 跳过死亡单位
        visited = 0
        while self.turn_order and visited < len(self.turn_order):
            actor_id = self.turn_order[self.current_turn_index]
            combatant = self.get_combatant(actor_id)
            if combatant and combatant.is_alive:
                break
            self.current_turn_index = (self.current_turn_index + 1) % len(self.turn_order)
            visited += 1

    def _on_round_end(self):
        """回合结束时的处理"""
        # 预留：回合结束处理（环境效果等）
        return

    def set_event_sink(self, sink: Optional[Callable[[CombatLogEvent], None]]):
        """设置事件输出回调（用于推送前端）"""
        self.event_sink = sink

    def add_event(
        self,
        actor_id: str,
        message: str,
        event_type: str = "log",
        payload: Optional[Dict[str, Any]] = None,
        action_id: Optional[str] = None,
    ) -> CombatLogEvent:
        """添加结构化事件"""
        self.event_seq += 1
        event = CombatLogEvent(
            seq=self.event_seq,
            round=self.current_round,
            actor_id=actor_id,
            event_type=event_type,
            message=message,
            action_id=action_id,
            payload=payload,
        )
        self.event_log.append(event)
        if self.event_sink:
            self.event_sink(event)
        return event

    def add_log(
        self,
        actor_id: str,
        message: str,
        event_type: str = "log",
        payload: Optional[Dict[str, Any]] = None,
        action_id: Optional[str] = None,
    ):
        """添加战斗日志"""
        entry = CombatLogEntry(round=self.current_round, actor_id=actor_id, message=message)
        self.combat_log.append(entry)
        self.add_event(
            actor_id=actor_id,
            message=message,
            event_type=event_type,
            payload=payload,
            action_id=action_id,
        )

    def get_distance_band(self, source_id: str, target_id: str) -> Optional[str]:
        """获取抽象距离段位"""
        if not self.spatial:
            return None
        return self.spatial.get_distance(source_id, target_id).value

    def enqueue_turn_request(self, actor_id: str) -> TurnRequest:
        """添加回合请求（用于调度外部LLM）"""
        self.turn_request_seq += 1
        request = TurnRequest(
            seq=self.turn_request_seq,
            round=self.current_round,
            actor_id=actor_id,
        )
        self.turn_requests.append(request)
        self.add_event(
            actor_id=actor_id,
            message=f"turn_request:{actor_id}",
            event_type="turn_request",
            payload={"actor_id": actor_id, "seq": request.seq},
        )
        return request

    def mark_turn_request_handled(self, actor_id: str) -> Optional[TurnRequest]:
        """标记某角色的回合请求为已处理"""
        for request in self.turn_requests:
            if request.actor_id == actor_id and request.status == "pending":
                request.status = "handled"
                return request
        return None

    def get_pending_turn_requests(self, since_seq: int = 0) -> List[TurnRequest]:
        """获取未处理的回合请求"""
        return [
            request
            for request in self.turn_requests
            if request.seq > since_seq and request.status == "pending"
        ]

    def get_event_log_since(self, since_seq: int = 0, limit: int = 100) -> List[CombatLogEvent]:
        """获取事件日志"""
        events = [event for event in self.event_log if event.seq > since_seq]
        return events[:limit]

    def check_combat_end(self) -> Optional[CombatEndReason]:
        """
        检查战斗是否结束

        Returns:
            Optional[CombatEndReason]: 如果战斗结束返回原因，否则None
        """
        from .combatant import CombatantType

        # 检查玩家是否死亡
        player = self.get_player()
        if not player or not player.is_alive:
            return CombatEndReason.DEFEAT

        # 检查敌人是否全灭
        enemies = self.get_enemies()
        if not enemies:
            return CombatEndReason.VICTORY

        return None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "combat_id": self.combat_id,
            "state": self.state.value,
            "round": self.current_round,
            "current_turn": self.turn_order[self.current_turn_index]
            if self.turn_order
            else None,
            "combatants": [c.to_dict() for c in self.combatants],
            "combat_log": [entry.to_dict() for entry in self.combat_log[-10:]],
            "end_reason": self.end_reason.value if self.end_reason else None,
        }
