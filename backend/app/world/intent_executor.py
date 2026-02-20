"""IntentExecutor — 引擎前置执行高置信度机械意图。

Direction A.2 实现。
提取自 v4_agentic_tools.navigate() 的机械部分，并补充 EXAMINE/USE_ITEM 轻量执行包装。
不生成叙述，只做状态变更 + 总线写入。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.config import settings
from app.world.intent_resolver import IntentType, ResolvedIntent
from app.world.scene_bus import BusEntry, BusEntryType

logger = logging.getLogger(__name__)


def update_hosts_edges(wg, old_area_id: Optional[str], new_area_id: str) -> None:
    """Update player HOSTS edge: remove from old area -> add to new area."""
    from app.world.models import WorldEdgeType
    if not wg.has_node("player"):
        return
    if old_area_id and wg.has_node(old_area_id):
        wg.remove_edge(old_area_id, "player", key="hosts_player")
    if wg.has_node(new_area_id):
        wg.add_edge(new_area_id, "player", WorldEdgeType.HOSTS.value, key="hosts_player")
        wg.merge_state("player", {"current_location": new_area_id})


def update_party_hosts_edges(wg, new_area_id: str, party) -> None:
    """Sync party member HOSTS edges to follow player."""
    from app.world.models import WorldEdgeType
    if not party or not wg.has_node(new_area_id):
        return
    for member in party.get_active_members():
        char_id = member.character_id
        if not wg.has_node(char_id):
            continue
        edge_key = f"hosts_{char_id}"
        npc_node = wg.get_node(char_id)
        if npc_node:
            old_host = npc_node.state.get("current_location", "")
            if old_host and old_host != new_area_id and wg.has_node(old_host):
                wg.remove_edge(old_host, char_id, key=edge_key)
        wg.add_edge(new_area_id, char_id, WorldEdgeType.HOSTS.value, key=edge_key)
        wg.merge_state(char_id, {"current_location": new_area_id})


class EngineResult(BaseModel):
    success: bool = False
    intent_type: str = ""
    target: str = ""
    bus_entries: List[BusEntry] = Field(default_factory=list)
    narrative_hints: List[str] = Field(default_factory=list)
    error: str = ""


class IntentExecutor:
    """引擎前置执行器 — 执行 MOVE / TALK / LEAVE / REST / EXAMINE / USE_ITEM 的机械部分。"""

    def __init__(
        self,
        session: Any,
        scene_bus: Any,
        recall_orchestrator: Optional[Any] = None,
        flash_cpu: Optional[Any] = None,
    ) -> None:
        self.session = session
        self.scene_bus = scene_bus
        self.recall_orchestrator = recall_orchestrator
        self.flash_cpu = flash_cpu

    async def dispatch(self, intent: ResolvedIntent) -> EngineResult:
        """分派意图到对应执行器。"""
        if intent.type == IntentType.LEAVE:
            return await self.execute_leave()
        elif intent.type == IntentType.MOVE:
            if intent.params.get("is_sublocation"):
                return await self.execute_sublocation_enter(intent.target, intent.target_name)
            return await self.execute_move(intent.target, intent.target_name)
        elif intent.type == IntentType.TALK:
            return await self.execute_talk(intent.target, intent.target_name, intent.raw_input)
        elif intent.type == IntentType.REST:
            return await self.execute_rest()
        elif intent.type == IntentType.EXAMINE:
            return await self.execute_examine(intent.target, intent.target_name)
        elif intent.type == IntentType.USE_ITEM:
            return await self.execute_use_item(intent.target, intent.target_name)
        return EngineResult(error=f"unsupported intent type: {intent.type}")

    async def execute_move(self, destination_id: str, destination_name: str = "") -> EngineResult:
        """执行区域导航的机械部分。

        包含:
        - 章节门限检查
        - 连接验证
        - 旅行时间推进
        - 区域切换
        - ON_EXIT / ON_ENTER 行为触发
        - 图状态更新

        不包含（保留给 GM）:
        - 叙事生成
        """
        from app.models.state_delta import GameTimeState
        from app.world.models import WorldEdgeType

        wg = getattr(self.session, "world_graph", None)
        current_area_id = self.session.player_location

        if not current_area_id:
            return EngineResult(error="current location unknown")

        # 1. 章节门限检查
        chapter_id = self.session.chapter_id
        world = self.session.world
        if chapter_id and world and hasattr(world, "chapter_registry"):
            chapter_data = world.chapter_registry.get(chapter_id)
            if chapter_data is not None:
                if isinstance(chapter_data, dict):
                    available_maps = chapter_data.get("available_maps", [])
                else:
                    available_maps = getattr(chapter_data, "available_maps", []) or []
                if available_maps and destination_id not in available_maps:
                    return EngineResult(
                        error=f"area '{destination_id}' not available in chapter '{chapter_id}'"
                    )

        # 2. 连接验证
        edge_props: Optional[Dict[str, Any]] = None
        if wg and not getattr(self.session, "_world_graph_failed", False):
            for neighbor_id, edata in wg.get_neighbors(current_area_id, WorldEdgeType.CONNECTS.value):
                if neighbor_id == destination_id:
                    edge_props = edata
                    break
            if edge_props is None:
                return EngineResult(
                    error=f"no connection from '{current_area_id}' to '{destination_id}'"
                )
            if edge_props.get("blocked"):
                return EngineResult(
                    error=f"path blocked: {edge_props.get('blocked_reason', 'impassable')}"
                )
        else:
            # WorldGraph 不可用时不执行（让 GM 走旧路径）
            return EngineResult(error="world_graph unavailable for engine execution")

        # 3. 旅行时间推进
        travel_minutes = self._parse_travel_time(edge_props.get("travel_time", "30 minutes"))
        travel_minutes = self._normalize_advance_minutes(travel_minutes)
        current_time = self.session.time
        if current_time:
            total = current_time.hour * 60 + current_time.minute + travel_minutes
            new_day = current_time.day + total // (24 * 60)
            remaining = total % (24 * 60)
            new_hour = remaining // 60
            new_minute = remaining % 60
        else:
            new_day, new_hour, new_minute = 1, 8, 0

        period = self._get_period(new_hour)
        formatted = f"第{new_day}天 {new_hour:02d}:{new_minute:02d}"
        new_time = GameTimeState(
            day=new_day, hour=new_hour, minute=new_minute,
            period=period, formatted=formatted,
        )
        self.session.update_time(new_time)

        # 4. 保存旧子地点供 ON_EXIT 使用
        old_sub_location = self.session.sub_location

        # 5. 区域切换
        try:
            result = await self.session.enter_area(destination_id)
        except Exception as exc:
            logger.error("[IntentExecutor] enter_area failed: %s", exc, exc_info=True)
            return EngineResult(error=f"enter_area error: {exc}")

        if not isinstance(result, dict) or not result.get("success"):
            return EngineResult(error=result.get("error", "enter_area failed") if isinstance(result, dict) else "enter_area failed")

        # 6. WorldGraph 状态更新
        hints: List[str] = []
        if wg and not getattr(self.session, "_world_graph_failed", False) and wg.has_node(destination_id):
            try:
                wg.merge_state(destination_id, {"visited": True})
                old_count = wg.get_node(destination_id).state.get("visit_count", 0)
                wg.set_state(destination_id, "visit_count", old_count + 1)
            except Exception:
                pass

            # ON_EXIT → HOSTS edge sync → ON_ENTER
            engine = getattr(self.session, "_behavior_engine", None)
            is_area_change = current_area_id and current_area_id != destination_id

            if is_area_change:
                # 6a. ON_EXIT (old area)
                if engine:
                    try:
                        if wg.has_node(current_area_id):
                            ctx_exit = self.session.build_tick_context("post")
                            if ctx_exit:
                                ctx_exit.player_location = current_area_id
                                ctx_exit.player_sub_location = old_sub_location or ""
                                exit_result = engine.handle_exit("player", current_area_id, ctx_exit)
                                if exit_result:
                                    hints.extend(exit_result.narrative_hints)
                                    self.session._sync_tick_to_narrative(exit_result)
                                    self.session._apply_tick_side_effects(exit_result)
                    except Exception as exc:
                        logger.warning("[IntentExecutor] exit handling failed: %s", exc)

                # 6b. HOSTS edge sync (player + party)
                try:
                    update_hosts_edges(wg, current_area_id, destination_id)
                    update_party_hosts_edges(wg, destination_id, self.session.party)
                except Exception:
                    pass

                # 6c. ON_ENTER (new area)
                if engine:
                    try:
                        if wg.has_node(destination_id):
                            ctx_enter = self.session.build_tick_context("post")
                            if ctx_enter:
                                enter_result = engine.handle_enter("player", destination_id, ctx_enter)
                                if enter_result:
                                    hints.extend(enter_result.narrative_hints)
                                    self.session._sync_tick_to_narrative(enter_result)
                                    self.session._apply_tick_side_effects(enter_result)
                    except Exception as exc:
                        logger.warning("[IntentExecutor] enter handling failed: %s", exc)

        # 7. 构建总线条目
        bus_entries = [
            BusEntry(
                actor="engine",
                type=BusEntryType.ENGINE_RESULT,
                content=f"navigated to {destination_name or destination_id} (travel: {travel_minutes}min)",
                data={
                    "tool": "navigate",
                    "destination": destination_id,
                    "travel_minutes": travel_minutes,
                },
            )
        ]

        return EngineResult(
            success=True,
            intent_type="move_area",
            target=destination_id,
            bus_entries=bus_entries,
            narrative_hints=hints,
        )

    def _resolve_sublocation_ids(self, sub_id: str) -> Tuple[str, str]:
        """Normalize sub-location IDs across graph and runtime layers.

        WorldGraph location nodes use `loc_{area_id}_{sub_id}` while runtime APIs
        expect raw `sub_id`.
        """
        area_id: str = ""
        area_candidate = getattr(self.session, "area_id", None)
        if isinstance(area_candidate, str) and area_candidate.strip():
            area_id = area_candidate.strip()
        else:
            player_location = getattr(self.session, "player_location", None)
            if isinstance(player_location, str) and player_location.strip():
                area_id = player_location.strip()

        runtime_sub_id = sub_id
        graph_location_id = sub_id

        if area_id:
            prefix = f"loc_{area_id}_"
            if sub_id.startswith(prefix) and len(sub_id) > len(prefix):
                runtime_sub_id = sub_id[len(prefix):]
                graph_location_id = sub_id
            elif not sub_id.startswith("loc_"):
                graph_location_id = f"loc_{area_id}_{sub_id}"

        if not runtime_sub_id:
            runtime_sub_id = sub_id

        return runtime_sub_id, graph_location_id

    async def execute_sublocation_enter(self, sub_id: str, sub_name: str = "") -> EngineResult:
        """执行子地点进入的机械部分。"""
        runtime_sub_id, graph_location_id = self._resolve_sublocation_ids(sub_id)

        # area_lock 检查（通过 WorldGraph）
        wg = getattr(self.session, "world_graph", None)
        if wg and not getattr(self.session, "_world_graph_failed", False):
            node = wg.get_node(graph_location_id)
            if node is None and graph_location_id != sub_id:
                node = wg.get_node(sub_id)
            if node and node.state.get("locked"):
                return EngineResult(
                    error=(
                        f"sub-location '{runtime_sub_id}' is locked: "
                        f"{node.state.get('lock_reason', 'inaccessible')}"
                    )
                )

        try:
            result = await self.session.enter_sublocation(runtime_sub_id)
        except Exception as exc:
            logger.error("[IntentExecutor] enter_sublocation failed: %s", exc, exc_info=True)
            return EngineResult(error=f"enter_sublocation error: {exc}")

        if not isinstance(result, dict) or not result.get("success"):
            return EngineResult(error=result.get("error", "enter_sublocation failed") if isinstance(result, dict) else "enter_sublocation failed")

        recall_entry = await self._build_enter_sublocation_recall_entry(runtime_sub_id, sub_name)
        bus_entries = [
            BusEntry(
                actor="engine",
                type=BusEntryType.ENGINE_RESULT,
                content=f"entered sub-location {sub_name or sub_id}",
                data={"tool": "enter_sublocation", "sub_location": runtime_sub_id},
            )
        ]
        if recall_entry is not None:
            bus_entries.append(recall_entry)

        return EngineResult(
            success=True,
            intent_type="move_sublocation",
            target=runtime_sub_id,
            bus_entries=bus_entries,
        )

    async def _build_enter_sublocation_recall_entry(
        self,
        sub_id: str,
        sub_name: str = "",
    ) -> Optional[BusEntry]:
        """Recall historical signals when entering a sub-location.

        Fail-open design: recall errors only log warnings and never block movement.
        """
        if self.recall_orchestrator is None:
            return None

        world_id = getattr(self.session, "world_id", "")
        chapter_id = getattr(self.session, "chapter_id", None)
        area_id = getattr(self.session, "area_id", None) or getattr(self.session, "player_location", None)
        if not world_id or not chapter_id or not area_id:
            return None

        character_id = "player"
        player = getattr(self.session, "player", None)
        if player is not None:
            candidate = getattr(player, "character_id", None)
            if isinstance(candidate, str) and candidate.strip():
                character_id = candidate.strip()

        seed_nodes = [seed for seed in [sub_id, sub_name, area_id] if isinstance(seed, str) and seed.strip()]
        if not seed_nodes:
            return None

        try:
            recall = await asyncio.wait_for(
                self.recall_orchestrator.recall(
                    world_id=world_id,
                    character_id=character_id,
                    seed_nodes=seed_nodes,
                    intent_type="enter_sublocation",
                    chapter_id=chapter_id,
                    area_id=area_id,
                    location_id=sub_id,
                ),
                timeout=settings.admin_agentic_tool_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[IntentExecutor] enter_sublocation recall timeout: sub=%s seeds=%s",
                sub_id,
                seed_nodes,
            )
            return None
        except Exception as exc:
            logger.warning(
                "[IntentExecutor] enter_sublocation recall failed: sub=%s error=%s",
                sub_id,
                exc,
            )
            return None

        activated = getattr(recall, "activated_nodes", {}) or {}
        if not activated:
            return None

        ranked = sorted(activated.items(), key=lambda item: item[1], reverse=True)
        top_items = ranked[:5]
        translated = getattr(recall, "translated_memory", None)
        if translated:
            summary = f"进入{sub_name or sub_id}时，旧记忆被唤起：{str(translated)[:240]}"
        else:
            compact = "、".join(f"{node_id}({score:.2f})" for node_id, score in top_items)
            summary = f"进入{sub_name or sub_id}时触发了历史线索：{compact}"

        return BusEntry(
            actor="engine",
            type=BusEntryType.SYSTEM,
            content=summary,
            data={
                "tool": "recall_memory",
                "source": "enter_sublocation",
                "sub_location": sub_id,
                "seed_nodes": seed_nodes,
                "activated_count": len(activated),
                "activated_top": [
                    {"node_id": node_id, "score": score}
                    for node_id, score in top_items
                ],
                "used_subgraph": bool(getattr(recall, "used_subgraph", False)),
            },
        )

    async def execute_talk(self, npc_id: str, npc_name: str = "", player_message: str = "") -> EngineResult:
        """TALK — 验证 + 计数 + 调用 NPC AI 生成对话。

        NPC 回复直接写入总线，GM 不再代理。
        flash_cpu 不可用或调用失败时降级：只写 ACTION 条目，GM 仍可调 npc_dialogue。
        """
        wg = getattr(self.session, "world_graph", None)

        # NPC 存在性验证
        if wg and not getattr(self.session, "_world_graph_failed", False):
            node = wg.get_node(npc_id)
            if not node:
                return EngineResult(error=f"NPC '{npc_id}' not found in world graph")
            if not node.state.get("is_alive", True):
                return EngineResult(error=f"NPC '{npc_name or npc_id}' is not alive")

        # 交互计数更新
        if self.session.narrative:
            count = self.session.narrative.npc_interactions.get(npc_id, 0)
            self.session.narrative.npc_interactions[npc_id] = count + 1
            self.session.mark_narrative_dirty()

        bus_entries = [
            BusEntry(
                actor="player",
                type=BusEntryType.ACTION,
                content=f"wants to talk to {npc_name or npc_id}",
                data={"npc_id": npc_id, "npc_name": npc_name},
            )
        ]

        # ---- NPC 自产对话 ----
        npc_responded = False
        if self.flash_cpu:
            try:
                from app.models.admin_protocol import FlashOperation, FlashRequest as _FR
                req = _FR(
                    operation=FlashOperation.NPC_DIALOGUE,
                    parameters={"npc_id": npc_id, "message": player_message or "你好"},
                )
                resp = await self.flash_cpu.execute_request(
                    world_id=self.session.world_id,
                    session_id=self.session.session_id,
                    request=req,
                    generate_narration=False,
                )
                if resp.success and isinstance(resp.result, dict):
                    response_text = resp.result.get("response", "")
                    if response_text:
                        char_data = None
                        if self.session.world:
                            char_data = self.session.world.get_character(npc_id)
                        display_name = (char_data or {}).get("name", npc_name or npc_id) if isinstance(char_data, dict) else (npc_name or npc_id)

                        bus_entries.append(BusEntry(
                            actor=npc_id,
                            actor_name=display_name,
                            type=BusEntryType.SPEECH,
                            content=response_text,
                        ))
                        npc_responded = True
            except Exception as exc:
                logger.warning("[IntentExecutor] NPC dialogue failed for %s, GM will handle: %s", npc_id, exc)

        hints = [f"玩家想要与{npc_name or npc_id}交谈"]
        if npc_responded:
            hints.append(f"{npc_name or npc_id}已在总线中回复，请勿再调用 npc_dialogue 重复对话")

        return EngineResult(
            success=True,
            intent_type="talk" if npc_responded else "talk_pending",
            target=npc_id,
            bus_entries=bus_entries,
            narrative_hints=hints,
        )

    async def execute_leave(self) -> EngineResult:
        """执行离开子地点的机械部分。

        包含:
        - 子地点状态验证
        - 调用 session.leave_sublocation()
        - 总线写入
        """
        sub = self.session.sub_location
        if not sub:
            return EngineResult(error="not in a sub-location")

        try:
            result = await self.session.leave_sublocation()
        except Exception as exc:
            logger.error("[IntentExecutor] leave_sublocation failed: %s", exc, exc_info=True)
            return EngineResult(error=f"leave_sublocation error: {exc}")

        if isinstance(result, dict) and not result.get("success", True):
            return EngineResult(error=result.get("error", "leave_sublocation failed"))

        bus_entries = [
            BusEntry(
                actor="engine",
                type=BusEntryType.ENGINE_RESULT,
                content=f"left sub-location {sub}",
                data={"tool": "leave_sublocation", "sub_location": sub},
            )
        ]

        return EngineResult(
            success=True,
            intent_type="leave",
            target=sub,
            bus_entries=bus_entries,
            narrative_hints=["玩家离开了子地点"],
        )

    async def execute_rest(self) -> EngineResult:
        """执行休息的机械部分。

        包含:
        - 推进时间 60 分钟
        - 恢复 HP 25%
        - 总线写入
        """
        # 战斗中禁止休息
        game_state = getattr(self.session, "game_state", None)
        if game_state and getattr(game_state, "combat_id", None):
            return EngineResult(error="cannot rest during combat")

        # 推进时间 60 分钟
        rest_minutes = 60
        self.session.advance_time(rest_minutes)

        # 恢复 HP 25%
        healed = 0
        player = getattr(self.session, "player", None)
        if player and hasattr(player, "current_hp"):
            max_hp = player.max_hp
            heal_amount = max(1, int(max_hp * 0.25))
            old_hp = player.current_hp
            player.current_hp = min(max_hp, old_hp + heal_amount)
            healed = player.current_hp - old_hp
            self.session.mark_player_dirty()

        bus_entries = [
            BusEntry(
                actor="engine",
                type=BusEntryType.ENGINE_RESULT,
                content=f"rested for {rest_minutes}min, healed {healed} HP",
                data={
                    "tool": "rest",
                    "rest_minutes": rest_minutes,
                    "healed": healed,
                },
            )
        ]

        return EngineResult(
            success=True,
            intent_type="rest",
            target="rest",
            bus_entries=bus_entries,
            narrative_hints=[f"玩家休息了{rest_minutes}分钟，恢复了{healed}点HP"],
        )

    @staticmethod
    def _parse_travel_time(travel_time: str) -> int:
        """Parse travel time string to minutes."""
        time_str = travel_time.lower()
        if "分钟" in time_str or "minutes" in time_str:
            try:
                return int("".join(filter(str.isdigit, time_str))) or 30
            except ValueError:
                return 30
        elif "小时" in time_str or "hour" in time_str:
            try:
                hours = float("".join(filter(str.isdigit, time_str))) or 1
                return int(hours * 60)
            except ValueError:
                return 60
        elif "半天" in time_str or "half day" in time_str:
            return 360
        elif "一天" in time_str or "day" in time_str:
            return 720
        return 30

    @staticmethod
    def _normalize_advance_minutes(raw_minutes: int) -> int:
        """Snap travel time to nearest allowed bucket."""
        allowed = [5, 10, 15, 30, 60, 120, 180, 240, 360, 480, 720]
        minutes = max(1, min(int(raw_minutes), 720))
        return min(allowed, key=lambda value: abs(value - minutes))

    @staticmethod
    def _get_period(hour: int) -> str:
        """Get time period string, consistent with TimePeriod enum values."""
        # 避免导入 app.services 包级 __init__ 的重依赖链（测试环境可无 mcp 包）
        if 5 <= hour < 8:
            return "dawn"
        elif 8 <= hour < 18:
            return "day"
        elif 18 <= hour < 20:
            return "dusk"
        else:
            return "night"

    async def execute_examine(self, target_id: str, target_name: str = "") -> EngineResult:
        """执行检查/查看意图的引擎前置部分。

        不改变游戏状态；
        聚合目标的结构化详情（WorldGraph + AreaRuntime），写入总线并提供可叙述提示。
        """
        display = target_name or target_id
        details: Dict[str, Any] = {
            "target_id": target_id,
            "target_name": display,
            "target_type": "unknown",
        }

        # 1) 从 WorldGraph 获取目标详情
        wg = getattr(self.session, "world_graph", None)
        if wg and not getattr(self.session, "_world_graph_failed", False):
            try:
                node = wg.get_node(target_id)
            except Exception:
                node = None
            if node is not None:
                raw_name = getattr(node, "name", "")
                node_name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else display
                raw_type = getattr(node, "type", "")
                node_type = raw_type.strip() if isinstance(raw_type, str) and raw_type.strip() else "unknown"
                props = getattr(node, "properties", {}) or {}
                state = getattr(node, "state", {}) or {}
                if not isinstance(props, dict):
                    props = {}
                if not isinstance(state, dict):
                    state = {}
                raw_desc = props.get("description") or state.get("description") or ""
                desc = raw_desc if isinstance(raw_desc, str) else ""
                details.update({
                    "target_name": node_name,
                    "target_type": node_type,
                })
                if desc:
                    details["description"] = str(desc)[:280]
                display = node_name

        # 2) 对子地点目标，尝试补充 location_context
        area_runtime = getattr(self.session, "current_area", None)
        if area_runtime and hasattr(area_runtime, "get_location_context"):
            try:
                loc_ctx = area_runtime.get_location_context(target_id)
                if isinstance(loc_ctx, dict) and not loc_ctx.get("error"):
                    details.update({
                        "target_type": "location",
                        "location_name": loc_ctx.get("name", details["target_name"]),
                        "interaction_type": loc_ctx.get("interaction_type", ""),
                        "resident_npcs": loc_ctx.get("resident_npcs", []),
                        "requirements": loc_ctx.get("requirements", []),
                    })
                    if loc_ctx.get("description"):
                        details["description"] = str(loc_ctx["description"])[:280]
                    display = details.get("location_name") or display
            except Exception:
                pass

        bus_entries = [
            BusEntry(
                actor="engine",
                type=BusEntryType.ENGINE_RESULT,
                content=f"player examines {display}",
                data={
                    "tool": "examine",
                    **details,
                },
            )
        ]

        hint_chunks = [f"玩家正在仔细查看「{display}」"]
        desc = details.get("description")
        if desc:
            hint_chunks.append(f"可见细节：{desc}")
        target_type = details.get("target_type", "unknown")
        hint_chunks.append(f"目标类型：{target_type}")

        return EngineResult(
            success=True,
            intent_type="examine",
            target=target_id,
            bus_entries=bus_entries,
            narrative_hints=["；".join(hint_chunks)],
        )

    async def execute_use_item(self, item_id: str, item_name: str = "") -> EngineResult:
        """执行使用物品意图的引擎前置部分。

        执行轻量物品效果（当前支持 heal 类），并消耗 1 个物品。
        若物品或效果不可执行，返回 error 交由 GM 兜底。
        """
        player = getattr(self.session, "player", None)
        if not player:
            return EngineResult(error="player not loaded")

        # 1) 匹配背包物品（id 优先，name 兜底）
        inventory = getattr(player, "inventory", []) or []
        match_item: Optional[Dict[str, Any]] = None
        resolved_item_id = item_id
        resolved_item_name = item_name
        query_id = (item_id or "").strip().lower()
        query_name = (item_name or "").strip().lower()
        for raw in inventory:
            if not isinstance(raw, dict):
                continue
            iid = str(raw.get("item_id") or raw.get("id") or "").strip()
            iname = str(raw.get("item_name") or raw.get("name") or iid).strip()
            iid_l = iid.lower()
            iname_l = iname.lower()
            if query_id and query_id == iid_l:
                match_item = raw
            elif query_name and query_name == iname_l:
                match_item = raw
            elif query_id and query_id == iname_l:
                match_item = raw
            elif query_name and query_name == iid_l:
                match_item = raw
            if match_item is not None:
                resolved_item_id = iid or resolved_item_id
                resolved_item_name = iname or resolved_item_name
                break

        if match_item is None:
            return EngineResult(error=f"item not found in inventory: {item_id or item_name}")

        display = resolved_item_name or resolved_item_id

        # 2) 解析并执行物品效果（当前支持 heal）
        from app.rules import ITEM_EFFECTS

        effect = ITEM_EFFECTS.get(resolved_item_id, {})
        effect_type = str(effect.get("effect_type", "")).strip().lower()
        if effect_type != "heal":
            return EngineResult(error=f"unsupported item effect: {resolved_item_id}")

        old_hp = int(getattr(player, "current_hp", 0))
        max_hp = int(getattr(player, "max_hp", 0))
        heal_expr = str(effect.get("heal_amount") or "2d4+2")
        heal_amount = 0
        try:
            from app.combat.dice import DiceRoller
            heal_amount, _ = DiceRoller.roll(heal_expr)
            heal_amount = max(0, int(heal_amount))
        except Exception:
            # 兜底：不因骰子解析失败中断主链路
            heal_amount = 8

        new_hp = min(max_hp, old_hp + heal_amount)
        actual_healed = max(0, new_hp - old_hp)
        player.current_hp = new_hp

        # 3) 消耗物品
        consumed = False
        if hasattr(player, "remove_item"):
            try:
                consumed = bool(player.remove_item(resolved_item_id, 1))
            except Exception:
                consumed = False
        if not consumed:
            return EngineResult(error=f"failed to consume item: {resolved_item_id}")

        if hasattr(self.session, "mark_player_dirty"):
            try:
                self.session.mark_player_dirty()
            except Exception:
                pass

        bus_entries = [
            BusEntry(
                actor="engine",
                type=BusEntryType.ENGINE_RESULT,
                content=f"player uses item {display} (+{actual_healed} HP)",
                data={
                    "tool": "use_item",
                    "item_id": resolved_item_id,
                    "item_name": resolved_item_name,
                    "effect_type": effect_type,
                    "heal_expr": heal_expr,
                    "rolled_heal": heal_amount,
                    "actual_healed": actual_healed,
                    "old_hp": old_hp,
                    "new_hp": new_hp,
                    "max_hp": max_hp,
                },
            )
        ]
        return EngineResult(
            success=True,
            intent_type="use_item",
            target=resolved_item_id,
            bus_entries=bus_entries,
            narrative_hints=[
                f"玩家使用了「{display}」，恢复 {actual_healed} 点HP（{new_hp}/{max_hp}），并已从背包消耗 1 个。"
            ],
        )
