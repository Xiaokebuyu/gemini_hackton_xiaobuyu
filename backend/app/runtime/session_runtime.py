"""SessionRuntime — 会话级状态统一层（Phase 2A 实现）。

包装现有服务（StateManager / PartyService / NarrativeService / SessionHistory / CharacterStore），
提供统一的会话状态访问与生命周期管理。初始阶段采用委托模式，不修改原有服务。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Set

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
        character_store: Optional[Any] = None,
        world_runtime: Optional[Any] = None,
        graph_store: Optional[Any] = None,
    ) -> None:
        from app.runtime.area_runtime import AreaRuntime

        self.world_id = world_id
        self.session_id = session_id
        self.world = world  # WorldInstance (Phase 1)

        # -- 包装的现有服务引用 --
        self._state_manager = state_manager
        self._party_service = party_service
        self._narrative_service = narrative_service
        self._session_history_manager = session_history_manager
        self._character_store = character_store
        self._world_runtime = world_runtime
        self._graph_store = graph_store

        # -- 状态组件（restore() 填充） --
        self.game_state: Optional[GameState] = None
        self._player_character: Optional[PlayerCharacter] = None  # 初始种子，图构建后由 PlayerNodeView 替代
        self.party: Optional[Party] = None
        self.time: Optional[GameTimeState] = None
        self.narrative: Optional[NarrativeProgress] = None
        self.history: Optional[Any] = None  # SessionHistory
        self.companions: Dict[str, Any] = {}  # Phase 5
        self.current_area: Optional[AreaRuntime] = None

        # -- WorldGraph (C7) --
        self.world_graph: Optional[Any] = None       # WorldGraph
        self._behavior_engine: Optional[Any] = None  # BehaviorEngine
        self._world_graph_failed: bool = False       # 降级标记
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
        if self.world_graph and not self._world_graph_failed:
            node = self.world_graph.get_node("player")
            if node is not None:
                from app.world.player_node import PlayerNodeView
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
            "world_graph_failed": self._world_graph_failed,
            "companion_count": len(self.companions),
        }

    # =========================================================================
    # restore — 从 Firestore 恢复会话状态
    # =========================================================================

    async def restore(self) -> None:
        """从 Firestore 恢复完整会话状态（分波并行）。

        Wave 1（并行）：GameState + Player + Party + Narrative（主路径）
        Wave 1（内联）：SessionHistory（同步）
        Wave 2（串行）：时间提取 → Narrative fallback → AreaRuntime
        """
        logger.info(
            "[SessionRuntime] restore 开始: world=%s session=%s",
            self.world_id,
            self.session_id,
        )

        # ── Wave 1: 独立数据源并行加载 ──
        wave1_tasks = [
            self._restore_game_state(),
            self._restore_player(),
            self._restore_party(),
        ]
        # Narrative 主路径（有 _narrative_service 时）才加入并行
        has_narrative_service = self._narrative_service is not None
        if has_narrative_service:
            wave1_tasks.append(self._restore_narrative())

        results = await asyncio.gather(*wave1_tasks, return_exceptions=True)
        wave1_names = ["game_state", "player", "party"]
        if has_narrative_service:
            wave1_names.append("narrative")
        for i, result in enumerate(results):
            name = wave1_names[i] if i < len(wave1_names) else f"task_{i}"
            if isinstance(result, Exception):
                logger.error("[SessionRuntime] Wave 1 '%s' 失败: %s", name, result)

        # SessionHistory（同步，无 I/O）
        self._restore_history()

        # ── Wave 2: 依赖 game_state / narrative 的串行步骤 ──
        # Narrative fallback（无 _narrative_service 时从 game_state 读取）
        if not has_narrative_service:
            await self._restore_narrative()

        # 时间快照（依赖 game_state）
        if self.game_state:
            self.time = self.game_state.game_time

        # AreaRuntime（依赖 game_state + narrative + world）
        await self._restore_area()

        # 同伴实例（依赖 party）
        await self._restore_companions()

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
        if self._world_graph_failed:
            failed.append("world_graph")
        if not self.current_area:
            failed.append("area")

        if failed:
            logger.warning(
                "[SessionRuntime] restore 完成(降级): location=%s chapter=%s "
                "failed_components=%s party_size=%d companions=%d",
                self.player_location,
                self.chapter_id,
                failed,
                len(self.party.members) if self.party else 0,
                len(self.companions),
            )
        else:
            logger.info(
                "[SessionRuntime] restore 完成: location=%s chapter=%s "
                "party_size=%d companions=%d graph=%s",
                self.player_location,
                self.chapter_id,
                len(self.party.members) if self.party else 0,
                len(self.companions),
                "ok" if self.world_graph else "disabled",
            )

    async def _restore_game_state(self) -> None:
        """加载 GameState。"""
        if self._world_runtime:
            self.game_state = await self._world_runtime.get_state(
                self.world_id, self.session_id
            )
        elif self._state_manager:
            self.game_state = await self._state_manager.get_state(
                self.world_id, self.session_id
            )
            if self.game_state is None:
                self.game_state = GameState(
                    world_id=self.world_id, session_id=self.session_id
                )
                await self._state_manager.set_state(
                    self.world_id, self.session_id, self.game_state
                )
        else:
            self.game_state = GameState(
                world_id=self.world_id, session_id=self.session_id
            )

    async def _restore_player(self) -> None:
        """加载 PlayerCharacter 初始种子（图构建时翻译为图节点）。"""
        if self._character_store:
            try:
                self._player_character = await self._character_store.get_character(
                    self.world_id, self.session_id
                )
            except Exception as exc:
                logger.warning(
                    "[SessionRuntime] PlayerCharacter 加载失败: %s", exc
                )
                self._player_character = None
        else:
            self._player_character = None

    async def _restore_party(self) -> None:
        """加载 Party。"""
        if self._party_service:
            try:
                self.party = await self._party_service.get_party(
                    self.world_id, self.session_id
                )
            except Exception as exc:
                logger.warning(
                    "[SessionRuntime] Party 加载失败: %s", exc
                )
                self.party = None
        else:
            self.party = None

    async def _restore_narrative(self) -> None:
        """加载 NarrativeProgress。"""
        if self._narrative_service:
            try:
                self.narrative = await self._narrative_service.get_progress(
                    self.world_id, self.session_id
                )
            except Exception as exc:
                logger.warning(
                    "[SessionRuntime] NarrativeProgress 加载失败: %s", exc
                )
                self.narrative = None
        else:
            # 从 GameState.narrative_progress 回退
            if self.game_state and self.game_state.narrative_progress:
                try:
                    self.narrative = NarrativeProgress.from_dict(
                        self.game_state.narrative_progress
                    )
                except Exception:
                    self.narrative = None

    def _restore_history(self) -> None:
        """加载 SessionHistory。"""
        if self._session_history_manager:
            self.history = self._session_history_manager.get_or_create(
                self.world_id, self.session_id
            )
        else:
            self.history = None

    async def _restore_area(self) -> None:
        """如果有当前区域，创建并加载 AreaRuntime。"""
        current_area_id = self.player_location
        if not current_area_id or not self.world:
            return

        area_def = self.world.get_area_definition(current_area_id)
        if not area_def:
            return

        from app.runtime.area_runtime import AreaRuntime

        try:
            area_rt = AreaRuntime(area_id=current_area_id, definition=area_def)
            await area_rt.load(
                self.world_id, self.session_id,
                chapter_id=self.chapter_id,
                graph_store=self._graph_store,
            )
            self.current_area = area_rt
        except NotImplementedError:
            # Phase 2B 尚未实现 load()
            self.current_area = AreaRuntime(
                area_id=current_area_id, definition=area_def
            )
        except Exception as exc:
            logger.warning(
                "[SessionRuntime] AreaRuntime 加载失败: area=%s err=%s",
                current_area_id,
                exc,
            )

        # SceneBus 挂载
        self._init_scene_bus()

    def _init_scene_bus(self) -> None:
        """创建 SceneBus。"""
        area_id = self.player_location
        if not area_id:
            return
        from app.world.scene_bus import SceneBus
        self.scene_bus = SceneBus(
            area_id=area_id,
            sub_location=self.sub_location,
        )

    async def _restore_companions(self) -> None:
        """从 Party 成员创建并加载 CompanionInstance。"""
        if not self.party:
            return

        from app.runtime.companion_instance import CompanionInstance

        for member in self.party.get_active_members():
            companion = CompanionInstance(
                character_id=member.character_id,
                name=member.name,
                world_id=self.world_id,
                session_id=self.session_id,
            )
            try:
                await companion.load()
            except Exception as exc:
                logger.warning(
                    "[SessionRuntime] 同伴 '%s' 加载失败: %s",
                    member.character_id, exc,
                )
                continue
            self.companions[member.character_id] = companion

    # =========================================================================
    # WorldGraph (C7) — 构建 + 快照 I/O
    # =========================================================================

    def _build_world_graph(self) -> None:
        """从 WorldInstance + 当前会话状态构建 WorldGraph（同步）。"""
        from app.config import settings
        if not settings.world_graph_enabled or not self.world:
            return
        try:
            from app.world.graph_builder import GraphBuilder
            from app.world.behavior_engine import BehaviorEngine
            wg = GraphBuilder.build(self.world, self)
            self.world_graph = wg
            self._behavior_engine = BehaviorEngine(wg)
            stats = wg.stats()
            logger.info("[SessionRuntime] WorldGraph built: %s", stats)
        except Exception as exc:
            logger.error("[SessionRuntime] WorldGraph build failed: %s", exc, exc_info=True)
            self._world_graph_failed = True

    async def _restore_world_graph_snapshot(self) -> None:
        """从 Firestore 加载快照并恢复到 world_graph。
        路径: worlds/{wid}/sessions/{sid}/world_snapshot/current
        """
        if not self.world_graph:
            return
        try:
            from google.cloud import firestore as fs
            from app.config import settings
            from app.world.snapshot import dict_to_snapshot, restore_snapshot
            db = fs.Client(database=settings.firestore_database)
            doc = (db.collection("worlds").document(self.world_id)
                   .collection("sessions").document(self.session_id)
                   .collection("world_snapshot").document("current").get())
            if not doc.exists:
                logger.info("[SessionRuntime] 无 WorldGraph 快照，使用干净构建")
                return
            snapshot = dict_to_snapshot(doc.to_dict())
            if snapshot:
                restore_snapshot(self.world_graph, snapshot)
                logger.info("[SessionRuntime] WorldGraph 快照恢复: %d states, %d spawned",
                            len(snapshot.node_states), len(snapshot.spawned_nodes))
        except Exception as exc:
            logger.warning("[SessionRuntime] WorldGraph 快照恢复失败: %s", exc)

    async def _persist_world_graph_snapshot(self) -> None:
        """保存 WorldGraph 快照到 Firestore。"""
        if not self.world_graph or self._world_graph_failed:
            return
        try:
            from google.cloud import firestore as fs
            from app.config import settings
            from app.world.snapshot import capture_snapshot, snapshot_to_dict
            game_day = self.time.day if self.time else 1
            game_hour = self.time.hour if self.time else 8
            snapshot = capture_snapshot(
                self.world_graph, self.world_id, self.session_id,
                game_day=game_day, game_hour=game_hour,
            )
            data = snapshot_to_dict(snapshot)
            db = fs.Client(database=settings.firestore_database)
            (db.collection("worlds").document(self.world_id)
             .collection("sessions").document(self.session_id)
             .collection("world_snapshot").document("current").set(data))
            self.world_graph.clear_dirty()
            logger.info("[SessionRuntime] WorldGraph 快照保存: %d states, %d spawned, %d edges",
                        len(snapshot.node_states), len(snapshot.spawned_nodes),
                        len(snapshot.modified_edges))
        except Exception as exc:
            logger.warning("[SessionRuntime] WorldGraph 快照保存失败: %s", exc)

    def build_tick_context(self, phase: str = "pre") -> Optional[Any]:
        """从当前会话状态构建 TickContext。无 WorldGraph 时返回 None。"""
        if not self.world_graph or self._world_graph_failed:
            return None
        from app.world.models import TickContext
        # 从 world_root 节点读取 world_flags 和 faction_reputations，供条件评估使用
        world_flags: Dict[str, Any] = {}
        faction_reputations: Dict[str, int] = {}
        world_root = self.world_graph.get_node("world_root")
        if world_root:
            world_flags = dict(world_root.state.get("world_flags", {}))
            faction_reputations = dict(world_root.state.get("faction_reputations", {}))
        return TickContext(
            session=self,
            phase=phase,
            player_location=self.player_location or "",
            player_sub_location=self.sub_location or "",
            game_day=self.time.day if self.time else 1,
            game_hour=self.time.hour if self.time else 8,
            active_chapter=self.chapter_id or "",
            party_members=[m.character_id for m in self.party.get_active_members()] if self.party else [],
            events_triggered=list(self.narrative.events_triggered) if self.narrative else [],
            objectives_completed=list(getattr(self.narrative, "objectives_completed", []) or []) if self.narrative else [],
            round_count=getattr(self.narrative, "rounds_in_chapter", 0) if self.narrative else 0,
            npc_interactions=dict(getattr(self.narrative, "npc_interactions", {}) or {}) if self.narrative else {},
            game_state="",
            world_flags=world_flags,
            faction_reputations=faction_reputations,
            flash_results=dict(self.flash_results),
        )

    # =========================================================================
    # BehaviorEngine tick — C8 唯一事件系统
    # =========================================================================

    def run_behavior_tick(self, phase: str = "pre") -> Optional[Any]:
        """BehaviorEngine.tick() + narrative 同步 + 副作用。返回 TickResult 或 None。"""
        if not self._behavior_engine or self._world_graph_failed:
            return None
        ctx = self.build_tick_context(phase)
        if ctx is None:
            return None
        try:
            tick_result = self._behavior_engine.tick(ctx)
            logger.info(
                "[SessionRuntime] tick(%s): %d fired, %d hints, %d events",
                phase, len(tick_result.results), len(tick_result.narrative_hints),
                len(tick_result.all_events),
            )
            self._sync_tick_to_narrative(tick_result)
            self._apply_tick_side_effects(tick_result)
            return tick_result
        except Exception as exc:
            logger.error("[SessionRuntime] tick(%s) failed: %s", phase, exc, exc_info=True)
            return None

    def _sync_tick_to_narrative(self, tick_result: Any) -> None:
        """将 BehaviorEngine 的事件完成同步到 narrative.events_triggered。"""
        if not self.narrative or not self.world_graph:
            return
        from app.world.models import EventStatus
        for nid, changes in tick_result.state_changes.items():
            if changes.get("status") != EventStatus.COMPLETED:
                continue
            node = self.world_graph.get_node(nid)
            if not node or node.type != "event_def":
                continue
            if nid not in self.narrative.events_triggered:
                self.narrative.events_triggered.append(nid)
                self.mark_narrative_dirty()
                logger.info("[SessionRuntime] 同步事件完成: %s", nid)

            # E4: is_repeatable 事件完成 → COOLDOWN（统一路径，覆盖自动完成和工具路径）
            cooldown_key = f"cooldown:{nid}"
            if node.properties.get("is_repeatable") and cooldown_key not in self._applied_side_effect_events:
                cooldown_rounds = node.properties.get("cooldown_rounds", 0)
                current_round = getattr(self.narrative, "rounds_in_chapter", 0)
                if cooldown_rounds > 0:
                    self.world_graph.merge_state(nid, {
                        "status": EventStatus.COOLDOWN,
                        "activated_at_round": current_round,
                    })
                    self.world_graph.reset_behaviors(nid)
                    logger.info("[SessionRuntime] 事件进入冷却: %s (%d 回合)", nid, cooldown_rounds)
                else:
                    # cooldown_rounds=0：直接回 AVAILABLE，避免卡死
                    self.world_graph.merge_state(nid, {
                        "status": EventStatus.AVAILABLE,
                        "activated_at_round": None,
                    })
                    self.world_graph.reset_behaviors(nid)
                    logger.info("[SessionRuntime] 事件直接回 AVAILABLE（cooldown=0）: %s", nid)
                self._applied_side_effect_events.add(cooldown_key)  # 防重复

    def _apply_tick_side_effects(self, tick_result: Any) -> None:
        """从 tick 产出的 WorldEvent 中应用 XP/物品等副作用。

        使用 _applied_side_effect_events 去重，防止工具手动 apply 后
        pipeline post-tick 再次重复发放。
        """
        for event in tick_result.all_events:
            # 双层去重：event_id 精确去重 + blanket_key 跨路径去重
            dedup_key = event.event_id
            blanket_key = f"{event.event_type}:{event.origin_node}"
            if dedup_key in self._applied_side_effect_events or blanket_key in self._applied_side_effect_events:
                continue
            if event.event_type == "xp_awarded":
                amount = event.data.get("amount", 0)
                if amount and self.player and hasattr(self.player, "xp"):
                    self.player.xp = (self.player.xp or 0) + amount
                    self.mark_player_dirty()
                    self._applied_side_effect_events.add(dedup_key)
                    logger.info("[SessionRuntime] 副作用: +%d XP", amount)
            elif event.event_type == "item_granted":
                if self.player:
                    inventory = getattr(self.player, "inventory", None)
                    if inventory is not None and hasattr(inventory, "append"):
                        inventory.append(event.data)
                        self.mark_player_dirty()
                        self._applied_side_effect_events.add(dedup_key)
                        logger.info("[SessionRuntime] 副作用: +物品 %s", event.data)
            elif event.event_type == "gold_awarded":
                amount = event.data.get("amount", 0)
                if amount and self.player and hasattr(self.player, "gold"):
                    self.player.gold = (self.player.gold or 0) + amount
                    self.mark_player_dirty()
                    self._applied_side_effect_events.add(dedup_key)
                    logger.info("[SessionRuntime] 副作用: +%d 金币", amount)
            elif event.event_type == "reputation_changed":
                faction = event.data.get("faction", "")
                delta = event.data.get("delta", 0)
                if faction and delta and self.world_graph and self.world_graph.has_node("world_root"):
                    root = self.world_graph.get_node("world_root")
                    reps = dict(root.state.get("faction_reputations", {})) if root else {}
                    reps[faction] = reps.get(faction, 0) + delta
                    self.world_graph.merge_state("world_root", {"faction_reputations": reps})
                    self._applied_side_effect_events.add(dedup_key)
                    logger.info("[SessionRuntime] 副作用: 声望 %s %+d", faction, delta)
            elif event.event_type == "world_flag_set":
                key = event.data.get("key", "")
                value = event.data.get("value")
                if key and self.world_graph and self.world_graph.has_node("world_root"):
                    root = self.world_graph.get_node("world_root")
                    flags = dict(root.state.get("world_flags", {})) if root else {}
                    flags[key] = value
                    self.world_graph.merge_state("world_root", {"world_flags": flags})
                    self._applied_side_effect_events.add(dedup_key)
                    logger.info("[SessionRuntime] 副作用: 世界标记 %s = %s", key, value)
        # 同伴分发
        self._dispatch_completed_events_to_companions(tick_result)

    def _dispatch_completed_events_to_companions(self, tick_result: Any) -> None:
        """将完成的事件分发到同伴实例。去重防止工具已分发的事件再次发放。"""
        if not self.companions or not self.world_graph:
            return
        from app.runtime.models.companion_state import CompactEvent
        game_day = self.time.day if self.time else 1
        area_id = self.player_location or ""
        from app.world.models import EventStatus
        for nid, changes in tick_result.state_changes.items():
            if changes.get("status") != EventStatus.COMPLETED:
                continue
            # 去重：同一事件只分发一次
            dedup_key = f"companion_dispatch:{nid}"
            if dedup_key in self._applied_side_effect_events:
                continue
            node = self.world_graph.get_node(nid)
            if not node or node.type != "event_def":
                continue
            compact = CompactEvent(
                event_id=nid,
                event_name=node.name,
                summary=node.properties.get("description", node.name),
                area_id=area_id,
                game_day=game_day,
                importance=node.properties.get("importance", "side"),
            )
            for companion in self.companions.values():
                if hasattr(companion, "add_event"):
                    companion.add_event(compact)
            self._applied_side_effect_events.add(dedup_key)

    def check_chapter_transitions(self) -> Optional[Dict[str, Any]]:
        """从 WorldGraph GATE 边评估章节转换（替代 AreaRuntime.check_chapter_transition）。

        只有存在 gate 边且目标章节 status=active 时才返回转换信息。
        多候选时按 priority 降序选取最高优先级。
        """
        if not self.world_graph or not self.narrative:
            return None
        from app.world.models import WorldNodeType
        current_chapter = self.narrative.current_chapter

        candidates: List[Dict[str, Any]] = []
        for ch_id in self.world_graph.get_by_type(WorldNodeType.CHAPTER.value):
            if ch_id == current_chapter:
                continue
            node = self.world_graph.get_node(ch_id)
            if not node or node.state.get("status") != "active":
                continue
            # 必须有 gate 边才算有效转换
            edges = self.world_graph.get_edges_between(current_chapter, ch_id)
            for key, edge_data in edges:
                if edge_data.get("relation") == "gate":
                    candidates.append({
                        "target_chapter_id": ch_id,
                        "transition_type": edge_data.get("transition_type", "normal"),
                        "priority": edge_data.get("priority", 0),
                        "narrative_hint": edge_data.get("narrative_hint", ""),
                    })
                    break  # 一个 ch_id 只取第一条 gate 边

        if not candidates:
            return None
        # 多候选按 priority 降序，取最高
        candidates.sort(key=lambda c: c["priority"], reverse=True)
        return candidates[0]

    def get_event_summaries_from_graph(self, area_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """从 WorldGraph 获取事件概要（替代 AreaRuntime 事件遍历）。"""
        if not self.world_graph:
            return []
        target = area_id or self.player_location
        if not target:
            return []
        summaries: List[Dict[str, Any]] = []
        for eid in self.world_graph.find_events_in_scope(target):
            node = self.world_graph.get_node(eid)
            if not node:
                continue
            from app.world.models import EventStatus as _ES
            status = node.state.get("status", _ES.LOCKED)
            if status not in (_ES.AVAILABLE, _ES.ACTIVE):
                continue
            entry: Dict[str, Any] = {
                "id": node.id,
                "name": node.name,
                "description": node.properties.get("description", ""),
                "status": status,
                "importance": node.properties.get("importance", "side"),
            }
            if node.properties.get("narrative_directive"):
                entry["narrative_directive"] = node.properties["narrative_directive"]
            if status == _ES.ACTIVE:
                # completion_hint
                if node.properties.get("completion_conditions"):
                    from app.runtime.area_runtime import AreaRuntime
                    hint = AreaRuntime._summarize_completion_conditions(
                        node.properties["completion_conditions"]
                    )
                    if hint:
                        entry["completion_hint"] = hint

                # P10: stage 信息
                stages_raw = node.properties.get("stages", [])
                current_stage_id = node.state.get("current_stage")
                if stages_raw and current_stage_id:
                    current_stage = next(
                        (s for s in stages_raw
                         if (s.get("id") if isinstance(s, dict) else getattr(s, "id", None)) == current_stage_id),
                        None,
                    )
                    if current_stage:
                        cs = current_stage if isinstance(current_stage, dict) else current_stage.model_dump()
                        obj_progress = node.state.get("objective_progress", {})
                        entry["current_stage"] = {
                            "id": cs["id"],
                            "name": cs.get("name", ""),
                            "narrative_directive": cs.get("narrative_directive", ""),
                            "objectives": [
                                {
                                    "id": obj["id"],
                                    "text": obj.get("text", ""),
                                    "required": obj.get("required", True),
                                    "completed": obj_progress.get(obj["id"], False),
                                }
                                for obj in cs.get("objectives", [])
                            ],
                        }
                    entry["stage_progress"] = node.state.get("stage_progress", {})

                # P10: outcomes 列表（仅 key + 描述，不暴露条件细节）
                outcomes_raw = node.properties.get("outcomes", {})
                if outcomes_raw:
                    entry["available_outcomes"] = [
                        {
                            "key": k,
                            "description": v.get("description", "") if isinstance(v, dict) else getattr(v, "description", ""),
                        }
                        for k, v in outcomes_raw.items()
                    ]
            summaries.append(entry)
        return summaries

    # =========================================================================
    # enter_area — 完整区域切换生命周期
    # =========================================================================

    async def enter_area(self, area_id: str) -> Dict[str, Any]:
        """进入区域 — 完整区域切换生命周期。

        1. 如果有 current_area → unload 旧区域
        2. 创建新 AreaRuntime → load
        3. 更新 GameState.player_location
        4. 同步队伍位置
        5. 返回区域切换结果
        """
        from app.runtime.area_runtime import AreaRuntime

        old_area_id = self.player_location

        # 1. Unload 旧区域
        visit_summary = None
        if self.current_area:
            try:
                visit_summary = await self.current_area.unload(self)
            except NotImplementedError:
                pass  # Phase 2B
            except Exception as exc:
                logger.warning(
                    "[SessionRuntime] 旧区域 unload 失败: %s", exc
                )
            self.current_area = None

        # 2. 创建并加载新 AreaRuntime
        area_def = self.world.get_area_definition(area_id) if self.world else None
        if area_def:
            new_area = AreaRuntime(area_id=area_id, definition=area_def)
            try:
                await new_area.load(
                    self.world_id, self.session_id,
                    chapter_id=self.chapter_id,
                    graph_store=self._graph_store,
                )
            except NotImplementedError:
                pass  # Phase 2B
            except Exception as exc:
                logger.warning(
                    "[SessionRuntime] 新区域 load 失败: area=%s err=%s",
                    area_id,
                    exc,
                )
            self.current_area = new_area

        # 3. 更新 GameState
        if self.game_state:
            self.game_state.player_location = area_id
            self.game_state.area_id = area_id
            self.game_state.sub_location = None
            self._dirty_game_state = True

        # 4. 同步队伍位置
        if self._party_service:
            try:
                await self._party_service.sync_locations(
                    self.world_id, self.session_id, area_id, None
                )
            except Exception as exc:
                logger.warning(
                    "[SessionRuntime] 队伍位置同步失败: %s", exc
                )

        # 5. 同步到 StateManager
        if self._state_manager and self.game_state:
            await self._state_manager.set_state(
                self.world_id, self.session_id, self.game_state
            )

        # 6. SceneBus 切换
        if self.scene_bus:
            self.scene_bus.clear()
        from app.world.scene_bus import SceneBus
        self.scene_bus = SceneBus(area_id=area_id)

        logger.info(
            "[SessionRuntime] enter_area: %s → %s", old_area_id, area_id
        )

        return {
            "success": True,
            "previous_area": old_area_id,
            "new_area": area_id,
            "area_name": area_def.name if area_def else area_id,
            "visit_summary": (
                visit_summary.model_dump() if visit_summary else None
            ),
        }

    # =========================================================================
    # enter_sublocation / leave_sublocation
    # =========================================================================

    async def enter_sublocation(self, sub_id: str) -> Dict[str, Any]:
        """进入子地点。

        委托 WorldRuntime 做子地点校验（营业时间等），然后更新状态。
        """
        if not self.game_state or not self.game_state.player_location:
            return {"success": False, "error": "当前位置未知"}

        # 如果有 WorldRuntime，委托完整逻辑
        if self._world_runtime:
            result = await self._world_runtime.enter_sub_location(
                self.world_id, self.session_id, sub_id
            )
            if result.get("success"):
                self.game_state.sub_location = sub_id
                self._dirty_game_state = True
                # SceneBus: 更新 sub_location（不换实例）
                if self.scene_bus:
                    self.scene_bus.sub_location = sub_id
            return result

        # 最小化实现：直接更新状态
        self.game_state.sub_location = sub_id
        self._dirty_game_state = True
        if self.scene_bus:
            self.scene_bus.sub_location = sub_id

        if self._state_manager:
            await self._state_manager.set_state(
                self.world_id, self.session_id, self.game_state
            )

        return {
            "success": True,
            "sub_location": sub_id,
        }

    async def leave_sublocation(self) -> Dict[str, Any]:
        """离开子地点。"""
        if not self.game_state:
            return {"success": False, "error": "游戏状态未初始化"}

        if not self.game_state.sub_location:
            return {"success": False, "error": "当前不在子地点"}

        # 如果有 WorldRuntime，委托完整逻辑
        if self._world_runtime:
            result = await self._world_runtime.leave_sub_location(
                self.world_id, self.session_id
            )
            if result.get("success"):
                self.game_state.sub_location = None
                self._dirty_game_state = True
                if self.scene_bus:
                    self.scene_bus.sub_location = None
            return result

        old_sub = self.game_state.sub_location
        self.game_state.sub_location = None
        self._dirty_game_state = True
        if self.scene_bus:
            self.scene_bus.sub_location = None

        if self._state_manager:
            await self._state_manager.set_state(
                self.world_id, self.session_id, self.game_state
            )

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

        # 1. GameState
        if self._dirty_game_state and self.game_state:
            if self._world_runtime:
                await self._world_runtime.persist_state(self.game_state)
            elif self._state_manager:
                await self._state_manager.set_state(
                    self.world_id, self.session_id, self.game_state
                )
            self._dirty_game_state = False
            persisted.append("game_state")

        # 2. NarrativeProgress
        if self._dirty_narrative and self.narrative and self._narrative_service:
            await self._narrative_service.save_progress(
                self.world_id, self.session_id, self.narrative
            )
            self._dirty_narrative = False
            persisted.append("narrative")

        # 3. PlayerCharacter — U2: 运行时不再写 character_store，仅走 snapshot
        #    player 脏节点已包含在 WorldGraph 快照中（步骤 7）
        if self._dirty_player:
            self._dirty_player = False
            persisted.append("player")

        # 4. Party — PartyService 内部已实时写入 Firestore，
        #    这里标记重置即可
        if self._dirty_party:
            self._dirty_party = False
            persisted.append("party")

        # 5. AreaRuntime — 每轮增量持久化 state + events
        if self.current_area:
            await self.current_area.persist_state()
            persisted.append("area")

        # 6. Companions — 保存同伴状态/事件/摘要
        for companion in self.companions.values():
            try:
                await companion.save()
            except Exception as exc:
                logger.warning(
                    "[SessionRuntime] 同伴 '%s' 持久化失败: %s",
                    getattr(companion, "character_id", "?"), exc,
                )
        if self.companions:
            persisted.append("companions")

        # 7. WorldGraph 快照 (C7a)
        if self.world_graph and not self._world_graph_failed:
            await self._persist_world_graph_snapshot()
            persisted.append("world_graph")

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
        if self.world_graph and not self._world_graph_failed:
            self.world_graph._dirty_nodes.add("player")

    def mark_party_dirty(self) -> None:
        """外部修改 party 后调用此方法标记脏。"""
        self._dirty_party = True

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

        from app.services.time_manager import TimeManager
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

    def update_narrative(self, progress: NarrativeProgress) -> None:
        """更新叙事进度并同步到 GameState。"""
        self.narrative = progress
        if self.game_state:
            self.game_state.narrative_progress = progress.to_dict()
            self.game_state.chapter_id = progress.current_chapter
            self._dirty_game_state = True
        self._dirty_narrative = True

    # =========================================================================
    # 上下文导出（供 ContextAssembler 消费）
    # =========================================================================

    def to_context_dict(self) -> Dict[str, Any]:
        """导出完整上下文字典，供 ContextAssembler / Flash 分析消费。"""
        ctx: Dict[str, Any] = {
            "world_id": self.world_id,
            "session_id": self.session_id,
            "player_location": self.player_location,
            "sub_location": self.sub_location,
            "chapter_id": self.chapter_id,
            "area_id": self.area_id,
        }

        if self.game_state:
            ctx["game_time"] = self.game_state.game_time.model_dump()
            ctx["chat_mode"] = self.game_state.chat_mode
            ctx["active_dialogue_npc"] = self.game_state.active_dialogue_npc
            ctx["combat_id"] = self.game_state.combat_id
            ctx["metadata"] = self.game_state.metadata

        if self.player:
            ctx["player_summary"] = self.player.to_summary_text()

        if self.party:
            ctx["party_members"] = [
                {
                    "character_id": m.character_id,
                    "name": m.name,
                    "role": m.role.value,
                    "is_active": m.is_active,
                }
                for m in self.party.get_active_members()
            ]
        else:
            ctx["party_members"] = []

        if self.narrative:
            ctx["narrative_progress"] = self.narrative.to_dict()

        if self.history:
            ctx["recent_history"] = self.history.get_recent_history(
                max_tokens=4000
            )

        return ctx
