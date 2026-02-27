"""Narrative coordination — extracted from AdminCoordinator."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.exceptions import LLMServiceError

logger = logging.getLogger(__name__)


class NarrativeCoordinator:
    """开场/恢复/章节过渡 叙事生成。"""

    def __init__(
        self,
        state_manager: Any,
        party_service: Any,
        session_history_manager: Any,
        instance_manager: Any,
        world_runtime: Any,
        narrative_service: Any,
        llm_service: Any,
        **_kwargs: Any,
    ) -> None:
        self._state_manager = state_manager
        self.party_service = party_service
        self.session_history_manager = session_history_manager
        self.instance_manager = instance_manager
        self._world_runtime = world_runtime
        self.narrative_service = narrative_service
        self.llm_service = llm_service
        self._world_background_cache: Dict[str, str] = {}
        self._character_roster_cache: Dict[str, str] = {}

    async def resume_session(
        self,
        world_id: str,
        session_id: str,
        generate_narration: bool = True,
    ) -> Dict[str, Any]:
        """完整恢复游戏会话状态。"""
        from app.models.state_delta import GameState

        # 1. 加载 admin_state
        state = await self._state_manager.get_state(world_id, session_id)
        if not state:
            from app.runtime.session_runtime import SessionRuntime
            session_data = await SessionRuntime.get_session_meta(world_id, session_id)
            if session_data and session_data.metadata.get("admin_state"):
                state = GameState(**session_data.metadata["admin_state"])
                await self._state_manager.set_state(world_id, session_id, state)
        if not state:
            raise ValueError(f"session {session_id} not found or has no admin_state")

        # 2. 加载队伍
        party = await self.party_service.get_party(world_id, session_id)

        # 3. 加载对话历史
        history = self.session_history_manager.get_or_create(world_id, session_id)

        # 4. 预热队友实例
        prewarmed_members = []
        if party:
            for member in party.get_active_members():
                try:
                    await self.instance_manager.get_or_create(
                        member.character_id, world_id,
                        session_id=session_id,
                    )
                    prewarmed_members.append(member.name)
                except (OSError, KeyError) as exc:
                    logger.warning(
                        "[resume] 队友实例预热失败 (%s): %s",
                        member.character_id, exc,
                    )

        # 5. 获取当前位置信息
        location = await self._world_runtime.get_current_location(world_id, session_id)

        # 6. 构建恢复数据
        history_stats = {
            "message_count": history._window.message_count,
            "total_tokens": history._window.current_tokens,
        }

        party_info: Dict[str, Any] = {"has_party": False, "members": []}
        if party:
            party_info = {
                "has_party": True,
                "party_id": party.party_id,
                "members": [
                    {
                        "character_id": m.character_id,
                        "name": m.name,
                        "role": m.role.value,
                        "is_active": m.is_active,
                    }
                    for m in party.members
                ],
            }

        state_dict = {
            "player_location": state.player_location,
            "sub_location": state.sub_location,
            "chapter_id": getattr(state, "chapter_id", None),
            "game_time": state.game_time.model_dump() if state.game_time else None,
        }

        # 7. 可选生成恢复叙述
        resume_narration = ""
        if generate_narration:
            resume_narration = await self._generate_resume_narration(
                world_id=world_id,
                location=location,
                state=state,
                party=party,
                history=history,
            )

        # Determine phase based on character existence
        from app.runtime.session_runtime import SessionRuntime
        session = await SessionRuntime.get_or_restore(world_id, session_id)
        has_character = session.player is not None
        phase = "active" if has_character else "character_creation"

        return {
            "session_id": session_id,
            "restored": True,
            "phase": phase,
            "state": state_dict,
            "location": location,
            "party": party_info,
            "history": history_stats,
            "resume_narration": resume_narration,
            "prewarmed_members": prewarmed_members,
        }

    async def _generate_resume_narration(
        self,
        world_id: str,
        location: Dict[str, Any],
        state: Any,
        party: Any,
        history: Any,
    ) -> str:
        """生成恢复叙述，帮助玩家回忆进度。"""
        location_name = location.get("location_name") or location.get("location_id") or "未知地点"
        time_info = state.game_time
        time_text = ""
        if time_info:
            day = getattr(time_info, "day", None)
            hour = getattr(time_info, "hour", None)
            minute = getattr(time_info, "minute", None)
            if day is not None and hour is not None and minute is not None:
                time_text = f"第{day}天 {hour:02d}:{minute:02d}"

        teammate_names = []
        if party:
            teammate_names = [m.name for m in party.get_active_members()]

        recent_history = history.get_recent_history(max_tokens=2000) if history else ""

        prompt_path = Path("app/prompts/session_resume.md")
        if prompt_path.exists():
            prompt_template = prompt_path.read_text(encoding="utf-8")
            prompt = prompt_template.format(
                location_name=location_name,
                time=time_text or "未知",
                teammates=", ".join(teammate_names) if teammate_names else "无",
                recent_history=recent_history or "无",
            )
        else:
            prompt = (
                f"你是游戏 GM。玩家正在恢复之前的游戏会话。请根据以下信息生成2-3句沉浸式中文叙述，"
                f"帮助玩家回忆他们上次游戏的进度和状态。不要输出JSON。\n\n"
                f"当前位置: {location_name}\n"
                f"时间: {time_text or '未知'}\n"
                f"队友: {', '.join(teammate_names) if teammate_names else '无'}\n"
                f"近期对话:\n{recent_history or '无'}\n"
            )

        try:
            result = await self.llm_service.generate_simple(
                prompt,
                model_override=settings.admin_flash_model,
                thinking_level=settings.admin_flash_thinking_level,
            )
            narration = (result or "").strip()
            if narration:
                return narration
        except LLMServiceError as exc:
            logger.error("[resume_narration] LLM 生成失败: %s", exc)

        # fallback
        parts = [f"你回到了{location_name}。"]
        if time_text:
            parts.append(f"现在是{time_text}。")
        if teammate_names:
            parts.append(f"{', '.join(teammate_names)}还在你身边。")
        return "".join(parts)

    async def generate_opening_narration(
        self,
        world_id: str,
        session_id: str,
    ) -> str:
        """生成开场叙述"""
        try:
            # 构建上下文
            location = await self._world_runtime.get_current_location(world_id, session_id)
            location_name = location.get("location_name") or location.get("location_id") or "未知地点"
            location_atmosphere = location.get("atmosphere") or ""
            npcs_present = ", ".join(location.get("npcs_present", [])) or "无"

            _time_state = await self._state_manager.get_state(world_id, session_id)
            time_info = _time_state.game_time.model_dump() if _time_state and _time_state.game_time else {}
            time_text = time_info.get("formatted") or time_info.get("formatted_time") or "未知"

            world_background = await self._get_world_background(world_id, session_id)

            # 章节信息
            chapter_plan = await self.narrative_service.get_current_chapter_plan(world_id, session_id)
            chapter_obj = chapter_plan.get("chapter") or {}
            chapter_name = chapter_obj.get("name", "序章")
            chapter_description = chapter_obj.get("description", "")
            chapter_goals = chapter_plan.get("goals", [])
            event_directives = chapter_plan.get("event_directives", [])
            first_event_directive = event_directives[0] if event_directives else "探索当前环境"

            # {{user}} 占位符替换
            _sanitize = lambda t: (t or "").replace("{{user}}", "冒险者").replace("{{char}}", "")
            chapter_name = _sanitize(chapter_name)
            chapter_description = _sanitize(chapter_description)
            first_event_directive = _sanitize(first_event_directive)

            # 队友
            party = await self.party_service.get_party(world_id, session_id)
            teammates = "无"
            if party:
                names = [m.name for m in party.get_active_members()]
                if names:
                    teammates = ", ".join(names)

            # 玩家角色信息
            from app.runtime.session_runtime import SessionRuntime
            _session = await SessionRuntime.get_or_restore(world_id, session_id)
            player_char = _session.player
            player_character_text = player_char.to_summary_text() if player_char else "无玩家角色"

            # 加载 prompt
            prompt_path = Path("app/prompts/opening_narration.md")
            if prompt_path.exists():
                prompt_template = prompt_path.read_text(encoding="utf-8")
                prompt = prompt_template.format(
                    player_character=player_character_text,
                    world_background=world_background or "未知世界",
                    chapter_name=chapter_name,
                    chapter_description=chapter_description[:600] or "冒险的开始",
                    chapter_goals="、".join(
                        _sanitize(g) for g in chapter_goals[:3]
                    ) if chapter_goals else "探索世界",
                    first_event_directive=first_event_directive,
                    location_name=location_name,
                    location_atmosphere=location_atmosphere or "平静",
                    npcs_present=npcs_present,
                    teammates=teammates,
                    time=time_text,
                )
            else:
                prompt = (
                    f"你是TRPG游戏GM。玩家刚开始冒险，请生成3-5句开场叙述。\n\n"
                    f"位置：{location_name}\n时间：{time_text}\n"
                    f"章节：{chapter_name}\n不要输出JSON。"
                )

            result = await self.llm_service.generate_simple(
                prompt,
                model_override=settings.admin_flash_model,
                thinking_level=settings.admin_flash_thinking_level,
            )
            narration = (result or "").strip()
            if narration:
                return narration
        except LLMServiceError as exc:
            logger.error("[opening_narration] 生成失败: %s", exc)

        # fallback
        fallback_name = location_name if "location_name" in locals() else "未知地点"
        return f"冒险在{fallback_name}开始了。"

    async def _generate_chapter_transition(
        self,
        world_id: str,
        session_id: str,
        new_chapter_id: Optional[str],
        new_maps_unlocked: List[str],
    ) -> str:
        """生成章节过渡叙述"""
        if not new_chapter_id:
            return ""

        chapter_info = self.narrative_service.get_chapter_info(world_id, new_chapter_id)
        chapter_name = chapter_info.get("name", new_chapter_id) if chapter_info else new_chapter_id
        chapter_desc = chapter_info.get("description", "") if chapter_info else ""

        maps_text = ""
        if new_maps_unlocked and "*" not in new_maps_unlocked:
            maps_text = f"\n新区域已解锁：{'、'.join(new_maps_unlocked)}"

        prompt = (
            f"你是TRPG游戏GM。当前章节已完成，故事推进到新章节。"
            f"请用2-3句中文叙述章节转换，营造过渡感。不要输出JSON。\n\n"
            f"新章节：{chapter_name}\n"
            f"章节描述：{chapter_desc}\n"
            f"{maps_text}"
        )

        try:
            result = await self.llm_service.generate_simple(
                prompt,
                model_override=settings.admin_flash_model,
                thinking_level=settings.admin_flash_thinking_level,
            )
            return (result or "").strip() or f"故事进入了新篇章——{chapter_name}。"
        except LLMServiceError as exc:
            logger.error("[chapter_transition] LLM 生成失败: %s", exc)
            return f"故事进入了新篇章——{chapter_name}。{maps_text}"

    async def _get_world_background(self, world_id: str, session_id: Optional[str] = None) -> str:
        """获取世界背景描述（缓存）。"""
        if world_id in self._world_background_cache:
            base_text = self._world_background_cache[world_id]
        else:
            parts = []
            try:
                from app.runtime.game_runtime import GameRuntime
                world_instance = GameRuntime.get_instance().get_world(world_id)
                if world_instance and world_instance.world_constants:
                    wc = world_instance.world_constants
                    desc = wc.description or wc.background or ""
                    if desc:
                        parts.append(desc)
                    name = wc.name or ""
                    if name and not desc.startswith(name):
                        parts.insert(0, f"世界: {name}")
            except (KeyError, AttributeError) as exc:
                logger.debug("[world_background] WorldInstance 读取失败: %s", exc)

            base_text = "\n".join(parts) if parts else ""
            self._world_background_cache[world_id] = base_text

        chapter_block = ""
        if session_id:
            try:
                progress = await self.narrative_service.get_progress(world_id, session_id)
                chapter_id = getattr(progress, "current_chapter", None)
                if chapter_id:
                    chapter_info = self.narrative_service.get_chapter_info(world_id, chapter_id) or {}
                    chapter_name = chapter_info.get("name") or chapter_id
                    chapter_desc = chapter_info.get("description") or ""
                    chapter_block = f"当前章节: {chapter_name}"
                    if chapter_desc:
                        chapter_block += f"\n{chapter_desc}"
                    try:
                        chapter_plan = await self.narrative_service.get_current_chapter_plan(
                            world_id=world_id,
                            session_id=session_id,
                        )
                        goals = chapter_plan.get("goals", []) if isinstance(chapter_plan, dict) else []
                        if goals:
                            goal_lines = "\n".join(f"- {goal}" for goal in goals[:3])
                            chapter_block += f"\n当前推进目标:\n{goal_lines}"
                    except (KeyError, AttributeError) as inner_exc:
                        logger.debug("[world_background] chapter_plan 读取失败: %s", inner_exc)
            except (KeyError, AttributeError) as exc:
                logger.debug("[world_background] chapter 读取失败: %s", exc)

        blocks = [b for b in (base_text, chapter_block) if b]
        result = "\n".join(blocks)
        return result.replace("{{user}}", "冒险者").replace("{{char}}", "")

    async def _get_character_roster(self, world_id: str) -> str:
        """获取世界角色花名册（缓存）。"""
        if world_id in self._character_roster_cache:
            return self._character_roster_cache[world_id]

        entries = []
        try:
            from app.runtime.game_runtime import GameRuntime
            world_instance = GameRuntime.get_instance().get_world(world_id)
            if world_instance:
                for char_id, data in world_instance.character_registry.items():
                    profile = data.get("profile") if isinstance(data.get("profile"), dict) else {}
                    metadata = profile.get("metadata") if isinstance(profile.get("metadata"), dict) else {}
                    state = data.get("state") if isinstance(data.get("state"), dict) else {}
                    name = profile.get("name") or data.get("name") or char_id
                    occupation = (
                        profile.get("occupation")
                        or data.get("occupation")
                        or profile.get("role")
                        or data.get("role")
                        or ""
                    )
                    default_map = (
                        metadata.get("default_map")
                        or profile.get("default_map")
                        or data.get("default_map")
                        or state.get("current_map")
                        or data.get("default_location")
                        or ""
                    )
                    default_sub = metadata.get("default_sub_location") or profile.get("default_sub_location") or ""
                    entry = f"{name}(id={char_id}"
                    if occupation:
                        entry += f", {occupation}"
                    if default_map:
                        entry += f", 常驻:{default_map}"
                    if default_sub:
                        entry += f"/{default_sub}"
                    entry += ")"
                    entries.append(entry)
        except (KeyError, AttributeError) as exc:
            logger.debug("[character_roster] WorldInstance 读取失败: %s", exc)

        result = ", ".join(entries) if entries else ""
        self._character_roster_cache[world_id] = result
        return result
