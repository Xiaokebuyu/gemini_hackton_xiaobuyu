"""SessionRuntime — 会话级状态统一层（Phase 2A 实现）。

包装现有服务（StateManager / PartyService / NarrativeService / SessionHistory / CharacterStore），
提供统一的会话状态访问与生命周期管理。初始阶段采用委托模式，不修改原有服务。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
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
        session_store: Optional[Any] = None,
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
        self._session_store = session_store
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
        if self.world_graph and not self._world_graph_failed:
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

            # 2.3: 恢复副作用去重集合（crash recovery 安全）
            saved_dedup = self.game_state.metadata.get("_applied_side_effects")
            if isinstance(saved_dedup, list):
                self._applied_side_effect_events = set(saved_dedup[-200:])

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
            await area_rt.load(self.world_id, self.session_id)
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
            from app.world.graph.builder import GraphBuilder
            from app.world.events.behavior_engine import BehaviorEngine
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
            from app.world.graph.snapshot import dict_to_snapshot, restore_snapshot
            db = fs.Client(database=settings.firestore_database)
            doc = (db.collection("worlds").document(self.world_id)
                   .collection("sessions").document(self.session_id)
                   .collection("world_snapshot").document("current").get())
            if not doc.exists:
                logger.info("[SessionRuntime] 无 WorldGraph 快照，加载知识数据")
                await self._load_knowledge_into_world_graph()
                return
            snapshot = dict_to_snapshot(doc.to_dict())
            if snapshot:
                restore_snapshot(self.world_graph, snapshot)
                logger.info("[SessionRuntime] WorldGraph 快照恢复: %d states, %d spawned",
                            len(snapshot.node_states), len(snapshot.spawned_nodes))
        except Exception as exc:
            logger.warning("[SessionRuntime] WorldGraph 快照恢复失败: %s", exc)

    async def _load_knowledge_into_world_graph(self) -> None:
        """首次会话：从 Firestore 加载预填充知识图谱到 WorldGraph。

        graph 已 sealed → 新节点自动成为 spawned_nodes → 首次 persist 即快照。
        后续 restore 从快照恢复，不再触发此方法。
        """
        if not self._graph_store or not self.world_graph:
            logger.info("[SessionRuntime] 知识加载跳过（graph_store=%s wg=%s）",
                        bool(self._graph_store), bool(self.world_graph))
            return

        from app.models.graph_scope import GraphScope

        wg = self.world_graph
        world_id = self.world_id

        # 构建 (scope, owner_id) 列表
        scopes: list = [(GraphScope.world(), "world_root")]

        chapter_id = self.chapter_id
        if chapter_id:
            scopes.append((GraphScope.chapter(chapter_id), chapter_id))

        area_id = self.area_id or self.player_location
        if chapter_id and area_id:
            scopes.append((GraphScope.area(chapter_id, area_id), area_id))

        for npc_id in wg.get_by_type("npc"):
            scopes.append((GraphScope.character(npc_id), npc_id))

        scopes.append((GraphScope.camp(), "camp"))

        # 并行加载
        async def _load_one(scope, owner_id):
            try:
                data = await self._graph_store.load_graph_v2(world_id, scope)
                return (owner_id, data, None)
            except Exception as exc:
                return (owner_id, None, exc)

        results = await asyncio.gather(
            *[_load_one(s, o) for s, o in scopes],
        )

        total_nodes = 0
        total_edges = 0

        for (scope, _), (owner_id, data, error) in zip(scopes, results):
            if error:
                logger.warning("[SessionRuntime] knowledge scope %s failed: %s", scope, error)
                continue
            if not data or (not data.nodes and not data.edges):
                continue
            n, e = self._inject_knowledge_scope(data, owner_id)
            total_nodes += n
            total_edges += e

        logger.info(
            "[SessionRuntime] 知识图谱首次加载完成: %d nodes, %d edges, %d scopes",
            total_nodes, total_edges, len(scopes),
        )

    def _inject_knowledge_scope(self, data: Any, owner_id: str) -> tuple:
        """将一个 scope 的知识节点/边注入 WorldGraph。返回 (nodes_added, edges_added)。"""
        from app.world.graph.models import WorldNode

        wg = self.world_graph
        nodes_added = 0
        edges_added = 0

        # 节点转换：MemoryNode → WorldNode
        for mn in data.nodes:
            if wg.has_node(mn.id):
                continue  # GraphBuilder 已创建的结构节点，跳过
            props = dict(mn.properties) if mn.properties else {}
            props["owner"] = owner_id
            node = WorldNode(
                id=mn.id, type=mn.type, name=mn.name,
                importance=mn.importance, properties=props,
            )
            wg.add_node(node)
            nodes_added += 1

        # has_memory 边：owner → 知识节点
        for mn in data.nodes:
            if not wg.has_node(mn.id) or not wg.has_node(owner_id):
                continue
            edge_key = f"has_memory_{owner_id}_{mn.id}"
            try:
                wg.add_edge(owner_id, mn.id, "has_memory", key=edge_key)
                edges_added += 1
            except Exception:
                pass  # 边已存在或其他问题，静默跳过

        # 原始知识边转换
        for me in data.edges:
            if not wg.has_node(me.source) or not wg.has_node(me.target):
                continue
            edge_key = me.id or f"{me.source}_{me.target}_{me.relation}"
            try:
                wg.add_edge(me.source, me.target, me.relation,
                            key=edge_key, weight=me.weight)
                edges_added += 1
            except Exception:
                pass

        return nodes_added, edges_added

    async def _persist_world_graph_snapshot(self) -> bool:
        """保存 WorldGraph 快照到 Firestore。返回 True 表示成功。"""
        if not self.world_graph or self._world_graph_failed:
            return False
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
            except Exception:
                pass
            db = fs.Client(database=settings.firestore_database)
            (db.collection("worlds").document(self.world_id)
             .collection("sessions").document(self.session_id)
             .collection("world_snapshot").document("current").set(data))
            self.world_graph.clear_dirty()
            logger.info("[SessionRuntime] WorldGraph 快照保存: %d states, %d spawned, %d edges",
                        len(snapshot.node_states), len(snapshot.spawned_nodes),
                        len(snapshot.modified_edges))
            return True
        except Exception as exc:
            logger.error("[SessionRuntime] WorldGraph 快照保存失败: %s", exc)
            return False

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
                await new_area.load(self.world_id, self.session_id)
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
            "visit_summary": (
                visit_summary.model_dump() if visit_summary else None
            ),
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
            if self._session_store:
                await self._session_store.update_session(
                    self.world_id, self.session_id,
                    {"metadata.admin_state": self.game_state.model_dump()},
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

        # 3. PlayerCharacter — 延迟清除脏标记（步骤 7 后处理）
        player_was_dirty = self._dirty_player

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

        # 7a. WorldGraph 快照（主路径）
        snapshot_ok = False
        if self.world_graph and not self._world_graph_failed:
            snapshot_ok = await self._persist_world_graph_snapshot()
            if snapshot_ok:
                persisted.append("world_graph")

        # 7b. Player 脏标记清除（仅在快照成功后）
        if player_was_dirty:
            if snapshot_ok:
                self._dirty_player = False
                persisted.append("player")
            else:
                logger.error("[SessionRuntime] Player 数据未持久化（WorldGraph 快照失败），保留脏标记")

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
