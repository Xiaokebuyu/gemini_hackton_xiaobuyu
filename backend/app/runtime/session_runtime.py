"""SessionRuntime — 会话级状态统一层（Phase 2A 实现）。

包装现有服务（StateManager / PartyService / NarrativeService / SessionHistory），
提供统一的会话状态访问与生命周期管理。初始阶段采用委托模式，不修改原有服务。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, ClassVar, Dict, List, Optional, Set, Tuple

from pydantic import ValidationError

from app.exceptions import FirestoreIOError, SessionRestoreError
from app.models.narrative import NarrativeProgress
from app.models.party import Party
from app.models.player_character import PlayerCharacter
from app.models.state_delta import GameState, GameTimeState, StateDelta
logger = logging.getLogger(__name__)


class SessionRuntime:
    """统一会话状态管理。

    整合 StateManager + PartyService + TimeManager + NarrativeService 进度追踪。
    初始阶段包装现有服务，逐步替换内部实现。

    Phase 2A 完整实现。

    Usage::

        session = SessionRuntime(world_id, session_id, world=world_instance)
        await session.restore()          # 从 Firestore 恢复
        ...
        await session.enter_area("town_square")
        ...
        await session.persist()          # 统一持久化
    """

    # -- 会话缓存（路由层 + Pipeline 共享，避免重复 restore） --
    _cache: ClassVar[Dict[Tuple[str, str], "SessionRuntime"]] = {}

    @classmethod
    async def get_or_restore(cls, world_id: str, session_id: str) -> "SessionRuntime":
        """获取已缓存的 SessionRuntime，或创建并 restore 一个新的。"""
        key = (world_id, session_id)
        cached = cls._cache.get(key)
        if cached and cached._restored:
            return cached
        from app.runtime.game_runtime import GameRuntime
        rt = await GameRuntime.get_instance()
        world = await rt.get_world(world_id)
        session = cls(world_id=world_id, session_id=session_id, world=world)
        await session.restore()
        cls._cache[key] = session
        return session

    @classmethod
    def invalidate_cache(cls, world_id: str, session_id: str) -> None:
        """persist 后调用，清除过期缓存。"""
        cls._cache.pop((world_id, session_id), None)

    @classmethod
    def cache_session(cls, session: "SessionRuntime") -> None:
        """Pipeline 创建的 session 主动注入缓存，路由层直接复用。"""
        cls._cache[(session.world_id, session.session_id)] = session

    def __init__(
        self,
        world_id: str,
        session_id: str,
        world: Optional[Any] = None,
        *,
        state_manager: Optional[Any] = None,
        party_service: Optional[Any] = None,
        narrative_service: Optional[Any] = None,
        session_history_manager: Optional[Any] = None,
        session_store: Optional[Any] = None,
        character_store: Optional[Any] = None,  # 已废弃，保留签名兼容旧调用方
    ) -> None:
        self.world_id = world_id
        self.session_id = session_id
        self.world = world  # WorldInstance (Phase 1)

        # -- 包装的现有服务引用 --
        self._state_manager = state_manager
        self._party_service = party_service
        self._narrative_service = narrative_service
        self._session_history_manager = session_history_manager
        self._session_store = session_store

        # -- 状态组件（restore() 填充） --
        self.game_state: Optional[GameState] = None
        self._player_character: Optional[PlayerCharacter] = None  # 初始种子，图构建后由 PlayerNodeView 替代
        self.party: Optional[Party] = None
        self.time: Optional[GameTimeState] = None
        self.narrative: Optional[NarrativeProgress] = None
        self.history: Optional[Any] = None  # SessionHistory
        self.companions: Dict[str, Any] = {}  # 空 dict，event_machine 引用安全
        self.current_area = None  # 已废弃，保留字段避免 getattr 报错

        # -- WorldGraph (C7) --
        self.world_graph: Optional[Any] = None       # WorldGraph
        self._behavior_engine: Optional[Any] = None  # BehaviorEngine
        # _world_graph_failed 已移除 — 严格模式下构建失败即 raise
        self._applied_side_effect_events: Set[str] = set()  # C8: 去重，防止副作用重复发放
        self.delta_log: List[StateDelta] = []

        # -- E3: pending_flash 闭环 --
        self.flash_results: Dict[str, bool] = {}
        """LLM 通过 report_flash_evaluation 写入，post-tick 后清空。"""

        # -- SceneBus (Direction A) --
        self.scene_bus: Optional[Any] = None

        # -- 脏标记（persist 时只保存有变更的部分） --
        self._dirty_game_state: bool = False
        self._dirty_party: bool = False
        self._dirty_narrative: bool = False
        self._dirty_player: bool = False

        self._restored: bool = False

        # -- 提取模块 (P5+P7) --
        from app.runtime.event_machine import EventMachine
        from app.runtime.memory_ops import MemoryOps
        from app.runtime.narrative_ops import NarrativeOps
        from app.runtime.stat_ops import StatOps

        self._events = EventMachine(self)
        self._memory_ops = MemoryOps(self)
        self._narrative_ops = NarrativeOps(self)
        self._stat_ops = StatOps(self)

    # =========================================================================
    # 属性便捷访问
    # =========================================================================

    @property
    def player_location(self) -> Optional[str]:
        """当前玩家位置（委托 GameState）。"""
        return self.game_state.player_location if self.game_state else None

    @property
    def sub_location(self) -> Optional[str]:
        """当前子地点（委托 GameState）。"""
        return self.game_state.sub_location if self.game_state else None

    @property
    def chapter_id(self) -> Optional[str]:
        """当前章节 ID。"""
        return self.game_state.chapter_id if self.game_state else None

    @property
    def area_id(self) -> Optional[str]:
        """当前区域 ID。"""
        return self.game_state.area_id if self.game_state else None

    @property
    def player(self) -> Optional[Any]:
        """运行时玩家数据视图。

        优先从 WorldGraph 返回 PlayerNodeView（图为唯一真理源）；
        图不可用时降级返回 _player_character（初始种子）。
        """
        if self.world_graph:
            node = self.world_graph.get_node("player")
            if node is not None:
                from app.world.player.node_view import PlayerNodeView
                return PlayerNodeView(node, self.world_graph)
        return self._player_character

    @player.setter
    def player(self, value: Any) -> None:
        """兼容旧代码直接赋值 session.player = xxx 的写法。"""
        self._player_character = value

    @property
    def is_restored(self) -> bool:
        """是否已完成 restore()。"""
        return self._restored

    @property
    def degradation_info(self) -> Dict[str, Any]:
        """降级状态摘要，供下游诊断和前端展示。"""
        return {
            "has_player": self._player_character is not None,
            "has_party": self.party is not None,
            "has_narrative": self.narrative is not None,
            "has_area": self.current_area is not None,
            "world_graph_ok": self.world_graph is not None,
        }

    # =========================================================================
    # restore — 从 Firestore 恢复会话状态
    # =========================================================================

    async def restore(self) -> None:
        """从 Firestore 恢复完整会话状态（分波并行）。

        Wave 1（并行）：GameState + Party + Narrative（主路径）
        Wave 1（内联）：SessionHistory（同步）
        Wave 2（串行）：时间提取 → Narrative fallback → SceneBus → WorldGraph（player 从快照恢复）
        """
        logger.info(
            "[SessionRuntime] restore 开始: world=%s session=%s",
            self.world_id,
            self.session_id,
        )

        # ── Wave 1: 独立数据源并行加载 ──
        wave1_tasks = [
            self._restore_game_state(),
            self._restore_party(),
        ]
        # Narrative 主路径（有 _narrative_service 时）才加入并行
        has_narrative_service = self._narrative_service is not None
        if has_narrative_service:
            wave1_tasks.append(self._restore_narrative())

        results = await asyncio.gather(*wave1_tasks, return_exceptions=True)
        wave1_names = ["game_state", "party"]
        if has_narrative_service:
            wave1_names.append("narrative")
        errors = []
        for i, result in enumerate(results):
            name = wave1_names[i] if i < len(wave1_names) else f"task_{i}"
            if isinstance(result, Exception):
                errors.append((name, result))
        if errors:
            names = [n for n, _ in errors]
            raise SessionRestoreError(
                f"会话恢复失败 [{', '.join(names)}]"
            ) from errors[0][1]

        # SessionHistory（同步，无 I/O）
        self._restore_history()

        # ── Wave 2: 依赖 game_state / narrative 的串行步骤 ──
        # Narrative fallback（无 _narrative_service 时从 game_state 读取）
        if not has_narrative_service:
            await self._restore_narrative()

        # 时间快照（依赖 game_state）
        if self.game_state:
            self.time = self.game_state.game_time

            # 2.3: 恢复副作用去重集合（crash recovery 安全）
            saved_dedup = self.game_state.metadata.get("_applied_side_effects")
            if isinstance(saved_dedup, list):
                self._applied_side_effect_events = set(saved_dedup[-200:])

        # SceneBus 挂载（依赖 game_state.player_location）
        await self._restore_area()

        # WorldGraph (C7a): 构建 + 恢复快照
        self._build_world_graph()
        await self._restore_world_graph_snapshot()

        self._restored = True

        # ── restore 摘要日志 ──
        failed = []
        if not self._player_character:
            failed.append("player")
        if not self.party:
            failed.append("party")
        if not self.narrative:
            failed.append("narrative")
        if not self.current_area:
            failed.append("area")

        if failed:
            logger.warning(
                "[SessionRuntime] restore 完成(降级): location=%s chapter=%s "
                "failed_components=%s party_size=%d",
                self.player_location,
                self.chapter_id,
                failed,
                len(self.party.members) if self.party else 0,
            )
        else:
            logger.info(
                "[SessionRuntime] restore 完成: location=%s chapter=%s "
                "party_size=%d graph=%s",
                self.player_location,
                self.chapter_id,
                len(self.party.members) if self.party else 0,
                "ok" if self.world_graph else "disabled",
            )

    async def _restore_game_state(self) -> None:
        """加载 GameState（StateManager 缓存 → 空初始化）。"""
        # 1. StateManager 缓存
        if self._state_manager:
            cached = await self._state_manager.get_state(
                self.world_id, self.session_id
            )
            if cached:
                self.game_state = cached
                return
        # 2. 空初始化
        self.game_state = GameState(
            world_id=self.world_id, session_id=self.session_id
        )
        if self._state_manager:
            await self._state_manager.set_state(
                self.world_id, self.session_id, self.game_state
            )

    async def _restore_party(self) -> None:
        """加载 Party。失败直接传播到 gather → SessionRestoreError。"""
        if self._party_service:
            self.party = await self._party_service.get_party(
                self.world_id, self.session_id
            )
        else:
            self.party = None

    async def _restore_narrative(self) -> None:
        """加载 NarrativeProgress。失败直接传播到 gather → SessionRestoreError。"""
        if self._narrative_service:
            self.narrative = await self._narrative_service.get_progress(
                self.world_id, self.session_id
            )

    def _restore_history(self) -> None:
        """加载 SessionHistory。"""
        if self._session_history_manager:
            self.history = self._session_history_manager.get_or_create(
                self.world_id, self.session_id
            )
        else:
            self.history = None

    async def _restore_area(self) -> None:
        """恢复区域上下文 — SceneBus 挂载。"""
        if not self.player_location:
            return
        self._init_scene_bus()

    def _init_scene_bus(self) -> None:
        """创建 SceneBus（含常驻成员）。"""
        area_id = self.player_location
        if not area_id:
            return
        from app.world.scene import SceneBus
        permanent = {"player"}
        if self.party:
            for m in self.party.get_active_members():
                permanent.add(m.character_id)
        self.scene_bus = SceneBus(
            area_id=area_id,
            sub_location=self.sub_location,
            permanent_members=permanent,
        )

    # =========================================================================
    # WorldGraph (C7) — 构建 + 快照 I/O
    # =========================================================================

    def _build_world_graph(self) -> None:
        """从 WorldInstance + 当前会话状态构建 WorldGraph（同步）。"""
        from app.config import settings
        if not settings.world_graph_enabled or not self.world:
            return
        try:
            from app.world.graph.builder import GraphBuilder
            from app.world.events.behavior_engine import BehaviorEngine
            wg = GraphBuilder.build(self.world, self)
            self.world_graph = wg
            self._behavior_engine = BehaviorEngine(wg)
            stats = wg.stats()
            logger.info("[SessionRuntime] WorldGraph built: %s", stats)
        except (KeyError, ValueError, RuntimeError) as exc:
            raise SessionRestoreError(f"WorldGraph 构建失败: {exc}") from exc

    async def _restore_world_graph_snapshot(self) -> None:
        """从 Firestore 加载快照并恢复到 world_graph。
        路径: worlds/{wid}/sessions/{sid}/world_snapshot/current
        失败直接传播 SessionRestoreError / WorldGraphError。
        """
        if not self.world_graph:
            return
        from google.cloud import firestore as fs
        from app.config import settings
        from app.world.graph.snapshot import dict_to_snapshot, restore_snapshot
        db = fs.Client(database=settings.firestore_database)
        doc = (db.collection("worlds").document(self.world_id)
               .collection("sessions").document(self.session_id)
               .collection("world_snapshot").document("current").get())
        if not doc.exists:
            logger.info("[SessionRuntime] 无 WorldGraph 快照（首次会话）")
            return
        snapshot = dict_to_snapshot(doc.to_dict())
        restore_snapshot(self.world_graph, snapshot)
        logger.info("[SessionRuntime] WorldGraph 快照恢复: %d states, %d spawned",
                    len(snapshot.node_states), len(snapshot.spawned_nodes))

    async def _persist_world_graph_snapshot(self) -> None:
        """保存 WorldGraph 快照到 Firestore。失败 raise FirestoreIOError。"""
        if not self.world_graph:
            return
        try:
            from google.cloud import firestore as fs
            from app.config import settings
            from app.world.graph.snapshot import capture_snapshot, snapshot_to_dict
            game_day = self.time.day if self.time else 1
            game_hour = self.time.hour if self.time else 8
            snapshot = capture_snapshot(
                self.world_graph, self.world_id, self.session_id,
                game_day=game_day, game_hour=game_hour,
            )
            data = snapshot_to_dict(snapshot)
            # L3 M3: 快照大小监控
            import json as _json
            try:
                snapshot_size = len(_json.dumps(data, default=str).encode("utf-8"))
                if snapshot_size > 500_000:
                    logger.warning(
                        "[SessionRuntime] WorldGraph 快照超过 500KB: %d bytes (states=%d spawned=%d edges=%d)",
                        snapshot_size, len(snapshot.node_states),
                        len(snapshot.spawned_nodes), len(snapshot.modified_edges),
                    )
            except (TypeError, ValueError, OverflowError) as exc:
                logger.debug("[SessionRuntime] 快照大小检测失败: %s", exc)
            db = fs.Client(database=settings.firestore_database)
            (db.collection("worlds").document(self.world_id)
             .collection("sessions").document(self.session_id)
             .collection("world_snapshot").document("current").set(data))
            self.world_graph.clear_dirty()
            logger.info("[SessionRuntime] WorldGraph 快照保存: %d states, %d spawned, %d edges",
                        len(snapshot.node_states), len(snapshot.spawned_nodes),
                        len(snapshot.modified_edges))
        except (OSError, KeyError, ValueError, RuntimeError) as exc:
            raise FirestoreIOError(f"WorldGraph 快照保存失败: {exc}") from exc

    # =========================================================================
    # Delegates — Event Machine (tick / 状态机 / 奖励 / 同伴分发)
    # =========================================================================

    def build_tick_context(self, phase: str = "pre") -> Optional[Any]:
        return self._events.build_tick_context(phase)

    def run_behavior_tick(self, phase: str = "pre") -> Optional[Any]:
        return self._events.run_behavior_tick(phase)

    def _sync_tick_to_narrative(self, tick_result: Any) -> None:
        self._events._sync_tick_to_narrative(tick_result)

    def _apply_tick_side_effects(self, tick_result: Any) -> None:
        self._events._apply_tick_side_effects(tick_result)

    def _dispatch_completed_events_to_companions(self, tick_result: Any) -> None:
        self._events._dispatch_completed_events_to_companions(tick_result)

    def check_chapter_transitions(self) -> Optional[Dict[str, Any]]:
        return self._events.check_chapter_transitions()

    def get_event_summaries_from_graph(self, area_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._events.get_event_summaries_from_graph(area_id)

    # =========================================================================
    # enter_area — 完整区域切换生命周期
    # =========================================================================

    async def enter_area(self, area_id: str) -> Dict[str, Any]:
        """进入区域 — 更新状态 + 同步队伍 + 切换 SceneBus。"""
        old_area_id = self.player_location
        area_def = self.world.get_area_definition(area_id) if self.world else None

        # 1. 更新 GameState
        if self.game_state:
            self.game_state.player_location = area_id
            self.game_state.area_id = area_id
            self.game_state.sub_location = None
            self._dirty_game_state = True

        # 4. 同步队伍位置（纯内存）
        if self._party_service:
            await self._party_service.sync_locations(
                self.world_id, self.session_id, area_id, None
            )

        # 5. 同步到 StateManager
        if self._state_manager and self.game_state:
            await self._state_manager.set_state(
                self.world_id, self.session_id, self.game_state
            )

        # 6. SceneBus 切换
        if self.scene_bus:
            self.scene_bus.clear()
        from app.world.scene import SceneBus
        permanent = {"player"}
        if self.party:
            for m in self.party.get_active_members():
                permanent.add(m.character_id)
        self.scene_bus = SceneBus(area_id=area_id, permanent_members=permanent)

        logger.info(
            "[SessionRuntime] enter_area: %s → %s", old_area_id, area_id
        )

        return {
            "success": True,
            "previous_area": old_area_id,
            "new_area": area_id,
            "area_name": area_def.name if area_def else area_id,
            "visit_summary": None,
        }

    # =========================================================================
    # enter_sublocation / leave_sublocation
    # =========================================================================

    async def enter_sublocation(self, sub_id: str) -> Dict[str, Any]:
        """进入子地点（直接更新状态，persist() 统一持久化）。"""
        if not self.game_state or not self.game_state.player_location:
            return {"success": False, "error": "当前位置未知"}

        self.game_state.sub_location = sub_id
        self._dirty_game_state = True
        if self.scene_bus:
            self.scene_bus.sub_location = sub_id
            self.scene_bus.active_members.clear()  # P3-B: 子地点切换清空对话

        return {
            "success": True,
            "sub_location": sub_id,
        }

    async def leave_sublocation(self) -> Dict[str, Any]:
        """离开子地点（直接更新状态，persist() 统一持久化）。"""
        if not self.game_state:
            return {"success": False, "error": "游戏状态未初始化"}

        if not self.game_state.sub_location:
            return {"success": False, "error": "当前不在子地点"}

        old_sub = self.game_state.sub_location
        self.game_state.sub_location = None
        self._dirty_game_state = True
        if self.scene_bus:
            self.scene_bus.sub_location = None
            self.scene_bus.active_members.clear()  # P3-B: 子地点切换清空对话

        return {
            "success": True,
            "left_sub_location": old_sub,
        }

    # =========================================================================
    # persist — 统一持久化
    # =========================================================================

    async def persist(self) -> None:
        """统一持久化所有脏状态到 Firestore。

        只保存有变更的部分（通过脏标记追踪）。
        """
        persisted: List[str] = []

        # 2.3: 副作用去重持久化（crash recovery 安全）
        if self._applied_side_effect_events and self.game_state:
            if self.game_state.metadata is None:
                self.game_state.metadata = {}
            dedup_list = sorted(self._applied_side_effect_events)
            if len(dedup_list) > 200:
                dedup_list = dedup_list[:200]
            self.game_state.metadata["_applied_side_effects"] = dedup_list
            self._dirty_game_state = True

        # 1. GameState — 内联持久化（StateManager 缓存 + Firestore）
        if self._dirty_game_state and self.game_state:
            if self._state_manager:
                await self._state_manager.set_state(
                    self.world_id, self.session_id, self.game_state
                )
            # Firestore 持久化：优先用 session_store，否则直接写
            if self._session_store:
                await self._session_store.update_session(
                    self.world_id, self.session_id,
                    {
                        "metadata.admin_state": self.game_state.model_dump(),
                        "metadata.has_character": self.player is not None,
                    },
                )
            else:
                try:
                    from google.cloud import firestore as _fs
                    from app.config import settings as _cfg
                    from datetime import datetime
                    _db = _fs.Client(database=_cfg.firestore_database)
                    _db.collection("worlds").document(self.world_id)\
                       .collection("sessions").document(self.session_id)\
                       .update({
                           "metadata.admin_state": self.game_state.model_dump(),
                           "metadata.has_character": self.player is not None,
                           "updated_at": datetime.now(),
                       })
                except (OSError, RuntimeError) as exc:
                    raise FirestoreIOError(f"GameState 持久化失败: {exc}") from exc
            self._dirty_game_state = False
            persisted.append("game_state")

        # 2. NarrativeProgress
        if self._dirty_narrative and self.narrative and self._narrative_service:
            await self._narrative_service.save_progress(
                self.world_id, self.session_id, self.narrative
            )
            self._dirty_narrative = False
            persisted.append("narrative")

        # 3. PlayerCharacter — 延迟清除脏标记（步骤 7 后处理）
        player_was_dirty = self._dirty_player

        # 4. Party — PartyService 内部已实时写入 Firestore，
        #    这里标记重置即可
        if self._dirty_party:
            self._dirty_party = False
            persisted.append("party")

        # 7a. WorldGraph 快照（主路径，失败 raise FirestoreIOError）
        if self.world_graph:
            await self._persist_world_graph_snapshot()
            persisted.append("world_graph")

        # 7b. Player 脏标记清除
        if player_was_dirty:
            self._dirty_player = False
            persisted.append("player")

        if persisted:
            logger.info(
                "[SessionRuntime] persist 完成: %s", ", ".join(persisted)
            )

    # =========================================================================
    # 状态变更辅助方法
    # =========================================================================

    def mark_game_state_dirty(self) -> None:
        """外部修改 game_state 后调用此方法标记脏。"""
        self._dirty_game_state = True

    def mark_narrative_dirty(self) -> None:
        """外部修改 narrative 后调用此方法标记脏。"""
        self._dirty_narrative = True

    def mark_player_dirty(self) -> None:
        """外部修改 player 后调用此方法标记脏。

        同时标记 WorldGraph 脏节点，确保 snapshot 捕获。
        """
        self._dirty_player = True
        if self.world_graph:
            self.world_graph._dirty_nodes.add("player")

    def apply_delta(self, delta: StateDelta) -> None:
        """记录状态变更到 delta_log。"""
        self.delta_log.append(delta)

    def update_time(self, game_time: GameTimeState) -> None:
        """更新时间并同步到 GameState。"""
        self.time = game_time
        if self.game_state:
            self.game_state.game_time = game_time
            self._dirty_game_state = True

    def advance_time(self, minutes: int) -> Dict[str, Any]:
        """推进游戏时间（TimeManager 版本）。

        使用 TimeManager.tick() 计算新时间 + 触发事件（时段/日期变化），
        同步到 GameState.game_time 并标记脏。
        Firestore 持久化由 persist() 统一处理。
        """
        if not self.time:
            return {"success": False, "error": "time not initialized"}

        from app.world.time import TimeManager
        from app.models.state_delta import GameTimeState

        tm = TimeManager.from_dict(self.time.model_dump())
        events = tm.tick(minutes)
        new_time = GameTimeState(**tm.to_dict())
        self.update_time(new_time)  # 内部已标记 _dirty_game_state

        return {
            "success": True,
            "time": tm.to_dict(),
            "events": [
                {"event_type": e.event_type, "description": e.description, "data": e.data}
                for e in events
            ],
        }

    # =========================================================================
    # Delegates — Stat Ops
    # =========================================================================

    def heal(self, amount: int) -> Dict[str, Any]:
        return self._stat_ops.heal(amount)

    def damage(self, amount: int) -> Dict[str, Any]:
        return self._stat_ops.damage(amount)

    def add_xp(self, amount: int) -> Dict[str, Any]:
        return self._stat_ops.add_xp(amount)

    def add_gold(self, amount: int) -> Dict[str, Any]:
        return self._stat_ops.add_gold(amount)

    def add_item(self, item_id: str, item_name: str, quantity: int = 1) -> Dict[str, Any]:
        return self._stat_ops.add_item(item_id, item_name, quantity)

    def remove_item(self, item_id: str, quantity: int = 1) -> Dict[str, Any]:
        return self._stat_ops.remove_item(item_id, quantity)

    # =========================================================================
    # Delegates — Narrative Ops
    # =========================================================================

    def advance_chapter(self, target_chapter_id: str, transition_type: str = "normal") -> Dict[str, Any]:
        return self._narrative_ops.advance_chapter(target_chapter_id, transition_type)

    def complete_objective(self, objective_id: str) -> Dict[str, Any]:
        return self._narrative_ops.complete_objective(objective_id)

    def update_disposition(self, npc_id: str, deltas: Dict[str, int], reason: str = "") -> Dict[str, Any]:
        return self._narrative_ops.update_disposition(npc_id, deltas, reason)

    def activate_event(self, event_id: str) -> Dict[str, Any]:
        return self._events.activate_event(event_id)

    def complete_event(self, event_id: str, outcome_key: str = "") -> Dict[str, Any]:
        return self._events.complete_event(event_id, outcome_key)

    def fail_event(self, event_id: str, reason: str = "") -> Dict[str, Any]:
        return self._events.fail_event(event_id, reason)

    def advance_stage(self, event_id: str, stage_id: str = "") -> Dict[str, Any]:
        return self._events.advance_stage(event_id, stage_id)

    def complete_event_objective(self, event_id: str, objective_id: str) -> Dict[str, Any]:
        return self._events.complete_event_objective(event_id, objective_id)

    def _apply_rewards(self, **kwargs: Any) -> None:
        self._events._apply_rewards(**kwargs)

    def _apply_on_complete_from_graph(self, on_complete: Optional[Dict[str, Any]], event_id: str, node: Any) -> None:
        self._events._apply_on_complete_from_graph(on_complete, event_id, node)

    def _apply_outcome_rewards(self, outcome: Dict[str, Any], event_id: str, node: Any) -> None:
        self._events._apply_outcome_rewards(outcome, event_id, node)

    def _dispatch_event_to_companions_from_graph(self, event_id: str, node: Any) -> None:
        self._events._dispatch_event_to_companions_from_graph(event_id, node)

    # =========================================================================
    # Delegates — Memory Ops
    # =========================================================================

    async def recall(self, role: str, actor_id: str, seeds: List[str],
                     intent_type: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        return await self._memory_ops.recall(role, actor_id, seeds, intent_type, limit)

    def _check_memory_write_permission(self, role: str, memory_type: str) -> None:
        self._memory_ops._check_memory_write_permission(role, memory_type)

    def record_memory(self, owner_id: str, memory_type: str, name: str, summary: str,
                      importance: float, role: str, **props: Any) -> str:
        return self._memory_ops.record_memory(owner_id, memory_type, name, summary, importance, role, **props)

    async def graphize_messages(self, owner_id: str, messages: List[Any],
                                current_scene: Optional[str] = None, game_day: int = 1) -> Dict[str, Any]:
        return await self._memory_ops.graphize_messages(owner_id, messages, current_scene, game_day)

    # =========================================================================
    # 上下文导出（供 ContextAssembler 消费）
    # =========================================================================

    # =========================================================================
    # Session CRUD 类方法（替代已删除的 GameSessionStore）
    # =========================================================================

    @classmethod
    async def create(
        cls,
        world_id: str,
        session_id: Optional[str] = None,
        participants: Optional[List[str]] = None,
    ) -> "GameSessionState":
        """创建会话文档（Firestore）。

        替代 GameSessionStore.create_session()。
        """
        from google.cloud import firestore as fs
        from app.config import settings as _settings
        from app.models.game import GameSessionState
        from datetime import datetime

        db = fs.Client(database=_settings.firestore_database)
        sessions_ref = db.collection("worlds").document(world_id).collection("sessions")

        if session_id:
            doc = sessions_ref.document(session_id).get()
            if doc.exists:
                raise ValueError(
                    f"session_id '{session_id}' already exists; "
                    "use resume endpoint or a new session_id"
                )
        else:
            for _ in range(8):
                candidate = f"sess_{uuid.uuid4().hex[:8]}"
                doc = sessions_ref.document(candidate).get()
                if not doc.exists:
                    session_id = candidate
                    break
            if not session_id:
                raise RuntimeError("failed to allocate unique session_id")

        state = GameSessionState(
            session_id=session_id,
            world_id=world_id,
            participants=participants or [],
            updated_at=datetime.now(),
        )
        sessions_ref.document(session_id).set(state.model_dump())
        return state

    @classmethod
    async def list_sessions(
        cls,
        world_id: str,
        user_id: Optional[str] = None,
        limit: int = 20,
    ) -> List["GameSessionState"]:
        """列出世界内会话（按更新时间倒序）。

        替代 GameSessionStore.list_sessions()。
        """
        from google.cloud import firestore as fs
        from app.config import settings as _settings
        from app.models.game import GameSessionState
        from datetime import datetime, timezone

        db = fs.Client(database=_settings.firestore_database)
        sessions_ref = db.collection("worlds").document(world_id).collection("sessions")
        query = sessions_ref
        if user_id:
            query = query.where("participants", "array_contains", user_id)

        docs = list(query.stream())
        sessions: List[GameSessionState] = []
        for doc in docs:
            data = doc.to_dict() or {}
            try:
                sessions.append(GameSessionState(**data))
            except (ValidationError, TypeError, KeyError) as exc:
                logger.warning("[SessionRuntime] 会话反序列化跳过 doc=%s: %s", doc.id, exc)
                continue

        def _sort_key(s: GameSessionState) -> datetime:
            updated = s.updated_at
            if isinstance(updated, datetime):
                if updated.tzinfo is None:
                    return updated.replace(tzinfo=timezone.utc)
                return updated
            return datetime.min.replace(tzinfo=timezone.utc)

        sessions.sort(key=_sort_key, reverse=True)
        safe_limit = max(1, min(int(limit), 100))
        return sessions[:safe_limit]

    @classmethod
    async def get_session_meta(
        cls,
        world_id: str,
        session_id: str,
    ) -> Optional["GameSessionState"]:
        """轻量读取会话元数据（不完整 restore）。

        替代 GameSessionStore.get_session()。
        """
        from google.cloud import firestore as fs
        from app.config import settings as _settings
        from app.models.game import GameSessionState

        db = fs.Client(database=_settings.firestore_database)
        doc = (db.collection("worlds").document(world_id)
               .collection("sessions").document(session_id).get())
        if not doc.exists:
            return None
        data = doc.to_dict() or {}
        return GameSessionState(**data)
