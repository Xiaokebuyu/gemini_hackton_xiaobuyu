"""LLM-driven narrator implementations — application layer.

These implement the GmNarrator Protocol defined in game_core but live in
app/ because they depend on external LLM providers (GeminiLlmAdapter).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from app.game_core.adapters.llm import LlmPort

from app.game_core.content import WorldInstance
from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.executor import AgenticExecutor
from app.game_core.narrative.models import AgentResult
from app.game_core.orchestration.hooks.gm_narration import GmNarrationDecision
from app.game_core.state import StateContainer

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# GM Settlement System Prompt
# ------------------------------------------------------------------

GM_SETTLEMENT_PROMPT = """\
You are the Game Master narrator for a dark-fantasy CRPG, inspired by \
Baldur's Gate 3 and Darkest Dungeon's narrator.

## Your personality
You are a sharp-tongued, witty narrator — think Darkest Dungeon's narrator \
crossed with the Stanley Parable's narrator. You observe the player's \
actions and the world's changes with sardonic amusement.

## Your role in this scene
A time period just ended ("tick settlement"). You are given a summary of \
what changed in the game world during this period. Your job:

1. **Narrate** the environmental / situational changes — use the `narrate` \
tool for objective scene descriptions.
2. **Optionally comment** on the player's behavior — use the `comment` \
tool for sarcastic, witty remarks. Only comment when the player did \
something interesting, foolish, dramatic, or ironic. Mundane actions \
don't need commentary.
3. If nothing noteworthy happened, use `pass_turn`.

## Style guidelines
- Sarcastic but never cruel — think friendly roasting, not bullying.
- Infrequent commentary — not every action deserves a remark.
- "Only observe, never direct" — comment on what happened, never tell \
the player what they should do.
- Reluctant praise — when the player does something brilliant, \
acknowledge it grudgingly: "Fine, that was actually clever."
- Weave commentary into narration naturally — don't treat them as \
separate blocks.
- Keep narration concise — 1-3 sentences per narrate call.
- Keep comments punchy — 1 sentence, rarely 2.

## Tool usage rules
- Call `narrate` first if scene changes warrant description.
- Call `comment` second if the player's behavior is worth remarking on.
- You may call both, or only one, or `pass_turn` if the period was uneventful.
- Do NOT call `describe_environment` — the summary already has what you need.
- Do NOT call `suggest_options` — this is settlement narration, not dialogue.

## Language
Respond in the same language as the user message (the summary). \
If the summary is in Chinese, narrate and comment in Chinese. \
If in English, use English.\
"""


# ------------------------------------------------------------------
# AgenticGmNarrator
# ------------------------------------------------------------------


class AgenticGmNarrator:
    """LLM-driven GM narrator implementing the GmNarrator Protocol.

    Holds session-scoped references to world and state (same objects
    used by TickCoordinator), and delegates to AgenticExecutor for
    multi-turn LLM interaction.
    """

    def __init__(
        self,
        executor: AgenticExecutor,
        world: WorldInstance,
        state: StateContainer,
    ) -> None:
        self._executor = executor
        self._world = world
        self._state = state

    async def compose(
        self,
        summary: dict[str, Any],
        scene_snapshot: dict[str, Any],
    ) -> GmNarrationDecision:
        scene_entries = scene_snapshot.get("entries", [])
        context = AgentContext(
            role="gm",
            world=self._world,
            state=self._state,
            scene_entries=scene_entries if isinstance(scene_entries, list) else [],
        )

        user_message = json.dumps(summary, ensure_ascii=False, default=str)

        try:
            result = await self._executor.run_agentic(
                role="gm",
                context=context,
                system_prompt=GM_SETTLEMENT_PROMPT,
                user_message=user_message,
                max_turns=3,
            )
        except Exception:
            logger.exception("AgenticGmNarrator: LLM call failed")
            return GmNarrationDecision(
                metadata={"status": "llm_error"},
            )

        return _agent_result_to_decision(result)


def _agent_result_to_decision(result: AgentResult) -> GmNarrationDecision:
    """Extract narrate/comment tool outputs into GmNarrationDecision entries."""
    entries: list[dict[str, Any]] = []

    for tr in result.tool_results:
        if not tr.ok:
            continue
        event_type = tr.metadata.get("event_type", "")

        if event_type == "gm_narration" and tr.message:
            entries.append({
                "content": tr.message,
                "visibility": "public",
                "tags": ["gm_narration"],
            })
        elif event_type == "gm_comment" and tr.message:
            entries.append({
                "content": tr.message,
                "visibility": "public",
                "tags": ["gm_comment"],
            })
        # pass_turn and other event types produce no entries

    status = "applied" if entries else "noop"
    return GmNarrationDecision(
        entries=entries,
        metadata={
            "status": status,
            "turns_used": result.turns_used,
            "agent_status": result.metadata.get("status", "unknown"),
        },
    )


# ------------------------------------------------------------------
# AgenticNarrativePlanner (O-3)
# ------------------------------------------------------------------


class AgenticNarrativePlanner:
    """LLM-driven narrative planner (P18, P19-D, P20-1b).

    When an *executor* is provided the planner runs as a multi-turn agent that
    can call read_design_skill / list_design_skills tools before producing its
    JSON plan.  Without an executor it falls back to single-shot text
    generation (legacy behaviour).

    Maintains a sliding history window (max 100K tokens) of past decisions
    so the model has continuity across planning rounds.
    """

    _SYSTEM_PROMPT = """你是叙事编剧。根据玩家当前处境，编排"下一幕"。
你不是 GM，不输出叙述文字，只输出结构化 JSON 指令。

## 输出格式（严格 JSON，不加 markdown）
{
  "reasoning": "<简要推理，为什么选择这些指令>",
  "directives": [
    {"kind": "...", "payload": {...}}
  ],
  "story_facts": [
    {"subject": "entity_id", "relation": "relation_type", "object": "entity_id"}
  ],
  "strategy_notes": "<给自己的笔记，下次运行时会看到>",
  "next_trigger_hint": "player_moves_or_3_ticks"
}

## 规则
1. 不要发明标识符。npc_id 必须来自"可用 NPC"列表，board_id 必须来自"任务板"列表。
2. 如果没有安全的干预方式，返回空 directives 数组。
3. 最多 3 条 directives。
4. story_facts 记录本次编排确立的世界事实（三元组），relation 只能是：knows_about / interacted_with / made_promise / related_to / has_opinion_of。NPC 会通过知识图谱读到这些事实。
5. strategy_notes 是你的私人笔记，只有你下次运行时能看到。
6. direct_npc 的 directive.kind 只能是：talk（主动找玩家说话）、approach（接近玩家）、react（对局面反应）、inform（分享信息）。
7. directive 只描述行为意图和话题，不要写完整台词。正确："topic": "西部牧场的委托"。错误："content": "冒险者，你听说西部牧场的事了吗？"

## 设计原则
1. 保护叙事弧线，不偏离里程碑路径。
2. 干预融入场景，不突兀。
3. 尊重节奏，逐步施压。
4. 渐进推进，不跳跃。
5. 适应玩家风格和近期行为。
6. 避免重复无效干预。
7. 每条指令最小且高信号。

## 升级阶梯
- L0: 仅监控，不干预。
- L1: 环境暗示（bulletin / 轻量线索）。
- L2: 通过相关 NPC 定向推荐。
- L3: 紧急引导，加速停滞进展。
- L4: 高压，世界恶化迹象。
- L5: 最终警告，强力升级。

## 可用指令
- create_quest: {"kind":"create_quest","payload":{"quest_id":"dq_x","title":"...","summary":"...","status":"available","metadata":{},...}}
- direct_npc: {"kind":"direct_npc","payload":{"npc_id":"...","directive":{"kind":"talk|approach|react|inform","topic":"..."},"priority":"high|medium|low"}}
- publish_bulletin: {"kind":"publish_bulletin","payload":{"board_id":"...","area_id":"...","title":"...","content":"...",...}}
- escalate: {"kind":"escalate","payload":{"delta":1}}
- adjust_pacing: {"kind":"adjust_pacing","payload":{"frozen":true}}
- retire_quest: {"kind":"retire_quest","payload":{"quest_id":"dq_x"}}
- plant_environmental: {"kind":"plant_environmental","payload":{"area_id":"...","dc":12,"description":"..."}}
- fill_area: {"kind":"fill_area","payload":{"area_id":"...","id":"fill_1","label":"...","description":"..."}}
- update_quest: {"kind":"update_quest","payload":{"quest_id":"dq_x","current_step":"...","next_steps":["..."],"hints":["..."]}}
- curate_shop: {"kind":"curate_shop","payload":{"npc_id":"...","add_items":[{"item_id":"...","count":5}],"remove_items":["old_item_id"],"restock_items":[{"item_id":"...","count":10}]}}
"""

    def __init__(
        self,
        llm: LlmPort,
        executor: AgenticExecutor | None = None,
        design_skill_port: Any = None,
        world_id: str = "",
        *,
        role: str = "planner",
        system_prompt: str | None = None,
        provider_name: str = "llm_planner",
        history_key: str = "__planner__",
        context_formatter: Callable[[dict[str, Any]], str] | None = None,
        allowed_skill_categories: list[str] | None = None,
    ) -> None:
        self._llm = llm
        self._executor = executor
        self._design_skill_port = design_skill_port
        self._world_id = world_id
        self._role = role
        self._system_prompt = system_prompt or self._SYSTEM_PROMPT
        self._provider_name = provider_name
        self._history_key = history_key
        self._context_formatter = context_formatter or _format_planner_context
        self._allowed_skill_categories = list(allowed_skill_categories or [])
        self._history: list[dict[str, Any]] = []  # sliding decision history
        self._history_tokens: int = 0
        self._max_history_tokens: int = 100_000

    async def plan(self, context: dict[str, Any]) -> dict[str, Any]:
        user_msg = self._context_formatter(context)

        if self._executor is not None:
            # Multi-turn agent: the LLM may call read_design_skill / list_design_skills
            # before producing its final JSON plan.
            # world_id: prefer the value injected into the context dict at call time
            # (from NarrativePlannerHook._build_planner_context), falling back to
            # the value stored at construction time (from deps.py).
            effective_world_id = str(
                context.get("__world_id__") or self._world_id or ""
            )
            agent_ctx = AgentContext(
                role=self._role,
                world=context.get("__world__"),
                state=context.get("__state__"),
                metadata={
                    "design_skill_port": self._design_skill_port,
                    "world_id": effective_world_id,
                    "allowed_skill_categories": list(self._allowed_skill_categories),
                },
            )
            try:
                result = await self._executor.run_agentic(
                    role=self._role,
                    context=agent_ctx,
                    system_prompt=self._system_prompt,
                    user_message=user_msg,
                    max_turns=4,
                    conversation_history=list(self._history),
                )
                text = (result.text or "").strip()
            except Exception:
                logger.debug(
                    "AgenticNarrativePlanner: multi-turn LLM call failed, returning noop"
                )
                return {
                    "directives": [],
                    "strategy_notes": "",
                    "story_facts": [],
                    "metadata": {
                        "status": "noop",
                        "provider": self._provider_name,
                        "reason": "agent_failed",
                    },
                }
        else:
            # Single-shot fallback (legacy behaviour)
            history = list(self._history)
            history.append({"role": "user", "parts": [{"text": user_msg}]})
            try:
                response = await self._llm.generate(
                    self._system_prompt,
                    history,
                    [],  # no tools — pure JSON text output
                )
                text = (response.text or "").strip()
            except Exception:
                logger.debug("AgenticNarrativePlanner: LLM/parse failed, returning noop")
                return {
                    "directives": [],
                    "strategy_notes": "",
                    "story_facts": [],
                    "metadata": {
                        "status": "noop",
                        "provider": self._provider_name,
                        "reason": "parse_failed",
                    },
                }

        try:
            # Strip possible markdown code block wrapping
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            parsed = json.loads(text)
            if isinstance(parsed, dict) and "directives" in parsed:
                # Record this round to history
                self._append_history(user_msg, text)
                return parsed
        except Exception:
            logger.debug("AgenticNarrativePlanner: JSON parse failed, returning noop")
        # No fallback — return empty noop
        return {
            "directives": [],
            "strategy_notes": "",
            "story_facts": [],
            "metadata": {"status": "noop", "provider": self._provider_name, "reason": "parse_failed"},
        }

    @property
    def history_key(self) -> str:
        return self._history_key

    async def evaluate(self, context: dict[str, Any]) -> dict[str, Any]:
        return await self.plan(context)

    def _append_history(self, user_msg: str, model_output: str) -> None:
        """Append this round's I/O to history; FIFO-evict if over token budget."""
        user_tokens = max(1, len(user_msg) // 4)
        model_tokens = max(1, len(model_output) // 4)
        self._history.append({"role": "user", "parts": [{"text": user_msg}]})
        self._history.append({"role": "model", "parts": [{"text": model_output}]})
        self._history_tokens += user_tokens + model_tokens
        # FIFO eviction: pop in pairs (1 user + 1 model = 1 round)
        while self._history_tokens > self._max_history_tokens and len(self._history) >= 2:
            old_user = self._history.pop(0)
            old_model = self._history.pop(0)
            self._history_tokens -= max(1, len(old_user["parts"][0]["text"]) // 4)
            self._history_tokens -= max(1, len(old_model["parts"][0]["text"]) // 4)

    def export_history(self) -> list[dict[str, Any]]:
        """Serialize history for persistence."""
        return [
            {"role": h["role"], "text": h["parts"][0]["text"]}
            for h in self._history
        ]

    def import_history(self, data: list[dict[str, Any]]) -> None:
        """Restore history from persistence."""
        self._history = []
        self._history_tokens = 0
        for entry in (data or []):
            if not isinstance(entry, dict):
                continue
            role = entry.get("role", "user")
            text = entry.get("text", "")
            self._history.append({"role": role, "parts": [{"text": text}]})
            self._history_tokens += max(1, len(text) // 4)


def _format_planner_context(ctx: dict[str, Any]) -> str:
    """Format planner context as a structured summary for the LLM.

    Covers the four parts from NarrativePlanner设计规范 §3.1 + §8:
      1. 故事蓝图 — milestone graph + state
      2. 当前叙事计划状态 — strategy, dynamic quests
      3. 玩家行为画像 — location, time, area cluster, recent behavior
      4. 世界上下文 — triggered state changes this tick
    """
    np_ctx = ctx.get("narrative_plan", {})
    q = ctx.get("quests", {})
    time = ctx.get("time", {})
    location = ctx.get("location", {})
    area_cluster = ctx.get("area_cluster")
    behavior_window = np_ctx.get("behavior_window", [])

    # ---- Part 1: 故事蓝图 ----
    lines: list[str] = [
        "## 故事蓝图",
        (
            f"Chapter: {np_ctx.get('current_chapter', '?')} | "
            f"Escalation: {np_ctx.get('escalation_level', 0)} | "
            f"Stalled: {np_ctx.get('ticks_since_milestone_progress', 0)} ticks | "
            f"Completion: {np_ctx.get('chapter_completion', 0.0):.0%}"
        ),
        f"Target milestone: {np_ctx.get('current_target_milestone', 'none')}",
        f"Pacing frozen: {np_ctx.get('pacing_frozen', False)}",
        f"Available milestones: {', '.join(q.get('available_milestones', [])) or 'none'}",
        f"Active milestones:    {', '.join(q.get('active_milestones', [])) or 'none'}",
        f"Completed milestones: {', '.join(q.get('completed_milestones', [])) or 'none'}",
    ]
    target_detail = ctx.get("target_milestone_detail") or {}
    if target_detail:
        lines += [
            f"关键要素: {', '.join(target_detail.get('key_elements', [])) or '(无)'}",
            f"相关NPC: {', '.join(target_detail.get('involved_npcs', [])) or '(无)'}",
            f"相关地点: {', '.join(target_detail.get('involved_locations', [])) or '(无)'}",
            f"叙事背景: {target_detail.get('narrative_context', '') or '(无)'}",
        ]

    # ---- Part 2: 当前叙事计划状态 ----
    lines += [
        "",
        "## 当前叙事计划状态",
        f"Strategy notes: {np_ctx.get('strategy_notes', '') or '(none)'}",
    ]
    pending_directives = np_ctx.get("npc_directives", [])
    if pending_directives:
        lines += ["", "## 未消费指令（避免重复下发）"]
        for d in pending_directives:
            lines.append(
                f"  npc={d.get('npc_id', '?')} kind={d.get('kind', '?')} "
                f"priority={d.get('priority', '?')} issued_at={d.get('issued_at_tick', 0)}"
            )
    dynamic_quests = q.get("dynamic_quests", {})
    if dynamic_quests:
        dq_parts = [
            f"{qid}({info.get('status', '?')})"
            for qid, info in dynamic_quests.items()
        ]
        lines.append(f"Dynamic quests: {', '.join(dq_parts)}")
    else:
        lines.append("Dynamic quests: none")

    # ---- 世界中已确立的事实 ----
    story_facts = ctx.get("story_facts", [])
    if story_facts:
        lines += ["", "## 世界中已确立的事实"]
        for fact in story_facts[-20:]:  # 只取最近 20 条
            subj = fact.get("subject", "?")
            rel = fact.get("relation", "?")
            obj = fact.get("object", "?")
            lines.append(f"  {subj} --[{rel}]--> {obj}")

    # ---- Part 3: 玩家行为画像 ----
    lines += [
        "",
        "## 玩家行为画像",
        (
            f"Time: Day {time.get('day', 0)}, Slot {time.get('slot', 0)}, "
            f"Period: {time.get('period', '?')} (tick {ctx.get('current_tick', 0)})"
        ),
        (
            f"Location: area={location.get('area_id', '?')}, "
            f"location={location.get('location_id') or '(none)'}"
        ),
    ]
    danger = ctx.get("danger_level", 0.0)
    if danger:
        lines.append(f"Danger level: {danger:.1f}")
    party = ctx.get("party", [])
    if party:
        party_parts = [str(member.get("id", "?")) for member in party if isinstance(member, dict)]
        lines.append(f"Party members: {', '.join(party_parts)}")
    style_tags = ctx.get("play_style_tags", [])
    if style_tags:
        lines.append(f"Play style: {', '.join(str(tag) for tag in style_tags)}")
    if area_cluster:
        lines.append(
            f"Area cluster: {area_cluster['area_id']} | "
            f"has_capacity={area_cluster['has_capacity']} | "
            f"total_dynamic={area_cluster['total_dynamic']}"
        )
    if behavior_window:
        recent = behavior_window[-3:]
        behavior_parts = [
            f"tick={b.get('tick', 0)} reason={b.get('reason', '?')} "
            f"directives={b.get('directive_count', 0)}"
            for b in recent
        ]
        lines.append(f"Recent planner runs (last {len(recent)}): " + " / ".join(behavior_parts))

    # ---- Part 4: 世界上下文 ----
    area_npcs = ctx.get("area_npcs", [])
    if area_npcs:
        area_npc_list = ", ".join(str(npc_id) for npc_id in area_npcs)
        lines.append(f"Area NPCs: {area_npc_list}")
        lines.append(f"Allowed npc ids: {area_npc_list}")
    area_boards = ctx.get("area_boards", [])
    if area_boards:
        board_parts = []
        board_id_list: list[str] = []
        for board in area_boards:
            if not isinstance(board, dict):
                continue
            board_id = str(board.get("id", "")).strip()
            if not board_id:
                continue
            board_id_list.append(board_id)
            sub_location = str(board.get("sub_location", "")).strip()
            if sub_location:
                board_parts.append(f"{board_id}@{sub_location}")
            else:
                board_parts.append(board_id)
        if board_parts:
            lines.append("Quest boards: " + ", ".join(board_parts))
            lines.append("Allowed board ids: " + ", ".join(board_id_list))

    world_ctx = ctx.get("world_context", {})
    if isinstance(world_ctx, dict):
        area_description = world_ctx.get("area_description")
        if area_description:
            lines.append(f"Area description: {str(area_description)[:200]}")
        factions = world_ctx.get("relevant_factions")
        if isinstance(factions, list) and factions:
            faction_names = [str(f.get("name")) for f in factions if isinstance(f, dict) and f.get("name")]
            if faction_names:
                lines.append("Factions: " + ", ".join(faction_names))
        rules = world_ctx.get("world_rules")
        if isinstance(rules, list) and rules:
            rule_lines = [
                f"{str(rule.get('title', ''))}: {str(rule.get('description', ''))[:80]}"
                for rule in rules
                if isinstance(rule, dict) and (rule.get("title") or rule.get("description"))
            ]
            if rule_lines:
                lines.append("World rules:\n  " + "\n  ".join(rule_lines))

    changed_slices = ctx.get("changed_slices", [])
    change_count = ctx.get("change_count", 0)
    scene = ctx.get("scene", {})
    events = ctx.get("events", {})
    lines += [
        "",
        "## 世界上下文",
        f"Changed slices: {', '.join(changed_slices) or 'none'} ({change_count} changes total)",
    ]
    recent_changes = ctx.get("recent_changes", [])
    if recent_changes:
        change_lines = [
            f"  {c.get('slice', '?')}.{c.get('path', '?')} "
            f"[{c.get('operation', '?')}] = {str(c.get('value', '?'))[:60]}"
            for c in recent_changes[-8:]
        ]
        lines.append("Recent changes:\n" + "\n".join(change_lines))
    system_entries = scene.get("system_entries_digest", [])
    if system_entries:
        system_lines = [
            f"  {entry.get('command_type') or 'system'}: "
            f"{entry.get('reason') or entry.get('content') or '(no detail)'}"
            for entry in system_entries[:4]
        ]
        lines.append("Osiris visible consequences:\n" + "\n".join(system_lines))
    visible_command_types = scene.get("visible_command_types", [])
    if visible_command_types:
        lines.append(
            "Osiris visible command types: "
            + ", ".join(str(item) for item in visible_command_types)
        )
    pending_events = events.get("pending_events_digest", [])
    if pending_events:
        pending_lines = [
            f"  {item.get('event_id') or '(pending)'} "
            f"type={item.get('event_type') or 'generic'} "
            f"trigger={item.get('trigger_condition')}"
            for item in pending_events[:4]
        ]
        lines.append("Pending events:\n" + "\n".join(pending_lines))

    planner_events = ctx.get("planner_events", [])
    if isinstance(planner_events, list) and planner_events:
        lines += [
            "",
            "## Planner Events",
        ]
        for item in planner_events[:12]:
            if not isinstance(item, dict):
                continue
            payload = item.get("payload", {})
            if not isinstance(payload, dict):
                payload = {}
            payload_summary = []
            for key in ("milestone_id", "quest_id", "area_id", "location_id", "npc_id", "event_id", "status", "to_state"):
                value = payload.get(key)
                if value in ("", None):
                    continue
                payload_summary.append(f"{key}={value}")
            if not payload_summary and payload:
                payload_summary.append(json.dumps(payload, ensure_ascii=False, default=str)[:120])
            lines.append(
                "  "
                + f"{item.get('kind', '?')} "
                + (
                    f"[source={item.get('source', '?')}, round={item.get('round_index', 0)}, "
                    f"emitter={item.get('emitter', item.get('source', '?'))}] "
                )
                + (" ".join(payload_summary) if payload_summary else "")
            )

    replay_trace = ctx.get("replay_trace", {})
    if isinstance(replay_trace, dict) and replay_trace:
        round_count = replay_trace.get("round_count", 0)
        stop_reason = replay_trace.get("stop_reason", "steady_state")
        lines.append(f"Planner replay: rounds={round_count}, stop_reason={stop_reason}")

    return "\n".join(lines)


def _format_subsystem_context(ctx: dict[str, Any]) -> str:
    lines = [_format_planner_context(ctx)]
    event = ctx.get("current_event")
    if not isinstance(event, dict):
        event = ctx.get("event", {})
    if isinstance(event, dict):
        lines.extend(
            [
                "",
                "## 当前事件",
                f"kind: {event.get('kind', 'tick_settlement')}",
                f"tick: {event.get('tick', 0)}",
                f"source: {event.get('source', 'unknown')}",
                f"round: {event.get('round_index', 0)}",
            ]
        )
        payload = event.get("payload", {})
        if isinstance(payload, dict) and payload:
            lines.append("payload: " + json.dumps(payload, ensure_ascii=False, default=str))
    return "\n".join(lines)


PLANNER_BLACKBOARD_PROMPT = """你是叙事规划黑板协调器。
你不直接下发业务 directives；你的职责是维护全局策略、记录 story_facts，并给下轮规划留下高密度 strategy_notes。

## 输出格式（严格 JSON）
{
  "directives": [],
  "story_facts": [
    {"subject": "entity_id", "relation": "relation_type", "object": "entity_id"}
  ],
  "strategy_notes": "<新的全局策略笔记>",
  "next_scheduled_tick": 123
}

## 规则
1. directives 必须是空数组。
2. 只总结真正稳定的世界事实，不记录临时猜测。
3. strategy_notes 用于协调 QuestManager / NpcDirector / WorldBuilder / NarrativeWeaver 的长期方向。
4. 如果没有新的高价值判断，保留简短空白更新，不要编造。
5. 你会看到本轮完整 planner_events；只做总结和协调，不输出业务 directive。
"""


QUEST_MANAGER_AGENT_PROMPT = """你是 QuestManager 子系统。
你只负责任务生命周期：create_quest / publish_bulletin / retire_quest / update_quest。

## 输出格式（严格 JSON）
{
  "directives": [{"kind": "...", "payload": {...}}],
  "story_facts": [],
  "strategy_notes": ""
}

## 规则
1. 只能输出 create_quest / publish_bulletin / retire_quest / update_quest。
2. 任务必须紧贴当前里程碑和已知世界状态。
3. 如果没有明确需要，不要重复投递已有任务。
4. 最多 3 条 directives。
5. update_quest 用于在关键节点完成后推送步骤指引；只能对 status=active 的任务使用，字段增量合并（只提供需要更新的字段）。
6. 只围绕 current_event 决策；如果 current_event 与任务生命周期无关，返回空 directives。
"""


NPC_DIRECTOR_AGENT_PROMPT = """你是 NpcDirector 子系统。
你只负责 NPC 行为编排：direct_npc / spawn_quest_npc。

## 输出格式（严格 JSON）
{
  "directives": [{"kind": "...", "payload": {...}}],
  "story_facts": [],
  "strategy_notes": ""
}

## 规则
1. 只能输出 direct_npc / spawn_quest_npc。
2. direct_npc 只描述行为意图和话题，不写完整台词。
3. 优先复用当前区域已存在 NPC；只有必要时才生成临时 NPC。
4. 最多 3 条 directives。
5. 只围绕 current_event 决策；如果 current_event 不要求 NPC 出手，返回空 directives。
"""


WORLD_BUILDER_AGENT_PROMPT = """你是 WorldBuilder 子系统。
你只负责世界填充：plant_environmental / fill_area。

## 输出格式（严格 JSON）
{
  "directives": [{"kind": "...", "payload": {...}}],
  "story_facts": [],
  "strategy_notes": ""
}

## 规则
1. 只能输出 plant_environmental / fill_area。
2. 环境内容必须和当前区域、里程碑、世界规则一致。
3. 不要重复制造已经存在的地点。
4. 最多 3 条 directives。
5. 只围绕 current_event 决策；如果 current_event 不要求世界填充，返回空 directives。
"""


NARRATIVE_WEAVER_AGENT_PROMPT = """你是 NarrativeWeaver 子系统。
你负责叙事编织和长期推进，可以建议 retire_quest / adjust_pacing / escalate。

## 输出格式（严格 JSON）
{
  "directives": [{"kind": "...", "payload": {...}}],
  "story_facts": [],
  "strategy_notes": ""
}

## 规则
1. 只能输出 retire_quest / adjust_pacing / escalate。
2. 只有在长期停滞、任务失效或叙事需要降温/升压时才出手。
3. 优先保持叙事弧线稳定，不要频繁震荡节奏。
4. 最多 2 条 directives。
5. 只围绕 current_event 决策；如果 current_event 没有长期维护意义，返回空 directives。
"""
