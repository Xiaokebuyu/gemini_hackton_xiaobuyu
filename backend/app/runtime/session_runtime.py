"""SessionRuntime — 会话级状态统一层（Phase 2A 实现）。

包装现有服务（StateManager / PartyService / NarrativeService / SessionHistory / CharacterStore），
提供统一的会话状态访问与生命周期管理。初始阶段采用委托模式，不修改原有服务。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

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
        self.player: Optional[PlayerCharacter] = None
        self.party: Optional[Party] = None
        self.time: Optional[GameTimeState] = None
        self.narrative: Optional[NarrativeProgress] = None
        self.history: Optional[Any] = None  # SessionHistory
        self.companions: Dict[str, Any] = {}  # Phase 5
        self.current_area: Optional[AreaRuntime] = None
        self.delta_log: List[StateDelta] = []

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
    def is_restored(self) -> bool:
        """是否已完成 restore()。"""
        return self._restored

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
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error("[SessionRuntime] Wave 1 任务 %d 异常: %s", i, result)

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

        self._restored = True
        logger.info(
            "[SessionRuntime] restore 完成: location=%s chapter=%s party_size=%d",
            self.player_location,
            self.chapter_id,
            len(self.party.members) if self.party else 0,
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
        """加载 PlayerCharacter。"""
        if self._character_store:
            try:
                self.player = await self._character_store.get_character(
                    self.world_id, self.session_id
                )
            except Exception as exc:
                logger.warning(
                    "[SessionRuntime] PlayerCharacter 加载失败: %s", exc
                )
                self.player = None
        else:
            self.player = None

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

        # 事件初始化：始终合并章节事件（initialize_events_from_chapter 内部去重）
        if self.current_area and self.narrative:
            chapter = self.world.chapter_registry.get(
                self.narrative.current_chapter
            )
            if chapter:
                from app.models.narrative import Chapter as ChapterModel
                if isinstance(chapter, dict):
                    try:
                        chapter = ChapterModel(**chapter)
                    except Exception:
                        chapter = None
                if chapter:
                    self.current_area.initialize_events_from_chapter(
                        chapter, self.narrative.current_chapter
                    )
                    logger.info(
                        "[SessionRuntime] 从章节定义初始化事件: area=%s chapter=%s events=%d",
                        current_area_id,
                        self.narrative.current_chapter,
                        len(self.current_area.events),
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

            # 事件初始化：始终合并章节事件（initialize_events_from_chapter 内部去重）
            if self.narrative:
                chapter = (
                    self.world.chapter_registry.get(self.narrative.current_chapter)
                    if self.world
                    else None
                )
                if chapter:
                    from app.models.narrative import Chapter as ChapterModel
                    if isinstance(chapter, dict):
                        try:
                            chapter = ChapterModel(**chapter)
                        except Exception:
                            chapter = None
                    if chapter:
                        new_area.initialize_events_from_chapter(
                            chapter, self.narrative.current_chapter
                        )
                        logger.info(
                            "[SessionRuntime] 从章节定义初始化事件: area=%s chapter=%s events=%d",
                            area_id,
                            self.narrative.current_chapter,
                            len(new_area.events),
                        )

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
            return result

        # 最小化实现：直接更新状态
        self.game_state.sub_location = sub_id
        self._dirty_game_state = True

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
            return result

        old_sub = self.game_state.sub_location
        self.game_state.sub_location = None
        self._dirty_game_state = True

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

        # 3. PlayerCharacter
        if self._dirty_player and self.player and self._character_store:
            await self._character_store.save_character(
                self.world_id, self.session_id, self.player
            )
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
        """外部修改 player 后调用此方法标记脏。"""
        self._dirty_player = True

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
