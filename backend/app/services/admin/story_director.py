"""
StoryDirector - 主动剧情编排器。

两阶段评估（Pre-Flash + Post-Flash），纯机械 ConditionEngine。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.models.narrative import (
    Chapter,
    ChapterTransition,
    PacingConfig,
    StoryEvent,
)
from app.services.admin.condition_engine import ConditionEngine, GameContext
from app.services.narrative_service import NarrativeService

# =============================================================================
# 数据结构
# =============================================================================


@dataclass
class PendingFlashCondition:
    """待 Flash 评估的语义条件。"""
    event_id: str
    condition_prompt: str
    condition_id: str


@dataclass
class PreDirective:
    """Pre-Flash 评估结果。"""
    auto_fired_events: List[StoryEvent] = field(default_factory=list)
    pending_flash_conditions: List[PendingFlashCondition] = field(default_factory=list)
    narrative_injections: List[str] = field(default_factory=list)
    pacing_action: Optional[str] = None
    pacing_detail: str = ""


@dataclass
class StoryDirective:
    """Post-Flash 综合评估结果。"""
    fired_events: List[StoryEvent] = field(default_factory=list)
    chapter_transition: Optional[ChapterTransition] = None
    narrative_injections: List[str] = field(default_factory=list)
    pacing_action: Optional[str] = None  # none/hint/accelerate/decelerate
    pacing_detail: str = ""
    side_effects: List[Dict[str, Any]] = field(default_factory=list)


class StoryDirector:
    """主动剧情编排器。"""

    def __init__(self, narrative_service: NarrativeService):
        self.narrative = narrative_service
        self.engine = ConditionEngine()

    # ----- v2 两阶段评估 -----

    def pre_evaluate_multi(
        self,
        ctx: GameContext,
        chapters: List[Chapter],
    ) -> PreDirective:
        """Pre-Flash 评估多个并行活跃章节。"""
        merged = PreDirective()
        for chapter in chapters:
            partial = self.pre_evaluate(ctx, chapter)
            merged.auto_fired_events.extend(partial.auto_fired_events)
            merged.pending_flash_conditions.extend(partial.pending_flash_conditions)
            merged.narrative_injections.extend(partial.narrative_injections)
            # 节奏：取最紧迫的 action
            if partial.pacing_action and not merged.pacing_action:
                merged.pacing_action = partial.pacing_action
                merged.pacing_detail = partial.pacing_detail
            elif partial.pacing_action == "accelerate":
                merged.pacing_action = "accelerate"
                merged.pacing_detail = partial.pacing_detail
        return merged

    def post_evaluate_multi(
        self,
        ctx: GameContext,
        chapters: List[Chapter],
        flash_condition_results: Optional[Dict[str, bool]] = None,
        pre_auto_fired_ids: Optional[List[str]] = None,
    ) -> StoryDirective:
        """Post-Flash 评估多个并行活跃章节。"""
        merged = StoryDirective()
        for chapter in chapters:
            partial = self.post_evaluate(
                ctx, chapter=chapter,
                flash_condition_results=flash_condition_results,
                pre_auto_fired_ids=pre_auto_fired_ids,
            )
            merged.fired_events.extend(partial.fired_events)
            merged.narrative_injections.extend(partial.narrative_injections)
            merged.side_effects.extend(partial.side_effects)
            # 章节转换：取第一个命中的
            if partial.chapter_transition and not merged.chapter_transition:
                merged.chapter_transition = partial.chapter_transition
            # 节奏：取最紧迫的 action
            if partial.pacing_action and not merged.pacing_action:
                merged.pacing_action = partial.pacing_action
                merged.pacing_detail = partial.pacing_detail
            elif partial.pacing_action == "accelerate":
                merged.pacing_action = "accelerate"
                merged.pacing_detail = partial.pacing_detail
        return merged

    @staticmethod
    def is_parallel_chapter(chapter: Chapter) -> bool:
        """检查章节是否支持并行激活。"""
        return "parallel" in chapter.tags

    def pre_evaluate(self, ctx: GameContext, chapter: Optional[Chapter] = None) -> PreDirective:
        """Pre-Flash 评估：纯机械条件 + 识别待评估语义条件。"""
        directive = PreDirective()

        if not chapter:
            return directive

        for event in chapter.events:
            # 跳过已触发且不可重复的事件
            if event.id in ctx.events_triggered and not event.is_repeatable:
                continue

            # 冷却检查
            if event.cooldown_rounds > 0:
                last_round = ctx.event_cooldowns.get(event.id)
                if isinstance(last_round, int):
                    if (ctx.rounds_in_chapter - last_round) < event.cooldown_rounds:
                        continue

            # 没有触发条件 → 保持 LLM 提议模式（不自动触发）
            if not event.trigger_conditions.conditions:
                continue

            result = self.engine.evaluate(event.trigger_conditions, ctx)

            if result.pending_flash:
                # 有语义条件需要 Flash 评估
                if result.satisfied:  # 结构化部分已满足
                    for idx, flash_cond in enumerate(result.pending_flash):
                        prompt = flash_cond.params.get("prompt", "")
                        cond_id = self._make_flash_condition_id(event.id, idx)
                        directive.pending_flash_conditions.append(
                            PendingFlashCondition(
                                event_id=event.id,
                                condition_prompt=prompt,
                                condition_id=cond_id,
                            )
                        )
            elif result.satisfied:
                # 纯机械触发
                directive.auto_fired_events.append(event)
                if event.narrative_directive:
                    directive.narrative_injections.append(event.narrative_directive)

        # 节奏预分析
        pacing = chapter.pacing
        pacing_action, pacing_detail = self._evaluate_pacing(
            pacing, ctx.rounds_in_chapter,
            rounds_since_progress=0,  # Pre-Flash 时还不知道
        )
        directive.pacing_action = pacing_action
        directive.pacing_detail = pacing_detail

        # 节奏注入
        if pacing_action == "decelerate":
            directive.narrative_injections.append(
                "[GM减速] 让玩家自由探索，不要急于推进剧情。"
            )

        return directive

    def post_evaluate(
        self,
        ctx: GameContext,
        chapter: Optional[Chapter] = None,
        flash_condition_results: Optional[Dict[str, bool]] = None,
        pre_auto_fired_ids: Optional[List[str]] = None,
    ) -> StoryDirective:
        """Post-Flash 评估：合并机械结果 + Flash 语义结果。"""
        directive = StoryDirective()
        flash_results = flash_condition_results or {}
        already_fired = set(pre_auto_fired_ids or [])

        if not chapter:
            return directive

        for event in chapter.events:
            if event.id in already_fired:
                continue
            if event.id in ctx.events_triggered and not event.is_repeatable:
                continue
            if event.cooldown_rounds > 0:
                last_round = ctx.event_cooldowns.get(event.id)
                if isinstance(last_round, int):
                    if (ctx.rounds_in_chapter - last_round) < event.cooldown_rounds:
                        continue
            if not event.trigger_conditions.conditions:
                continue

            result = self.engine.evaluate(event.trigger_conditions, ctx)

            if result.pending_flash:
                # 检查 Flash 是否确认了语义条件
                if result.satisfied:
                    all_flash_ok = all(
                        flash_results.get(self._make_flash_condition_id(event.id, idx), False)
                        for idx, _ in enumerate(result.pending_flash)
                    )
                    if all_flash_ok:
                        directive.fired_events.append(event)
                        directive.side_effects.extend(event.side_effects)
                        if event.narrative_directive:
                            directive.narrative_injections.append(event.narrative_directive)
            elif result.satisfied:
                # 操作执行后新满足的纯机械条件（Pre-Flash 时可能未满足）
                directive.fired_events.append(event)
                directive.side_effects.extend(event.side_effects)
                if event.narrative_directive:
                    directive.narrative_injections.append(event.narrative_directive)

        # 章节转换评估
        if chapter.transitions:
            directive.chapter_transition = self._evaluate_transitions(
                chapter.transitions, ctx, flash_results
            )

        # 节奏评估
        rounds_since = ctx.rounds_since_last_progress
        pacing_action, pacing_detail = self._evaluate_pacing(
            chapter.pacing, ctx.rounds_in_chapter, rounds_since
        )
        directive.pacing_action = pacing_action
        directive.pacing_detail = pacing_detail

        # 节奏 narrative 注入
        if pacing_action == "hint":
            hint_text = self._get_hint_text(chapter.pacing, rounds_since)
            if hint_text:
                directive.narrative_injections.append(hint_text)
        elif pacing_action == "accelerate":
            directive.narrative_injections.append(
                "[GM加速] 章节回合数已接近上限，主动推进关键事件。"
            )

        # 失败恢复：超过 max_rounds 且无正常 transition 满足
        if (
            ctx.rounds_in_chapter > chapter.pacing.max_rounds
            and directive.chapter_transition is None
        ):
            directive.chapter_transition = self._find_failure_transition(
                chapter.transitions, ctx, flash_results
            )
            if directive.chapter_transition is None:
                # 没有任何 transition 可用 → 注入强制推进指令
                directive.narrative_injections.append(
                    "[GM强制推进] 章节已严重超时且无可用转换，"
                    "请直接描述场景变化并推动进入下一阶段。"
                )

        return directive

    # ----- 节奏控制 -----

    @staticmethod
    def _evaluate_pacing(
        pacing: PacingConfig,
        rounds_in_chapter: int,
        rounds_since_progress: int,
    ) -> Tuple[Optional[str], str]:
        """评估节奏，返回 (action, detail)。"""
        if rounds_in_chapter < pacing.min_rounds:
            return "decelerate", f"回合 {rounds_in_chapter}/{pacing.min_rounds}，让玩家自由探索"

        if rounds_in_chapter > pacing.max_rounds:
            return "accelerate", f"回合 {rounds_in_chapter} 超过上限 {pacing.max_rounds}，强制推进"

        if rounds_since_progress >= pacing.stall_threshold:
            return "hint", f"无进展 {rounds_since_progress} 回合（阈值 {pacing.stall_threshold}）"

        return None, ""

    @staticmethod
    def _get_hint_text(pacing: PacingConfig, rounds_stalled: int) -> str:
        """根据卡关回合数获取逐级升级的提示。"""
        if not pacing.hint_escalation:
            return ""
        escalation = pacing.hint_escalation
        stall = pacing.stall_threshold
        if stall <= 0:
            idx = 0
        else:
            idx = min((rounds_stalled - stall) // 2, len(escalation) - 1)
        idx = max(0, idx)
        level = escalation[idx]

        hint_map = {
            "subtle_environmental": "[GM暗示] 通过环境描写暗示方向（风声、远处光亮、NPC的目光等）。",
            "npc_reminder": "[GM暗示] 让NPC自然地提及与章节目标相关的内容。",
            "direct_prompt": "[GM引导] 直接建议玩家一个可能的行动方向。",
            "forced_event": "[GM加速] 强制触发一个推进剧情的事件。",
        }
        return hint_map.get(level, "")

    # ----- 章节转换评估 -----

    def _evaluate_transitions(
        self,
        transitions: List[ChapterTransition],
        ctx: GameContext,
        flash_results: Dict[str, bool],
    ) -> Optional[ChapterTransition]:
        """评估章节转换，返回优先级最高的满足条件转换。"""
        candidates: List[Tuple[int, ChapterTransition]] = []

        for trans in transitions:
            if not trans.conditions.conditions:
                # 无条件转换（通常是 failure fallback）
                candidates.append((trans.priority, trans))
                continue

            result = self.engine.evaluate(trans.conditions, ctx)
            if result.satisfied and not result.pending_flash:
                candidates.append((trans.priority, trans))

        if not candidates:
            return None

        # 选优先级最高的
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    def _find_failure_transition(
        self,
        transitions: List[ChapterTransition],
        ctx: GameContext,
        flash_results: Dict[str, bool],
    ) -> Optional[ChapterTransition]:
        """查找 failure 类型的转换作为失败恢复。"""
        for trans in transitions:
            if trans.transition_type != "failure":
                continue
            # failure transition 允许无条件触发
            if not trans.conditions.conditions:
                return trans
            result = self.engine.evaluate(trans.conditions, ctx)
            if result.satisfied and not result.pending_flash:
                return trans
        return None

    @staticmethod
    def _make_flash_condition_id(event_id: str, index: int) -> str:
        """为事件内第 N 个语义条件生成稳定 ID。"""
        return f"{event_id}__flash_{index}"
