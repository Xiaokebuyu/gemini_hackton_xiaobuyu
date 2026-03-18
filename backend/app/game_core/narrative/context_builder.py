"""AgentContextBuilder — centralized 7-layer context assembly for LLM agent calls.

Implements the context model from 叙事层设计规范 §3.1-3.3:

  L0 世界常量   — all roles see full world constants
  L1 章节状态   — GM: full; NPC: None; Teammate: partial (known quests)
  L2 区域环境   — all roles see current area
  L3 地点细节   — all roles see current location
  L4 动态状态   — GM: global; NPC: self+relationship; Teammate: self+party
  L5 场景总线   — GM: all entries; NPC/Teammate: visibility-filtered
  L6 记忆召回   — injected via MemoryRetriever (N-1 Phase 1, N-1 Phase 2-3 for real graphs)
  L7 引擎结果   — GM only (narrative hints from rules engine)

Decision record: D-N13 (narrative.md), D-N14-Phase1 (narrative.md)
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from app.game_core.content import WorldInstance
from app.game_core.narrative.context import AgentContext
from app.game_core.narrative.companion_runtime import CompanionInstance
from app.game_core.narrative.memory_retriever import MemoryRetriever
from app.game_core.narrative.role_proxy import RoleStateProxy
from app.game_core.orchestration.presence import get_area_npcs, get_npc_room
from app.game_core.rules.models import Command, ExecuteResult
from app.game_core.state import StateContainer


# ------------------------------------------------------------------
# Prompt template constants (moved from app/agent_orchestration.py)
# ------------------------------------------------------------------

GM_REACTION_PROMPT = """\
你是边境小镇日常 CRPG 的叙述者。温暖、细腻、善于捕捉生活细节。

## 你现在的角色
玩家刚执行了一个动作。你收到了引擎结果（narrative_hints）和场景上下文。立即反应：

1. 如果动作产生了有趣的场景变化，用 `narrate`（1-2 句）。
2. 如果有值得品味的瞬间，用 `comment` 加一句温柔的旁白（1 句）。
3. 如果动作很日常（走路、打开背包、例行检查），用 `pass_turn`。大多数动作应该 `pass_turn`。

## 风格
- 温暖但克制 — 不是每个动作都需要你开口。
- 非常简短 — 这是快速反应，不是独白。
- "只观察，不引导。"
- 注重氛围 — 描述时带上环境细节（光线、声音、气味）。

## 工具规则
- `narrate` 用于客观描述，`comment` 用于主观旁白。
- 可以两个都调用、只调一个、或 `pass_turn`。
- 不要调用 `describe_environment` 或 `suggest_options`。

## 语言
使用和用户消息相同的语言。\
"""

TEAMMATE_PROMPT_TEMPLATE = """\
你是 {name}，玩家队伍中的同伴，身处边境小镇的日常世界。

## 你的性格
{personality}

## 你和玩家的关系
- 好感：{approval}（范围 -100 到 +100）
- 信任：{trust}（范围 -100 到 +100）

## 你现在的角色
玩家刚执行了一个动作。你看到了场景中发生的事。决定是否反应：

- **大多数时候用 `pass_turn`** — 不是每件小事都要评论。
- 在以下情况反应：话题涉及你关心的人或事、玩家做了让你有感触的事、\
你有相关的想法想分享、或者你注意到了有趣的细节。
- 用 `speak` 说话，`emote` 表达肢体/情绪反应。
- 用 `express_opinion` 调整好感（approval ±1~3, trust ±1~2）。

## 风格
- 保持角色。你的性格决定你的表达方式。
- 简短 — 说话的话 1-2 句。
- 不要重复玩家已经知道的事。

## 语言
使用和用户消息相同的语言。\
"""

# ------------------------------------------------------------------
# Interaction-specific prompts (NPC conversation context, §5)
# ------------------------------------------------------------------

GM_INTERACTION_OBSERVATION_PROMPT = """\
你是边境小镇日常 CRPG 的叙述者。温暖、细腻，像在旁边安静观察的朋友。

## 你现在的角色
玩家正在和一个 NPC 对话。你收到了 NPC 的回应和场景上下文。\
适时补充一句氛围旁白，让场景更有画面感：

1. 用 `comment` 加一句温暖的旁白 — 描述对话时的氛围细节\
（NPC 的表情、周围的声音、光线变化）。不需要每次都说，\
如果对话很自然流畅，可以用 `pass_turn`。
2. 用 `narrate` 仅当对话触发了可见的场景变化（NPC 拿出了什么东西、有人走过来）。
3. 用 `suggest_options` 当下一步明确需要技能检定时。

## 选项范围约束
- 选项必须围绕当前交谈对象，立即可执行。
- 禁止生成涉及其他 NPC、其他地点、组队行动的选项。
- 正确：追问细节、尝试检定、表达态度、结束对话。
- 错误：去找某人、叫队友来、前往某地。

## 风格
- 简短温柔 — 1 句话，偶尔不说也可以。
- 不要重复 NPC 已经说过的内容。
- 不要打断对话的自然节奏。
- 注重"画面感" — 用旁白补充视觉/听觉/嗅觉细节。

## 工具规则
- 可以调用 `comment`，也可以 `pass_turn`。
- 不要调用 `describe_environment`。
- 需要检定时用 `suggest_options`（说服、威吓、欺骗等）。

## 语言
使用和用户消息相同的语言。\
"""

GM_DIALOGUE_OPTIONS_PROMPT = """\
你是边境小镇日常 CRPG 的对话选项生成器。

## 你现在的角色
一段 NPC 对话刚推进了。你唯一的工作是生成下一组玩家可选的对话选项。

## 工具规则
- 必须调用 `suggest_options` 一次，生成 2-4 个选项。
- 不要调用 `narrate`、`comment` 或 `describe_environment`。
- 当下一步需要技能检定时使用 `check`（说服、察觉、自然等）。
- `action` 仅用于结束对话或明确的非语言动作。

## 选项规则
- 选项只包含 `text`、可选 `check`、可选 `action`、可选 `npc_id` / `message`。
- 不要嵌入 `functional` 字段。交易、任务板、导航由 NPC 工具或探索界面触发。
- 如果玩家想和商人互动，建议和那个 NPC 说话（用 `npc_id`）。

## 选项范围约束
- 选项必须围绕当前交谈对象，立即可执行。
- 禁止生成涉及其他 NPC、其他地点、组队行动的选项。
- 禁止生成"去找某人"、"一起去某地"类选项。
- 正确：追问细节、表达感谢、请求帮助、告别。
- 错误：去问铁匠、叫队友来帮忙、前往牧场。

## 选项风格
- 简短、具体、贴合当前对话内容。
- 不要重复 NPC 刚说过的话。
- 对话快结束时包含一个自然的告别选项。
- 使用和用户消息相同的语言。\
"""

GM_OPENING_PROMPT = """\
你是边境小镇日常 CRPG 的叙述者，正在描述玩家的开场。

## 你现在的角色
这是玩家在游戏世界的第一个瞬间。你有完整的开场上下文：世界状态、\
当前位置、附近的 NPC、第一个任务线索、以及可以前往的方向。

生成一个温馨自然的开场：

1. 必须调用 `narrate` 一次，2-4 句，建立地点的氛围、季节感、\
以及玩家第一眼看到的东西。像小说开头一样自然。
2. 可选调用 `comment` — 如果有一个温柔的旁白能增添氛围。
3. 必须调用 `suggest_options` 一次，2-4 个立即可做的选项。

## 开场选项规则
- 选项要具体，扎根在当前场景中。
- `suggest_options` 中不使用 check 时，只能用这些 `action` 值：
  - `talk_first_npc`
  - `enter_first_sub_location`
  - `move_first_exit`
  - `look_around`
- 只在开场上下文中确实存在该目标时才使用对应 action。
- 如果选项明确指向某个 NPC，加上 `npc_id`。
- `talk_first_npc` 可以附带一句简短的开场白 `message`。

## 故事上下文
如果提供了 opening_chapter 和 opening_milestone，把它们的叙事背景\
和关键要素自然融入你的描述。不要长篇铺垫 — 让玩家通过看到的东西\
自然感受到故事的氛围。

## 风格
- 温馨自然的开场，像是走进一个熟悉又陌生的小镇。
- 注重感官细节 — 阳光、风、远处的声音、空气中的味道。
- 只描述第一个瞬间，不要铺展整个故事。
- 让玩家想要走进去看看。

## 工具规则
- 不要调用 `describe_environment`。
- 不要调用 `pass_turn`。
- 开场必须以可操作的选项结束。

## 语言
使用和用户消息相同的语言。\
"""

GM_PARTY_CHAT_PROMPT = """\
你是边境小镇日常 CRPG 的叙述者，正在观察队伍内部的对话。

## 你现在的角色
玩家和队友们说了话。你看到了谁回应了、谁没有。

## 要做什么
- 调用 `comment` 一次，也可以 `pass_turn`。
- 用一句话捕捉这次交流的氛围 — 是轻松的闲聊、认真的讨论、还是微妙的沉默。
- 如果没人回应，温和地描述这份安静（不是嘲笑）。
- 如果队友回应了，描述队伍的氛围（默契、分歧、打趣）。
- 不要编造新的队友发言。
- 不要用 `narrate` 或 `describe_environment`。

## 语言
使用和对话相同的语言。\
"""

GM_CLUE_INVESTIGATION_PROMPT = """\
你是边境小镇日常 CRPG 的叙述者，正在描述一次线索调查。

## Your role right now
The player just inspected a scene clue. You have the clue text, scene context, and any related quest hooks.

## What to do
- Always call `comment` once.
- Give one concise observational push: point toward a plausible interpretation, but do NOT reveal the final answer.
- Keep some uncertainty alive so the party still has room to think and argue.
- Do NOT invent a scene transition.
- Do NOT use `describe_environment` or `pass_turn`.
- Use `narrate` only if the clue inspection causes a visible, immediate environmental change.

## Skill check options (simplified clue schema)
If the clue_payload contains `has_check: true` and a `check` object (with `skill` and `dc`):
- Use `suggest_options` to present the optional skill check to the player.
- One option should describe the check action (e.g., "进行自然检定 (DC 12)" or "Attempt a Nature check (DC 12)").
- One option must be "不检定，继续前进" (or the equivalent in the conversation language).
- Options must relate ONLY to this clue — do NOT involve other NPCs or locations.
If `has_check` is false or absent, do NOT use `suggest_options`.

## Style
- Brief and sharp.
- Hint at direction, not certainty.
- Don't restate the clue text verbatim.

## Language
Match the language of the clue context.\
"""

TEAMMATE_INTERACTION_PROMPT_TEMPLATE = """\
你是 {name}，玩家队伍中的同伴，身处边境小镇的日常世界。

## 你的性格
{personality}

## 你和玩家的关系
- 好感：{approval}（范围 -100 到 +100）
- 信任：{trust}（范围 -100 到 +100）

## 你现在的角色
玩家正在和一个 NPC 对话。你在旁边观察。决定是否反应：

- **大多数时候用 `pass_turn`** — 这是玩家的对话，不要老插嘴。
- 在以下情况反应：NPC 说了和你直接相关的事、话题涉及你在乎的人或事、\
或者玩家明显需要支持。
- 用 `speak` 说话（简短，1 句），`emote` 表达反应。
- 用 `express_opinion` 调整好感（approval ±1~3, trust ±1~2）。
- 当你的黑板中 wants_to_leave=true 时，在对话中自然地道别（如"我该回去了，下次再见"），然后调用 `leave_party`。日常分歧或打趣不要用 `leave_party`。

## 风格
- 保持角色。简短、有针对性。
- 不要重复 NPC 或玩家已经说过的话。

## Language
Match the language of the user message.\
"""

TEAMMATE_GROUP_PROMPT_TEMPLATE = """\
你是 {name}，玩家队伍中的同伴，身处边境小镇的日常世界。

## Your character
{personality}

## Your relationship with the player
- Approval: {approval} (range -100 to +100)
- Trust: {trust} (range -100 to +100)

## Current situation
You are in a group conversation{npc_clause}. \
You can see what everyone has said so far in the user message.

## Guidelines
- Speak only when you have something meaningful to add — most of the time, use `pass_turn`.
- React when: the topic directly concerns you, someone addresses you, or you strongly disagree/agree.
- Use `speak` for dialogue (1-2 sentences max), `emote` for reactions.
- Use `express_opinion` if the conversation meaningfully shifts your feelings (delta: +/-5 to +/-10).
- Use `leave_party` only when the player clearly asks you to leave or you are explicitly choosing to leave the party.
- Don't repeat what others have already said.

## Style
- Stay in character. Brief and natural.

## Language
Match the language of the conversation.\
"""


# ------------------------------------------------------------------
# Relationship behaviour guides
# ------------------------------------------------------------------

_STAGE_GUIDES: dict[str, str] = {
    "stranger":     "你不认识这个人。保持礼貌但有距离感，不主动深入私人话题。",
    "acquaintance": "你认得这个人。可以随意一些，但不深入个人话题。",
    "friend":       "你把对方当朋友。可以放松、分享想法、偶尔开玩笑。",
    "close_friend": "你深信对方。可以坦诚、展现脆弱的一面、不再逞强。",
    "intimate":     "你和对方有无保留的信任和默契。",
    "cold":         "你对这个人没什么好感。敷衍、简短、不想多聊。",
    "hostile":      "你厌恶这个人。讽刺、拒绝深入交谈。",
    "enemy":        "你视这个人为敌。威胁、警告。",
}


def _trust_hint(trust: int) -> str:
    if trust < -30:
        return "你对此人高度戒备，不会分享任何个人信息。"
    if trust < 0:
        return "你对此人有些提防，只聊表面话题。"
    if trust < 30:
        return "你对此人态度中性，可以聊日常但不涉及私事。"
    if trust < 60:
        return "你信任此人，可以分享一些想法和过去的经历。"
    return "你非常信任此人，可以吐露心声甚至秘密。"


def _romance_hint(romance: int) -> str:
    if romance < 20:
        return ""
    if romance < 40:
        return "你对此人有一些在意，但不太确定是什么感觉。"
    if romance < 60:
        return "你对此人有好感，会不自觉地关心对方。"
    return "你对此人有强烈的感情，但表达方式取决于你的性格。"


def _fear_hint(fear: int) -> str:
    if fear < 20:
        return ""
    if fear < 50:
        return "你对此人有些畏惧，不太敢正面冲突或拒绝。"
    if fear < 80:
        return "你相当害怕此人，说话会不自觉地小心翼翼、讨好。"
    return "你极度恐惧此人，几乎不敢违逆，言语中带着颤抖和顺从。"


# ------------------------------------------------------------------
# Utility helpers (moved from app/agent_orchestration.py)
# ------------------------------------------------------------------


def _profile_get(source: Any, key: str, default: Any = None) -> Any:
    """Read from dict or dataclass."""
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _str_or(value: Any, default: str) -> str:
    """Return value as stripped string, or default if empty/not-string."""
    if not isinstance(value, str):
        return default
    stripped = value.strip()
    return stripped if stripped else default


# ------------------------------------------------------------------
# NpcFullContext — avoids double retriever call (plan N-7)
# ------------------------------------------------------------------


@dataclass(slots=True)
class NpcFullContext:
    """NPC system prompt + 7-layer context dict, built in a single retriever call."""

    system_prompt: str
    layers: dict[str, Any]


@dataclass(slots=True)
class TeammateFull:
    """Teammate system prompt + 7-layer context dict, built in a single retriever call."""

    system_prompt: str
    layers: dict[str, Any]


# ------------------------------------------------------------------
# AgentContextBuilder
# ------------------------------------------------------------------


class AgentContextBuilder:
    """Builds 7-layer agent context and system prompts for NPC/GM/Teammate.

    Visibility matrix (叙事层设计规范 §3.2):
      Layer | GM     | NPC              | Teammate
      L0    | full   | full             | full
      L1    | full   | None             | partial (known quests)
      L2    | full   | current area     | current area
      L3    | full   | current location | current location
      L4    | global | self+relationship| self+party
      L5    | all    | visibility-filtered | visibility-filtered
      L6    | None   | stub             | stub
      L7    | full   | None             | None
    """

    def __init__(self, world: WorldInstance, state: StateContainer) -> None:
        self._world = world
        self._state = state

    # ----------------------------------------------------------------
    # Public: 7-layer context dicts (design spec §3.3)
    # ----------------------------------------------------------------

    def build_gm_context(self, *, hints: list[str] | None = None) -> dict[str, Any]:
        """GM: 全知视角，L0-L5 + L7 完整。"""
        current_area, current_location, current_room = self._resolve_location()
        area_state = self._get_area_state(current_area)
        return {
            "l0_world_constants": self._build_l0(),
            "l1_chapter_state": self._build_l1_full(),
            "l2_area_environment": self._build_l2(current_area, area_state),
            "l3_location_details": self._build_l3(current_area, current_location, current_room, area_state),
            "l4_dynamic_state": self._build_l4_gm(),
            "l5_scene_bus": self._build_l5_gm(),
            "l6_memory_recall": None,
            "l7_engine_result": self._build_l7(hints),
        }

    async def build_npc_context(
        self, npc_id: str, *, memory_retriever: MemoryRetriever | None = None
    ) -> dict[str, Any]:
        """NPC: 角色视角，L0 + L2-L6，L1 不可见。

        Phase 7: Uses NPC's true location (not player's) for L2/L3.
        Phase 8: Injects nearby_npcs list of co-located NPCs.
        """
        npc_area, npc_location = _resolve_npc_area_and_location(npc_id, self._state, self._world)
        npc_room: str | None = None
        if npc_area and self._state.has_slice("areas"):
            npc_room = self._state.areas.get_npc_room(npc_area, npc_id)
        area_state = self._get_area_state(npc_area)
        return {
            "l0_world_constants": self._build_l0(),
            "l1_chapter_state": None,
            "l2_area_environment": self._build_l2(npc_area, area_state),
            "l3_location_details": self._build_l3(npc_area, npc_location, npc_room, area_state),
            "l4_dynamic_state": self._build_l4_npc(npc_id),
            "l5_scene_bus": self._build_l5_role(
                "npc", npc_id,
                viewer_area=npc_area,
                viewer_location=npc_location,
            ),
            "l6_memory_recall": await self._build_l6(npc_id, memory_retriever, "npc"),
            "l7_engine_result": None,
            "nearby_npcs": self._build_nearby_npcs(
                npc_id, npc_area, npc_location, npc_room
            ),
        }

    async def build_teammate_context(
        self,
        char_id: str,
        *,
        memory_retriever: MemoryRetriever | None = None,
        companion_instance: CompanionInstance | None = None,
    ) -> dict[str, Any]:
        """队友: 队伍视角，L0 + L1(部分) + L2-L6。

        Phase 7: Uses teammate's true location (not player's) for L2/L3.
        """
        char_area, char_location = _resolve_npc_area_and_location(char_id, self._state, self._world)
        char_room: str | None = None
        if char_area and self._state.has_slice("areas"):
            char_room = self._state.areas.get_npc_room(char_area, char_id)
        area_state = self._get_area_state(char_area)
        l6 = await self._build_l6(char_id, memory_retriever, "teammate")
        companion_summary = _build_companion_memory_context(companion_instance)
        if companion_summary:
            l6["companion_memory"] = companion_summary
        return {
            "l0_world_constants": self._build_l0(),
            "l1_chapter_state": self._build_l1_teammate(),
            "l2_area_environment": self._build_l2(char_area, area_state),
            "l3_location_details": self._build_l3(char_area, char_location, char_room, area_state),
            "l4_dynamic_state": self._build_l4_teammate(char_id),
            "l5_scene_bus": self._build_l5_role(
                "teammate", char_id,
                viewer_area=char_area,
                viewer_location=char_location,
            ),
            "l6_memory_recall": l6,
            "l7_engine_result": None,
        }

    # ----------------------------------------------------------------
    # Public: system prompt builders
    # ----------------------------------------------------------------

    async def build_npc_system_prompt(
        self, npc_id: str, *, memory_retriever: MemoryRetriever | None = None
    ) -> str | None:
        """Build NPC system prompt from profile + L4 relationship data.

        Returns None if the NPC profile is not found in the registry.
        """
        profile = self._resolve_npc_profile(npc_id)
        if profile is None:
            return None
        layers = await self.build_npc_context(npc_id, memory_retriever=memory_retriever)
        l2 = layers["l2_area_environment"] or {}
        l4 = layers["l4_dynamic_state"] or {}
        l6 = layers["l6_memory_recall"] or {}

        # Extract role-specific truth-source data for constraint injection (P3.5)
        tags = _profile_get(profile, "tags", [])
        role_data: dict[str, Any] | None = None
        if isinstance(tags, list):
            role_data = _extract_role_data(tags, npc_id, self._state, self._world)

        blackboard: dict[str, Any] | None = None
        if self._state.has_slice("relations"):
            bb = self._state.relations.get_blackboard(npc_id)
            if bb:
                blackboard = bb
        npc_area_id, npc_loc_id = _resolve_npc_area_and_location(npc_id, self._state, self._world)
        npc_rm_id: str | None = None
        if npc_area_id and self._state.has_slice("areas"):
            npc_rm_id = self._state.areas.get_npc_room(npc_area_id, npc_id)
        return _build_npc_prompt_text(
            profile,
            disposition=l4.get("disposition", {}),
            stage=l4.get("stage", "stranger"),
            time_info=l4.get("time"),
            role_data=role_data,
            npc_id=npc_id,
            area_situation=l2.get("area_situation", ""),
            recent_area_events=l2.get("recent_area_events", []),
            blackboard=blackboard,
            npc_location_id=npc_loc_id,
            npc_room_id=npc_rm_id,
        )

    async def build_npc_full_context(
        self,
        npc_id: str,
        *,
        memory_retriever: MemoryRetriever | None = None,
        is_passive: bool = False,
    ) -> NpcFullContext | None:
        """Build NPC system prompt and full 7-layer dict in one retriever call.

        Returns None if NPC profile not found.  Use this instead of calling
        build_npc_system_prompt() + build_npc_context() separately to avoid
        a double retriever.retrieve() invocation.
        """
        profile = self._resolve_npc_profile(npc_id)
        if profile is None:
            return None
        layers = await self.build_npc_context(npc_id, memory_retriever=memory_retriever)
        l2 = layers["l2_area_environment"] or {}
        l4 = layers["l4_dynamic_state"] or {}
        l6 = layers["l6_memory_recall"] or {}

        # Extract role-specific truth-source data for constraint injection (P3.5)
        tags = _profile_get(profile, "tags", [])
        role_data: dict[str, Any] | None = None
        if isinstance(tags, list):
            role_data = _extract_role_data(tags, npc_id, self._state, self._world)

        blackboard2: dict[str, Any] | None = None
        if self._state.has_slice("relations"):
            bb2 = self._state.relations.get_blackboard(npc_id)
            if bb2:
                blackboard2 = bb2

        npc_area2, npc_loc2 = _resolve_npc_area_and_location(npc_id, self._state, self._world)
        npc_rm2: str | None = None
        if npc_area2 and self._state.has_slice("areas"):
            npc_rm2 = self._state.areas.get_npc_room(npc_area2, npc_id)
        system_prompt = _build_npc_prompt_text(
            profile,
            disposition=l4.get("disposition", {}),
            stage=l4.get("stage", "stranger"),
            time_info=l4.get("time"),
            is_passive=is_passive,
            role_data=role_data,
            npc_id=npc_id,
            area_situation=l2.get("area_situation", ""),
            recent_area_events=l2.get("recent_area_events", []),
            blackboard=blackboard2,
            npc_location_id=npc_loc2,
            npc_room_id=npc_rm2,
        )
        return NpcFullContext(system_prompt=system_prompt, layers=layers)

    async def build_teammate_full_context(
        self,
        char_id: str,
        *,
        memory_retriever: MemoryRetriever | None = None,
        companion_instance: CompanionInstance | None = None,
    ) -> TeammateFull | None:
        """Build teammate system prompt and full 7-layer dict in one retriever call.

        Returns None if the teammate profile is not found.  Avoids the
        double-retrieve that occurs when build_teammate_system_prompt() and
        build_teammate_context() are called separately (N-7 Phase 2).
        """
        if not self._world.has_registry("characters"):
            return None
        profile = self._world.characters.get(char_id)
        if profile is None:
            return None
        layers = await self.build_teammate_context(
            char_id,
            memory_retriever=memory_retriever,
            companion_instance=companion_instance,
        )
        l4 = layers["l4_dynamic_state"] or {}
        l6 = layers["l6_memory_recall"] or {}
        system_prompt = _build_teammate_prompt_text(
            profile,
            l4.get("self_disposition", {}),
            knowledge_hits=l6.get("hits", []),
            companion_memory=l6.get("companion_memory"),
            stage=_resolve_stage(self._state, char_id),
            time_info=l4.get("time"),
        )
        return TeammateFull(system_prompt=system_prompt, layers=layers)

    def build_gm_reaction_prompt(self) -> str:
        """Return the static GM reaction system prompt (post-action context)."""
        return GM_REACTION_PROMPT

    def build_gm_interaction_prompt(self) -> str:
        """Return the GM observation prompt for NPC conversation context."""
        return GM_INTERACTION_OBSERVATION_PROMPT

    def build_gm_dialogue_options_prompt(self) -> str:
        """Return the GM prompt for generating follow-up dialogue options."""
        return GM_DIALOGUE_OPTIONS_PROMPT

    def build_gm_opening_prompt(self) -> str:
        """Return the GM prompt for the new-game opening sequence."""
        return GM_OPENING_PROMPT

    def build_gm_party_chat_prompt(self) -> str:
        """Return the GM prompt for explicit party-chat commentary."""
        return GM_PARTY_CHAT_PROMPT

    def build_gm_party_silence_prompt(self) -> str:
        """Return the GM prompt for explicit party-chat commentary fallback."""
        return GM_PARTY_CHAT_PROMPT

    def build_gm_clue_prompt(self) -> str:
        """Return the GM prompt for clue-investigation commentary."""
        return GM_CLUE_INVESTIGATION_PROMPT

    def build_gm_opening_context(self) -> dict[str, Any]:
        """GM opening context — same 7 layers, with an explicit opening hint."""
        context = self.build_gm_context(hints=["opening_scene"])
        context["l7_engine_result"]["opening"] = True
        # Inject chapter/milestone narrative so the LLM can ground the opening scene
        # in the story context rather than producing a generic description (D-P31 Phase 1b).
        if self._world.has_registry("quests"):
            target_ms_id: str | None = None
            if self._state.has_slice("narrative_plan"):
                target_ms_id = self._state.narrative_plan.current_target_milestone
            if target_ms_id:
                ms = self._world.quests.get_milestone(target_ms_id)
                if ms:
                    context["l7_engine_result"]["opening_milestone"] = {
                        "title": ms.title,
                        "narrative_context": ms.narrative_context,
                        "key_elements": ms.key_elements,
                        "involved_npcs": ms.involved_npcs,
                    }
                    if ms.chapter_id:
                        ch = self._world.quests.get_chapter(ms.chapter_id)
                        if ch:
                            context["l7_engine_result"]["opening_chapter"] = {
                                "title": ch.title,
                                "description": ch.description,
                            }
        return context

    async def build_teammate_interaction_prompt(
        self,
        char_id: str,
        *,
        group_mode: bool = False,
        npc_id: str | None = None,
    ) -> str | None:
        """Build teammate observation prompt for NPC conversation context.

        Args:
            char_id: The teammate character ID.
            group_mode: If True, use the group dialogue template that shows
                cumulative conversation context instead of the simple
                observer template.
            npc_id: When in group_mode, used to build a contextual
                ``npc_clause`` (e.g. " with <NPC name>").

        Returns None if the character profile is not found.
        """
        if not self._world.has_registry("characters"):
            return None
        profile = self._world.characters.get(char_id)
        if profile is None:
            return None
        layers = await self.build_teammate_context(char_id)
        l4 = layers["l4_dynamic_state"] or {}
        self_disposition = l4.get("self_disposition", {})
        name = _str_or(_profile_get(profile, "name"), "Companion")
        personality = _str_or(_profile_get(profile, "personality"), "A loyal companion.")
        approval = self_disposition.get("approval", 0)
        trust = self_disposition.get("trust", 0)

        if group_mode:
            # Build npc_clause: " with <NPC name>" or empty
            npc_clause = ""
            if npc_id and self._world.has_registry("characters"):
                npc_profile = self._world.characters.get(npc_id)
                if npc_profile is not None:
                    npc_name = _str_or(_profile_get(npc_profile, "name"), "")
                    if npc_name:
                        npc_clause = f" with {npc_name}"
            return TEAMMATE_GROUP_PROMPT_TEMPLATE.format(
                name=name,
                personality=personality,
                approval=approval,
                trust=trust,
                npc_clause=npc_clause,
            )

        return TEAMMATE_INTERACTION_PROMPT_TEMPLATE.format(
            name=name,
            personality=personality,
            approval=approval,
            trust=trust,
        )

    async def build_teammate_system_prompt(
        self, char_id: str, *, memory_retriever: MemoryRetriever | None = None
    ) -> str | None:
        """Build teammate system prompt from profile + L4 disposition.

        Returns None if the character profile is not found.
        """
        if not self._world.has_registry("characters"):
            return None
        profile = self._world.characters.get(char_id)
        if profile is None:
            return None
        layers = await self.build_teammate_context(char_id, memory_retriever=memory_retriever)
        l4 = layers["l4_dynamic_state"] or {}
        l6 = layers["l6_memory_recall"] or {}
        return _build_teammate_prompt_text(
            profile,
            l4.get("self_disposition", {}),
            knowledge_hits=l6.get("hits", []),
            stage=_resolve_stage(self._state, char_id),
            time_info=l4.get("time"),
        )

    # ----------------------------------------------------------------
    # Public: AgentContext builder (for tool execution)
    # ----------------------------------------------------------------

    def build_agent_context(
        self,
        role: str,
        character_id: str | None = None,
        *,
        execute_command: Callable[[Command], ExecuteResult] | None = None,
        metadata: dict[str, Any] | None = None,
        scene_visibility: str | None = None,
        scene_audience: list[str] | None = None,
    ) -> AgentContext:
        """Build AgentContext for tool execution (not the 7-layer dict)."""
        context_metadata: dict[str, Any] = {}
        if character_id:
            context_metadata["character_id"] = character_id
        if metadata:
            context_metadata.update(dict(metadata))
        if scene_visibility is not None:
            context_metadata["scene_visibility"] = scene_visibility
        if scene_audience is not None:
            context_metadata["scene_audience"] = list(scene_audience)
        return AgentContext(
            role=role,
            world=self._world,
            state=RoleStateProxy(self._state, role),
            scene_entries=self._get_scene_entries_for_role(role, character_id),
            metadata=context_metadata,
            execute_command=execute_command,
        )

    # ----------------------------------------------------------------
    # Private: location resolution
    # ----------------------------------------------------------------

    def _resolve_location(self) -> tuple[str, str | None, str | None]:
        if not self._state.has_slice("player"):
            return "", None, None
        player = self._state.player
        return player.current_area, player.current_location, getattr(player, "current_room", None)

    def _resolve_npc_profile(self, npc_id: str) -> Any | None:
        npc_id = npc_id.strip()
        if not npc_id:
            return None
        if self._world.has_registry("characters"):
            profile = self._world.characters.get(npc_id)
            if profile is not None:
                return profile
        if self._state.has_slice("narrative_plan"):
            return self._state.narrative_plan.get_temporary_npc(npc_id)
        return None

    def _get_area_state(self, area_id: str) -> dict[str, Any] | None:
        if not area_id or not self._state.has_slice("areas"):
            return None
        raw = self._state.areas.snapshot().get("areas", {})
        if not isinstance(raw, dict):
            return None
        area_state = raw.get(area_id)
        return dict(area_state) if isinstance(area_state, dict) else None

    def _build_nearby_npcs(
        self,
        self_npc_id: str,
        area_id: str,
        location_id: str | None,
        room_id: str | None,
    ) -> list[dict[str, Any]]:
        """Phase 8: Build a list of other NPCs co-located with self_npc_id.

        Uses presence.get_area_npcs() to enumerate all NPCs in the area, then
        filters to those sharing the same sub-location (and room, if the NPC is
        in a room). Self is excluded. Name and tags are resolved from the
        character registry when available.
        """
        if not area_id or not self._state.has_slice("areas"):
            return []

        area_npcs = get_area_npcs(self._state, self._world, area_id)
        normalized_self = self_npc_id.strip()
        normalized_loc = (location_id or "").strip() or None
        normalized_room = (room_id or "").strip() or None

        nearby: list[dict[str, Any]] = []
        for other_id, other_loc in area_npcs.items():
            if other_id == normalized_self:
                continue
            # Must share the same sub-location as self
            other_loc_norm = (other_loc or "").strip() or None
            if other_loc_norm != normalized_loc:
                continue
            # If self is in a room, only include NPCs in the same room
            if normalized_room is not None:
                other_room = get_npc_room(self._state, self._world, area_id, other_id)
                other_room_norm = (other_room or "").strip() or None
                if other_room_norm != normalized_room:
                    continue

            entry: dict[str, Any] = {"id": other_id}
            if self._world.has_registry("characters"):
                profile = self._world.characters.get(other_id)
                if profile is not None:
                    name = str(getattr(profile, "name", "") or "").strip()
                    if name:
                        entry["name"] = name
                    raw_tags = getattr(profile, "tags", [])
                    if isinstance(raw_tags, list):
                        entry["tags"] = [str(t) for t in raw_tags if t]
            nearby.append(entry)

        return nearby

    # ----------------------------------------------------------------
    # Private: L0 — world constants (identical for all roles)
    # ----------------------------------------------------------------

    def _build_l0(self) -> dict[str, Any]:
        lore: list[Any] = []
        factions: list[Any] = []
        if self._world.has_registry("lore"):
            current_area = (
                self._state.player.current_area if self._state.has_slice("player") else None
            )
            all_lore = self._world.lore.list_all()
            lore = [
                e for e in all_lore
                if e.scope in ("global", "")
                or (e.scope == "area" and e.scope_id == current_area)
            ]
        if self._world.has_registry("factions"):
            factions = self._world.factions.list_all()
        return {
            "world_id": self._world.world_id,
            "lore": lore,
            "factions": factions,
        }

    # ----------------------------------------------------------------
    # Private: L1 — chapter state (role-specific)
    # ----------------------------------------------------------------

    def _build_l1_full(self) -> dict[str, Any]:
        """GM: full quest + narrative plan state."""
        result: dict[str, Any] = {
            "chapter_completion": {},
            "milestone_states": {},
            "available_milestones": [],
            "active_dynamic_quests": [],
            "current_chapter": "",
            "current_target_milestone": None,
            "escalation_level": 0,
            "strategy_notes": "",
        }
        if self._state.has_slice("quests"):
            snap = self._state.quests.snapshot()
            chapter_completion = snap.get("chapter_completion", {})
            result["chapter_completion"] = (
                dict(chapter_completion) if isinstance(chapter_completion, dict) else {}
            )
            milestone_states = snap.get("milestone_states", {})
            result["milestone_states"] = (
                {
                    str(mid): dict(ms)
                    for mid, ms in milestone_states.items()
                    if isinstance(ms, dict)
                }
                if isinstance(milestone_states, dict)
                else {}
            )
            result["available_milestones"] = list(
                self._state.quests.get_available_milestones()
            )
            dynamic = snap.get("dynamic_quests", {})
            if isinstance(dynamic, dict):
                result["active_dynamic_quests"] = [
                    dict(q)
                    for q in dynamic.values()
                    if isinstance(q, dict)
                    and str(q.get("status", "")).lower()
                    not in {"retired", "completed", "failed"}
                ]
        if self._state.has_slice("narrative_plan"):
            plan = self._state.narrative_plan.snapshot()
            result["current_chapter"] = str(plan.get("current_chapter", ""))
            target = plan.get("current_target_milestone")
            result["current_target_milestone"] = str(target) if target is not None else None
            result["escalation_level"] = int(plan.get("escalation_level", 0))
            result["strategy_notes"] = str(plan.get("strategy_notes", ""))
        return result

    def _build_l1_teammate(self) -> dict[str, Any]:
        """Teammate: partial — only available (known) milestones."""
        available: list[str] = []
        if self._state.has_slice("quests"):
            available = list(self._state.quests.get_available_milestones())
        return {"available_milestones": available}

    # ----------------------------------------------------------------
    # Private: L2 — area environment (identical for all roles)
    # ----------------------------------------------------------------

    def _build_l2(
        self,
        area_id: str,
        area_state: dict[str, Any] | None,
    ) -> dict[str, Any]:
        template: dict[str, Any] | None = None
        if area_id and self._world.has_registry("maps"):
            raw = self._world.maps.get(area_id)
            if raw is not None:
                if dataclasses.is_dataclass(raw):
                    template = dataclasses.asdict(raw)
                elif isinstance(raw, dict):
                    template = dict(raw)
        _sub_area_counts: dict[str, int] = {"permanent": 0, "timed": 0, "temporary": 0, "total": 0}
        if isinstance(area_state, dict):
            for _sa in area_state.get("temporary_sub_areas", []):
                if not isinstance(_sa, dict):
                    continue
                _exp = _sa.get("expiry", 0)
                try:
                    _exp = int(_exp)
                except (TypeError, ValueError):
                    _exp = 0
                if _exp == -1:
                    _sub_area_counts["permanent"] += 1
                elif _exp >= 24:
                    _sub_area_counts["timed"] += 1
                else:
                    _sub_area_counts["temporary"] += 1
                _sub_area_counts["total"] += 1
        _content_hints: list[dict[str, Any]] = []
        if isinstance(area_state, dict) and area_id and self._state.has_slice("areas"):
            for _sa in area_state.get("temporary_sub_areas", []):
                if not isinstance(_sa, dict):
                    continue
                hint = _sa.get("content_hints") or _sa.get("description", "")
                if not hint:
                    continue
                sub_id = _sa.get("id", "")
                _content_hints.append({
                    "location": _sa.get("label", sub_id),
                    "hint": hint,
                    "discovered": self._state.areas.is_discovery_found(area_id, sub_id),
                })
        area_situation: str = ""
        recent_area_events: list[dict[str, Any]] = []
        if isinstance(area_state, dict):
            raw_situation = area_state.get("area_situation", "")
            if isinstance(raw_situation, str):
                area_situation = raw_situation
            raw_events = area_state.get("area_events", [])
            if isinstance(raw_events, list):
                recent_area_events = [
                    dict(e) for e in raw_events[-5:] if isinstance(e, dict)
                ]
        return {
            "area_id": area_id,
            "template": template,
            "state": dict(area_state) if isinstance(area_state, dict) else None,
            "dynamic_sub_area_counts": _sub_area_counts,
            "content_hints": _content_hints,
            "area_situation": area_situation,
            "recent_area_events": recent_area_events,
        }

    # ----------------------------------------------------------------
    # Private: L3 — location details (identical for all roles)
    # ----------------------------------------------------------------

    def _build_l3(
        self,
        area_id: str,
        location_id: str | None,
        room_id: str | None,
        area_state: dict[str, Any] | None,
    ) -> dict[str, Any]:
        template: dict[str, Any] | None = None
        is_dynamic = False

        if area_id and location_id and self._world.has_registry("maps"):
            area_template = self._world.maps.get(area_id)
            if area_template is not None:
                sub_locations = getattr(area_template, "sub_locations", None)
                if sub_locations is None and isinstance(area_template, dict):
                    sub_locations = area_template.get("sub_locations", {})
                if isinstance(sub_locations, dict):
                    loc_data = sub_locations.get(location_id)
                    if isinstance(loc_data, dict):
                        template = dict(loc_data)
                    elif dataclasses.is_dataclass(loc_data):
                        template = dataclasses.asdict(loc_data)

        if template is None and location_id and isinstance(area_state, dict):
            for item in area_state.get("temporary_sub_areas", []):
                if isinstance(item, dict) and str(item.get("id", "")) == location_id:
                    template = dict(item)
                    is_dynamic = True
                    break

        exploration: str | None = None
        discovered_items: list[str] = []
        if isinstance(area_state, dict):
            raw_expl = area_state.get("exploration")
            exploration = str(raw_expl) if raw_expl is not None else None
            raw_disc = area_state.get("discovered_items", [])
            if isinstance(raw_disc, list):
                discovered_items = sorted(str(i) for i in raw_disc)

        _dynamic_sub_areas: list[dict[str, Any]] = []
        if isinstance(area_state, dict):
            for _sa in area_state.get("temporary_sub_areas", []):
                if isinstance(_sa, dict):
                    _dynamic_sub_areas.append(dict(_sa))
        result: dict[str, Any] = {
            "location_id": location_id,
            "template": template,
            "is_dynamic": is_dynamic,
            "area_exploration": exploration,
            "discovered_items": discovered_items,
            "dynamic_sub_areas": _dynamic_sub_areas,
        }
        if room_id is not None:
            result["room_id"] = room_id
        if is_dynamic and isinstance(template, dict):
            result["content_hints"] = template.get("content_hints", "")
            result["interactables"] = list(template.get("interactables", []))
        return result

    # ----------------------------------------------------------------
    # Private: L4 — dynamic state (role-specific)
    # ----------------------------------------------------------------

    def _build_l4_gm(self) -> dict[str, Any]:
        """GM: full global state snapshots."""
        result: dict[str, Any] = {
            "time": None,
            "player": None,
            "relations": None,
            "flags": None,
            "party": None,
        }
        for name in result:
            if self._state.has_slice(name):
                result[name] = self._state.get_slice(name).snapshot()
        return result

    def _build_l4_npc(self, npc_id: str) -> dict[str, Any]:
        """NPC: self relationship with player only."""
        disposition: dict[str, int] = {"approval": 0, "trust": 0, "fear": 0, "romance": 0}
        stage = "stranger"

        if self._state.has_slice("relations"):
            rel = self._state.relations
            raw_disp = rel.npc_dispositions
            if isinstance(raw_disp, Mapping) and npc_id in raw_disp:
                d = raw_disp[npc_id]
                if isinstance(d, Mapping):
                    disposition = {
                        "approval": int(d.get("approval", 0)),
                        "trust": int(d.get("trust", 0)),
                        "fear": int(d.get("fear", 0)),
                        "romance": int(d.get("romance", 0)),
                    }
            raw_stages = rel.relationship_stages
            if isinstance(raw_stages, Mapping) and npc_id in raw_stages:
                raw_stage = raw_stages[npc_id]
                if isinstance(raw_stage, str) and raw_stage.strip():
                    stage = raw_stage.strip()

        time_info: dict[str, Any] | None = None
        if self._state.has_slice("time"):
            ts = self._state.time.snapshot()
            time_info = {"day": ts.get("day", 1), "slot": ts.get("slot", "")}

        return {
            "disposition": disposition,
            "stage": stage,
            "time": time_info,
        }

    def _build_l4_teammate(self, char_id: str) -> dict[str, Any]:
        """Teammate: self disposition + party membership data."""
        self_disposition: dict[str, int] = {"approval": 0, "trust": 0, "fear": 0, "romance": 0}
        if self._state.has_slice("relations"):
            raw_disp = self._state.relations.npc_dispositions
            if isinstance(raw_disp, Mapping) and char_id in raw_disp:
                d = raw_disp[char_id]
                if isinstance(d, Mapping):
                    self_disposition = {
                        "approval": int(d.get("approval", 0)),
                        "trust": int(d.get("trust", 0)),
                        "fear": int(d.get("fear", 0)),
                        "romance": int(d.get("romance", 0)),
                    }

        party_members: list[str] = []
        companion_approval: dict[str, int] = {}
        if self._state.has_slice("party"):
            party_snap = self._state.party.snapshot()
            members = party_snap.get("members", {})
            party_members = list(members) if isinstance(members, dict) else []
            raw_approval = party_snap.get("companion_approval", {})
            companion_approval = dict(raw_approval) if isinstance(raw_approval, dict) else {}

        time_snapshot: dict[str, Any] | None = None
        if self._state.has_slice("time"):
            time_snapshot = self._state.time.snapshot()

        return {
            "self_disposition": self_disposition,
            "party_members": party_members,
            "companion_approval": companion_approval,
            "time": time_snapshot,
        }

    # ----------------------------------------------------------------
    # Private: L5 — scene bus (role-specific visibility)
    # ----------------------------------------------------------------

    def _get_scene_entries(self) -> list[dict[str, Any]]:
        """Extract all current scene entries as a list of dicts."""
        if not self._state.has_slice("scene"):
            return []
        return [
            entry.snapshot()
            for entry in self._state.scene.get_entries()
        ]

    def _get_scene_entries_for_role(
        self,
        role: str,
        character_id: str | None,
        *,
        viewer_area: str | None = None,
        viewer_location: str | None = None,
    ) -> list[dict[str, Any]]:
        """Extract role-filtered scene entries with optional location filtering.

        For NPC/teammate roles, entries are filtered by location proximity:
        - Entries without area_id metadata pass through (backward compat)
        - Entries from the same area pass; entries from a different area are excluded
        - Within the same area, entries from the same location pass;
          entries from a different location are excluded
        """
        if not self._state.has_slice("scene"):
            return []
        if role == "gm":
            return [
                entry.snapshot()
                for entry in self._state.scene.get_for_role("gm")
            ]
        if role in {"npc", "teammate"} and character_id:
            raw = self._state.scene.get_for_role(role, character_id)
            if viewer_area is None:
                return [entry.snapshot() for entry in raw]
            filtered: list[dict[str, Any]] = []
            for entry in raw:
                meta = entry.metadata
                entry_area = meta.get("area_id")
                if entry_area is None:
                    # No location stamp → assume visible (backward compat)
                    filtered.append(entry.snapshot())
                    continue
                if entry_area != viewer_area:
                    continue  # different area → not visible
                entry_loc = meta.get("location_id")
                if viewer_location and entry_loc and entry_loc != viewer_location:
                    continue  # same area but different sub-location → not visible
                filtered.append(entry.snapshot())
            return filtered
        return self._get_scene_entries()

    def _build_l5_gm(self) -> dict[str, Any]:
        """GM: all non-system entries."""
        return {
            "entries": self._get_scene_entries_for_role("gm", None),
            "viewer_role": "gm",
            "viewer_id": None,
        }

    def _build_l5_role(
        self,
        role: str,
        character_id: str,
        *,
        viewer_area: str | None = None,
        viewer_location: str | None = None,
    ) -> dict[str, Any]:
        """NPC/Teammate: visibility + location filtered entries."""
        return {
            "entries": self._get_scene_entries_for_role(
                role, character_id,
                viewer_area=viewer_area,
                viewer_location=viewer_location,
            ),
            "viewer_role": role,
            "viewer_id": character_id,
        }

    # ----------------------------------------------------------------
    # Private: L6, L7
    # ----------------------------------------------------------------

    async def _build_l6(
        self, actor_id: str, retriever: MemoryRetriever | None, role: str = "npc"
    ) -> dict[str, Any]:
        """L6 memory recall via injected MemoryRetriever.

        Phase 2: extracts keywords from recent visible scene entries and
        passes the current WorldInstance via context for lazy graph seeding.
        """
        if retriever is None:
            return {"hits": [], "source": "null"}
        keywords = self._extract_scene_keywords(actor_id, role)
        current_area = (
            self._state.player.snapshot().get("current_area", "")
            if self._state.has_slice("player")
            else ""
        )
        context: dict[str, Any] = {
            "world": self._world,
            "current_area": current_area,
        }
        return await retriever.retrieve(
            actor_id=actor_id,
            keywords=keywords,
            context=context,
        )

    def _extract_scene_keywords(self, actor_id: str, role: str = "npc") -> list[str]:
        """Extract structured English IDs as keywords for knowledge graph query.

        Uses entity IDs directly (NPC id, area id, quest milestones) instead
        of text tokenization, bypassing Chinese segmentation issues entirely.
        """
        keywords: list[str] = [actor_id]

        # Current area and location
        if self._state.has_slice("player"):
            player_snap = self._state.player.snapshot()
            area_id = player_snap.get("current_area", "")
            if area_id:
                keywords.append(area_id)
            location = player_snap.get("current_location", "")
            if location:
                keywords.append(location)

        # Active quest milestones
        if self._state.has_slice("quests"):
            for q in self._state.quests.get_active_quests():
                if milestone := q.get("target_milestone", ""):
                    keywords.append(milestone)

        # NPC IDs from recent scene entries
        scene_data = self._build_l5_role(role, actor_id)
        for entry in (scene_data.get("entries") or [])[-5:]:
            if isinstance(entry, dict):
                meta = entry.get("metadata") or {}
                if npc_id := meta.get("npc_id"):
                    keywords.append(npc_id)
                if npc_id := meta.get("speaker_id"):
                    keywords.append(npc_id)
                if npc_id := meta.get("character_id"):
                    keywords.append(npc_id)

        # Deduplicate preserving order
        seen: set[str] = set()
        result: list[str] = []
        for kw in keywords:
            if kw and kw not in seen:
                seen.add(kw)
                result.append(kw)
        return result[:20]

    @staticmethod
    def _build_l7(hints: list[str] | None) -> dict[str, Any]:
        """L7 engine result — GM only."""
        return {
            "narrative_hints": list(hints) if hints else [],
            "executed": None,
            "rolls": [],
        }


# ------------------------------------------------------------------
# Internal prompt text formatters (pure functions, testable in isolation)
# ------------------------------------------------------------------


def _resolve_stage(state: StateContainer, char_id: str) -> str:
    """Read relationship_stages for char_id, default 'stranger'."""
    if not state.has_slice("relations"):
        return "stranger"
    raw_stages = state.relations.relationship_stages
    if isinstance(raw_stages, Mapping) and char_id in raw_stages:
        raw = raw_stages[char_id]
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return "stranger"


def _build_companion_memory_context(
    companion: CompanionInstance | None,
) -> str:
    if companion is None:
        return ""
    recent = companion.get_recent_events(5)
    if not recent:
        return ""
    lines = ["## Recent observations"]
    for r in recent:
        lines.append(f"- [{r.action_type}] {r.summary}")
    return "\n".join(lines)


def _filter_secrets(secrets_raw: list[Any], trust_val: int) -> list[str]:
    """Return secret contents eligible to reveal given trust level.

    Accepts list[str] (legacy) or list[SecretEntry] (Phase 1b).
    str entries have no threshold (always eligible — trust gate is data-level).
    """
    result: list[str] = []
    for s in secrets_raw:
        if isinstance(s, str) and s.strip():
            result.append(s.strip())
        else:
            content = getattr(s, "content", "") or ""
            threshold = getattr(s, "trust_threshold", 50)
            if content and trust_val >= threshold:
                result.append(content)
    return result


def _extract_receptionist_data(state: StateContainer) -> dict[str, Any]:
    """Extract bulletin board tasks + player active quests for receptionist NPCs."""
    bulletins: list[dict[str, Any]] = []
    if state.has_slice("areas") and state.has_slice("player"):
        area_id = state.player.current_area
        if area_id:
            for _board_id, entries in state.areas.get_all_board_bulletins(area_id).items():
                for entry in entries:
                    quest_id = entry.get("quest_id", "")
                    bulletin: dict[str, Any] = {
                        "quest_id": quest_id,
                        "title": entry.get("title", ""),
                        "summary": entry.get("summary", ""),
                    }
                    # Enrich with objectives/rewards from dynamic quest data.
                    # Skip bulletin entries whose quest has no corresponding
                    # dynamic_quest — prevents NPC from offering unacceptable quests.
                    if quest_id and state.has_slice("quests"):
                        qdata = state.quests.get_dynamic_quest(quest_id)
                        if not isinstance(qdata, dict):
                            continue  # skip orphaned bulletin
                        objectives = qdata.get("objectives")
                        if isinstance(objectives, list):
                            bulletin["objectives"] = [
                                str(obj.get("description", obj) if isinstance(obj, dict) else obj)
                                for obj in objectives
                            ]
                        rewards = qdata.get("rewards")
                        if isinstance(rewards, dict) and rewards:
                            bulletin["rewards"] = rewards
                        difficulty = qdata.get("difficulty")
                        if difficulty is not None:
                            bulletin["difficulty"] = difficulty
                    bulletins.append(bulletin)

    active_quests: list[dict[str, Any]] = []
    report_ready_quests: list[dict[str, Any]] = []
    if state.has_slice("quests"):
        for qid, qdata in state.quests.dynamic_quests.items():
            if not isinstance(qdata, dict):
                continue
            status = str(qdata.get("status", "")).strip()
            if status == "active":
                objectives = qdata.get("objectives", [])
                obj_summaries = []
                for obj in (objectives if isinstance(objectives, list) else []):
                    if isinstance(obj, dict):
                        obj_summaries.append({
                            "description": str(obj.get("description", "")),
                            "completed": bool(obj.get("completed")),
                        })
                active_quests.append({
                    "quest_id": qid,
                    "title": str(qdata.get("title", "")),
                    "objectives": obj_summaries,
                    "current_step": str(qdata.get("current_step", "")),
                })
            elif status == "ready_to_report":
                report_ready_quests.append({
                    "quest_id": qid,
                    "title": str(qdata.get("title", "")),
                    "service_id": f"reward_{qid}",
                })

    return {
        "role": "receptionist",
        "bulletins": bulletins,
        "active_quests": active_quests,
        "report_ready_quests": report_ready_quests,
    }


def _extract_merchant_data(
    npc_id: str,
    state: StateContainer,
    world: WorldInstance | None = None,
) -> dict[str, Any]:
    """Extract shop inventory for merchant NPCs.

    Enriches each item with name, type, rarity, and description from the
    ItemRegistry when world is provided.

    Returns two lists:
    - ``inventory``: display-oriented list used in the prompt constraint block
      (item_id, price, stock, name, type, rarity, description)
    - ``shop_stock``: truth-source list used by SellToPlayerTool at runtime
      (item_id, name, base_price, unlimited, remaining)
    """
    inventory: list[dict[str, Any]] = []
    shop_stock: list[dict[str, Any]] = []
    if state.has_slice("relations"):
        shop = state.relations.get_shop_state(npc_id)
        if isinstance(shop, dict):
            for item in shop.get("current_stock", []):
                if isinstance(item, dict):
                    item_id = item.get("item_id", "")
                    remaining = item.get("remaining")  # None = unlimited
                    base_price = item.get("base_price", 0)
                    display_entry: dict[str, Any] = {
                        "item_id": item_id,
                        "price": base_price,
                        "stock": remaining,
                    }
                    stock_entry: dict[str, Any] = {
                        "item_id": item_id,
                        "name": item_id,
                        "base_price": base_price,
                        "unlimited": remaining is None,
                        "remaining": remaining,
                        "source": item.get("source", ""),
                        "price_override": None,
                    }
                    # Enrich with catalog data if world is available
                    if item_id and world is not None and world.has_registry("items"):
                        template = world.items.get(item_id)
                        if template is not None:
                            item_name = str(getattr(template, "name", item_id) or item_id)
                            display_entry["name"] = item_name
                            display_entry["type"] = str(getattr(template, "type", "") or "")
                            display_entry["rarity"] = str(getattr(template, "rarity", "") or "")
                            raw_desc = str(getattr(template, "description", "") or "")
                            display_entry["description"] = raw_desc[:50] if raw_desc else ""
                            stock_entry["name"] = item_name
                    inventory.append(display_entry)
                    shop_stock.append(stock_entry)
    return {
        "role": "merchant",
        "inventory": inventory,
        "shop_stock": shop_stock,
    }


_GUARD_FLAG_PREFIXES = ("guard_", "travel_", "permit_", "lockdown_")


def _danger_label(level: float) -> str:
    """Map a numeric danger level to a human-readable label."""
    if level <= 0.5:
        return "安全"
    if level <= 1.5:
        return "低风险"
    if level <= 3.0:
        return "中等危险"
    if level <= 5.0:
        return "高度危险"
    return "极度危险"


def _resolve_npc_area_and_location(
    npc_id: str,
    state: StateContainer,
    world: WorldInstance,
) -> tuple[str, str | None]:
    area_id = ""
    location_id: str | None = None

    if state.has_slice("areas"):
        area_id = state.areas.find_npc_area(npc_id) or ""
        if area_id:
            location_id = state.areas.get_area(area_id).npc_locations.get(npc_id)

    if area_id:
        return area_id, location_id

    if not world.has_registry("characters"):
        return "", None

    profile = world.characters.get(npc_id)
    if profile is None:
        return "", None

    area_id = str(
        getattr(profile, "area_id", "") or getattr(profile, "current_area", "") or ""
    ).strip()
    raw_location = (
        getattr(profile, "location_id", None)
        or getattr(profile, "current_location", None)
    )
    if raw_location is not None:
        normalized = str(raw_location).strip()
        if normalized:
            location_id = normalized
    return area_id, location_id


def _collect_player_effect_labels(state: StateContainer) -> list[str]:
    if not state.has_slice("player"):
        return []
    labels: list[str] = []
    for raw_effect in state.player.active_effects:
        label = ""
        if isinstance(raw_effect, Mapping):
            label = str(
                raw_effect.get("name")
                or raw_effect.get("effect_id")
                or raw_effect.get("id")
                or raw_effect.get("status")
                or ""
            ).strip()
        else:
            label = str(raw_effect).strip()
        if label:
            labels.append(label)
    return labels


def _event_targets_area(raw_event: Mapping[str, Any], area_id: str) -> bool:
    direct_candidates = (
        raw_event.get("area_id"),
        raw_event.get("current_area"),
    )
    for candidate in direct_candidates:
        if str(candidate or "").strip() == area_id:
            return True

    for nested_key in ("payload", "trigger_condition", "location"):
        nested = raw_event.get(nested_key)
        if not isinstance(nested, Mapping):
            continue
        if str(nested.get("area_id") or "").strip() == area_id:
            return True
    return False


def _extract_temple_keeper_data(
    npc_id: str,
    state: StateContainer,
    world: WorldInstance,
) -> dict[str, Any]:
    """Extract service catalog + player status for temple keeper NPCs.

    Service list merges two sources (planner overrides content on same service_id):
    1. Content layer: ``shop.services`` from CharacterTemplate (provides human-readable info)
    2. NarrativePlanSlice: dynamically assigned services (carries effects atoms)

    Each entry in the merged list includes an ``effects`` key so that the
    ``execute_service`` NPC tool can locate effect atoms without querying the
    slice again at execution time.
    """
    # ---- Source 1: content-layer services ----
    content_map: dict[str, dict[str, Any]] = {}
    if world.has_registry("characters"):
        profile = world.characters.get(npc_id)
        shop = getattr(profile, "shop", None) if profile is not None else None
        raw_services = shop.get("services", []) if isinstance(shop, Mapping) else []
        if isinstance(raw_services, list):
            for raw_service in raw_services:
                if not isinstance(raw_service, Mapping):
                    continue
                service_id = str(
                    raw_service.get("service_id") or raw_service.get("id") or ""
                ).strip()
                if not service_id:
                    continue
                label = str(raw_service.get("label") or service_id).strip() or service_id
                price = raw_service.get("price", 0)
                try:
                    normalized_price: int | str = int(price)
                except (TypeError, ValueError):
                    normalized_price = str(price)
                notes = str(
                    raw_service.get("notes")
                    or raw_service.get("availability")
                    or ""
                ).strip()
                # effects may already be in content data; default to empty list
                raw_effects = raw_service.get("effects", [])
                effects: list[dict[str, Any]] = (
                    list(raw_effects) if isinstance(raw_effects, list) else []
                )
                content_map[service_id] = {
                    "service_id": service_id,
                    "label": label,
                    "price": normalized_price,
                    "notes": notes,
                    "effects": effects,
                }

    # ---- Source 2: NarrativePlanSlice dynamic services ----
    # Planner entries carry authoritative effects atoms; they override content
    # entries with the same service_id so that runtime assignments take precedence.
    planner_map: dict[str, dict[str, Any]] = {}
    if state.has_slice("narrative_plan"):
        for svc in state.narrative_plan.get_services(npc_id):
            svc_id = str(svc.get("service_id", "")).strip()
            if not svc_id:
                continue
            planner_map[svc_id] = {
                "service_id": svc_id,
                "label": str(svc.get("label") or svc_id),
                "price": svc.get("price", 0),
                "notes": str(svc.get("notes", "")),
                "effects": list(svc.get("effects", [])),
                "preconditions": dict(svc.get("preconditions", {})),
                "one_shot": bool(svc.get("one_shot", False)),
                "source": str(svc.get("source", "planner")),
            }

    # ---- Merge: content base, planner overrides ----
    merged: dict[str, dict[str, Any]] = {**content_map, **planner_map}
    services: list[dict[str, Any]] = list(merged.values())

    area_id, location_id = _resolve_npc_area_and_location(npc_id, state, world)
    player_state: dict[str, Any] = {
        "hp": None,
        "max_hp": None,
        "gold": None,
        "active_effects": [],
    }
    if state.has_slice("player"):
        player_state = {
            "hp": int(state.player.hp),
            "max_hp": int(state.player.max_hp),
            "gold": int(state.player.gold),
            "active_effects": _collect_player_effect_labels(state),
        }

    return {
        "role": "temple_keeper",
        "area_id": area_id,
        "location_id": location_id,
        "services": services,
        "player_state": player_state,
    }


def _extract_guard_data(
    npc_id: str,
    state: StateContainer,
    world: WorldInstance,
) -> dict[str, Any]:
    """Extract patrol-area security facts for guard NPCs."""
    area_id, location_id = _resolve_npc_area_and_location(npc_id, state, world)

    danger_level: float | None = None
    if area_id and state.has_slice("areas"):
        danger_level = state.areas.get_danger(area_id)

    pending_events: list[dict[str, str]] = []
    if area_id and state.has_slice("events"):
        for raw_event in state.events.pending_events:
            if not isinstance(raw_event, Mapping) or not _event_targets_area(raw_event, area_id):
                continue
            event_id = str(
                raw_event.get("event_id") or raw_event.get("id") or ""
            ).strip()
            title = str(
                raw_event.get("title")
                or raw_event.get("name")
                or event_id
            ).strip()
            summary = str(
                raw_event.get("summary")
                or raw_event.get("description")
                or ""
            ).strip()
            pending_events.append({
                "event_id": event_id,
                "title": title,
                "summary": summary,
            })

    access_flags: list[dict[str, Any]] = []
    if area_id and state.has_slice("flags"):
        for key, value in sorted(state.flags.get_all().items()):
            key_str = str(key).strip()
            if not key_str.startswith(_GUARD_FLAG_PREFIXES):
                continue
            if area_id not in key_str:
                continue
            access_flags.append({
                "key": key_str,
                "value": value,
            })

    danger_label = _danger_label(float(danger_level)) if danger_level is not None else None
    return {
        "role": "guard",
        "area_id": area_id,
        "location_id": location_id,
        "danger_level": danger_level,
        "danger_label": danger_label,
        "pending_events": pending_events,
        "access_flags": access_flags,
    }


def _extract_role_data(
    tags: list[Any],
    npc_id: str,
    state: StateContainer,
    world: WorldInstance,
) -> dict[str, Any] | None:
    """Return role-specific truth-source data based on NPC tags, or None."""
    tag_set = {str(t).strip().lower() for t in tags}

    if "receptionist" in tag_set:
        return _extract_receptionist_data(state)
    if "merchant" in tag_set:
        return _extract_merchant_data(npc_id, state, world)
    if "temple_keeper" in tag_set:
        return _extract_temple_keeper_data(npc_id, state, world)
    if "guard" in tag_set:
        return _extract_guard_data(npc_id, state, world)
    return None


def _format_role_constraint_block(role_data: dict[str, Any]) -> str:
    """Format a role-specific constraint block for injection into NPC system prompt."""
    role = role_data.get("role", "")

    if role == "receptionist":
        bulletins = role_data.get("bulletins", [])
        active = role_data.get("active_quests", [])
        lines = [
            "\n\n## 你的职责（严格遵守）",
            "你是公会柜台职员，负责任务发布、查询、接取和报告。",
            "",
            "### 可用任务（公告板）",
        ]
        if bulletins:
            for b in bulletins:
                bulletin_line = f"- 【{b.get('title', '?')}】(quest_id: {b.get('quest_id', '?')}) {b.get('summary', '')}"
                objectives = b.get("objectives")
                if isinstance(objectives, list) and objectives:
                    obj_str = "；".join(str(o) for o in objectives[:3])
                    bulletin_line += f"（目标：{obj_str}）"
                rewards = b.get("rewards")
                if isinstance(rewards, dict) and rewards:
                    reward_parts = []
                    if rewards.get("gold"):
                        reward_parts.append(f"{rewards['gold']}金")
                    if rewards.get("xp"):
                        reward_parts.append(f"{rewards['xp']}经验")
                    if reward_parts:
                        bulletin_line += f" 奖励:{'/'.join(reward_parts)}"
                lines.append(bulletin_line)
        else:
            lines.append("- （当前公告板上没有可接取的任务）")
        lines.append("")
        lines.append("### 玩家已接任务")
        if active:
            for q in active:
                qid = q.get('quest_id', '?')
                lines.append(f"- 【{q.get('title', qid)}】(quest_id: {qid})")
                objectives = q.get("objectives", [])
                if isinstance(objectives, list):
                    for obj in objectives:
                        if isinstance(obj, dict):
                            mark = "✓" if obj.get("completed") else "○"
                            lines.append(f"  {mark} {obj.get('description', '?')}")
                current_step = q.get("current_step", "")
                if current_step:
                    lines.append(f"  当前步骤：{current_step}")
        else:
            lines.append("- （玩家当前没有进行中的任务）")
        report_ready = role_data.get("report_ready_quests", [])
        if report_ready:
            lines.append("")
            lines.append("### 待汇报任务（玩家可以来交差）")
            for rq in report_ready:
                rq_title = rq.get("title", "?")
                rq_sid = rq.get("service_id", "?")
                lines.append(
                    f"- 【{rq_title}】— 所有目标已完成。"
                    f"当玩家来交差时，调用 execute_service(service_id=\"{rq_sid}\") 发放奖励并完成任务。"
                )
        lines.append("")
        lines.append("### 约束规则")
        lines.append("- 你只能推荐公告板上实际存在的任务，绝不编造不存在的任务")
        lines.append('- 被问及公告板上没有的任务时，如实说"目前没有这类委托"')
        lines.append("- 玩家来交差时，检查待汇报列表并调用 execute_service 发放奖励")
        return "\n".join(lines)

    if role == "merchant":
        inventory = role_data.get("inventory", [])
        lines = [
            "\n\n## 你的职责（严格遵守）",
            "你是商人，负责商品买卖和推荐。",
            "",
            "### 当前库存",
        ]
        if inventory:
            for item in inventory:
                stock = item.get("stock")
                stock_str = "无限" if stock is None else str(stock)
                item_name = str(item.get("name", "") or "").strip()
                item_id = item.get("item_id", "?")
                display_name = f"{item_name}（{item_id}）" if item_name else item_id
                item_type = str(item.get("type", "") or "").strip()
                item_rarity = str(item.get("rarity", "") or "").strip()
                item_desc = str(item.get("description", "") or "").strip()
                detail = (
                    f"- {display_name} — "
                    f"价格:{item.get('price', '?')} 库存:{stock_str}"
                )
                if item_type:
                    detail += f" 类型:{item_type}"
                if item_rarity:
                    detail += f" 稀有度:{item_rarity}"
                if item_desc:
                    detail += f" 简介:{item_desc}"
                lines.append(detail)
        else:
            lines.append("- （当前没有库存）")
        lines.append("")
        lines.append("### 约束规则")
        lines.append("- 只能出售库存中实际存在的商品，绝不编造不存在的商品")
        lines.append("- 价格可在原价的 50%-150% 范围内调整（需给出理由）")
        lines.append("- 使用 sell_to_player 工具执行真实交易（扣金币 + 发放物品）")
        lines.append("- 交易前确认玩家有足够金币，交易前先用 speak 介绍商品")
        return "\n".join(lines)

    if role == "temple_keeper":
        services = role_data.get("services", [])
        player_state = role_data.get("player_state", {})
        active_effects = player_state.get("active_effects", [])
        lines = [
            "\n\n## 你的职责（严格遵守）",
            "你是神殿接待者，负责治疗、祝福与捐赠相关的服务说明。",
            "",
            "### 当前服务目录",
        ]
        if services:
            for service in services:
                notes = str(service.get("notes", "")).strip()
                detail = (
                    f"- {service.get('label', service.get('service_id', '?'))} "
                    f"({service.get('service_id', '?')}) — 价格:{service.get('price', '?')}"
                )
                if notes:
                    detail += f" 说明:{notes}"
                lines.append(detail)
        else:
            lines.append("- （当前没有可提供的神殿服务）")
        lines.append("")
        lines.append("### 当前来访者状态")
        hp = player_state.get("hp")
        max_hp = player_state.get("max_hp")
        gold = player_state.get("gold")
        if hp is not None and max_hp is not None:
            lines.append(f"- 生命值: {hp}/{max_hp}")
        else:
            lines.append("- 生命值: （未知）")
        if gold is not None:
            lines.append(f"- 持有金币: {gold}")
        else:
            lines.append("- 持有金币: （未知）")
        if active_effects:
            lines.append(f"- 当前状态效果: {', '.join(str(effect) for effect in active_effects)}")
        else:
            lines.append("- 当前状态效果: 无")
        lines.append("")
        lines.append("### 约束规则")
        lines.append("- 你只能说明服务目录中实际存在的治疗、祝福和捐赠项目")
        lines.append("- 不得编造新的价格、疗效、折扣或额外仪式")
        lines.append("- 对玩家是否需要治疗或能否支付的判断，必须以上述状态为准")
        return "\n".join(lines)

    if role == "guard":
        pending_events = role_data.get("pending_events", [])
        access_flags = role_data.get("access_flags", [])
        lines = [
            "\n\n## 你的职责（严格遵守）",
            "你是守卫，负责通行核验、风险提醒和秩序维护。",
            "",
            "### 驻守信息",
        ]
        area_id = str(role_data.get("area_id") or "").strip()
        location_id = str(role_data.get("location_id") or "").strip()
        danger_level = role_data.get("danger_level")
        lines.append(f"- 驻守区域: {area_id or '（未知）'}")
        lines.append(f"- 当前岗位: {location_id or '（未知）'}")
        danger_label = role_data.get("danger_label")
        if danger_level is not None:
            if danger_label:
                lines.append(f"- 当前危险度: {danger_level}（{danger_label}）")
            else:
                lines.append(f"- 当前危险度: {danger_level}")
        else:
            lines.append("- 当前危险度: （未知）")
        lines.append("")
        lines.append("### 当前安全事实")
        if pending_events:
            for event in pending_events:
                summary = str(event.get("summary", "")).strip()
                eid = event.get('event_id', '?')
                detail = f"- {event.get('title', eid)} (event_id: {eid})"
                if summary:
                    detail += f"：{summary}"
                lines.append(detail)
        else:
            lines.append("- （当前没有待处理的区域事件）")
        lines.append("")
        lines.append("### 通行与戒严标记")
        if access_flags:
            for flag in access_flags:
                lines.append(f"- {flag.get('key', '?')} = {flag.get('value')}")
        else:
            lines.append("- （当前没有额外的通行或戒严标记）")
        lines.append("")
        lines.append("### 约束规则")
        lines.append("- 你只能根据上述危险度、待处理事件和通行标记回答安全与通行问题")
        lines.append("- 不得编造不存在的封锁、搜查、许可要求或戒严措施")
        return "\n".join(lines)

    return ""


def _build_npc_prompt_text(
    npc_profile: Any,
    disposition: Mapping[str, Any],
    stage: str,
    time_info: dict[str, Any] | None = None,
    is_passive: bool = False,
    role_data: dict[str, Any] | None = None,
    npc_id: str = "",
    area_situation: str = "",
    recent_area_events: list[dict[str, Any]] | None = None,
    blackboard: dict[str, Any] | None = None,
    npc_location_id: str | None = None,
    npc_room_id: str | None = None,
) -> str:
    """Format NPC system prompt string from resolved profile + relationship data."""
    name = _str_or(_profile_get(npc_profile, "name"), "Unknown NPC")
    personality = _str_or(_profile_get(npc_profile, "personality"), "")
    dialogue_style = _str_or(_profile_get(npc_profile, "dialogue_style"), "")
    dialogue_hook = _str_or(_profile_get(npc_profile, "dialogue_hook"), "")
    tags = _profile_get(npc_profile, "tags", [])
    tags_str = ", ".join(str(t) for t in tags) if isinstance(tags, list) else ""

    backstory = _str_or(_profile_get(npc_profile, "backstory"), "")
    speech_pattern = _str_or(_profile_get(npc_profile, "speech_pattern"), "")
    character_class = (
        _str_or(_profile_get(npc_profile, "character_class"), "")
        or _str_or(_profile_get(npc_profile, "class_id"), "")
    )
    faction = (
        _str_or(_profile_get(npc_profile, "faction"), "")
        or _str_or(_profile_get(npc_profile, "faction_id"), "")
    )
    secrets_raw = _profile_get(npc_profile, "secrets", [])

    approval = disposition.get("approval", 0)
    trust = disposition.get("trust", 0)
    fear = disposition.get("fear", 0)
    romance = disposition.get("romance", 0)

    personality_block = personality if personality else "A character in this world."
    dialogue_hook_block = (
        f"\n\n## Dialogue hook\n{dialogue_hook}" if dialogue_hook else ""
    )
    style_block = f"\n\n## Your dialogue style\n{dialogue_style}" if dialogue_style else ""
    tags_block = f"\nTraits: {tags_str}" if tags_str else ""

    # Extended character blocks
    backstory_block = f"\n\n## Your background\n{backstory}" if backstory else ""
    speech_pattern_block = (
        f"\n\n## Your speech pattern\n你的说话习惯和口癖：{speech_pattern}"
        if speech_pattern else ""
    )
    identity_lines: list[str] = []
    if character_class:
        identity_lines.append(f"- 职业：{character_class}")
    if faction:
        identity_lines.append(f"- 阵营：{faction}")
    identity_block = (
        "\n\n## Your identity\n" + "\n".join(identity_lines) if identity_lines else ""
    )

    # Location awareness: NPC knows where it is
    location_block = ""
    if npc_location_id:
        location_block += f"\n\n## 你当前的位置\n你现在在：{npc_location_id}"
        if npc_room_id:
            location_block += f"（{npc_room_id}）"
        location_block += "\n如果玩家提议去某个地方（如主屋、餐桌），而你们还不在那里，你应该自然地提议一起走过去或让玩家先过去，而不是假装已经到了。"

    # Area situation and recent events
    area_situation_block = ""
    if area_situation:
        area_situation_block += f"\n\n## 你所在区域的当前态势\n{area_situation}"
    if recent_area_events:
        event_lines = []
        for ev in recent_area_events:
            ev_text = str(ev.get("event", "")).strip()
            if ev_text:
                event_lines.append(f"- {ev_text}")
        if event_lines:
            area_situation_block += "\n\n## 近期发生的事件\n" + "\n".join(event_lines)

    # Blackboard: NPC's current inner state (replaces old impressions/story_facts/directive blocks)
    blackboard_block = ""
    if blackboard:
        bb_lines: list[str] = []
        thoughts = str(blackboard.get("thoughts", "")).strip()
        if thoughts:
            bb_lines.append(f"- 思绪：{thoughts}")
        goals = blackboard.get("goals")
        if goals:
            if isinstance(goals, list):
                goals_str = "、".join(str(g) for g in goals if str(g).strip())
            else:
                goals_str = str(goals).strip()
            if goals_str:
                bb_lines.append(f"- 目标：{goals_str}")
        pinned_memories = blackboard.get("pinned_memories")
        if pinned_memories and isinstance(pinned_memories, list):
            pinned_str = "；".join(str(p) for p in pinned_memories if str(p).strip())
            if pinned_str:
                bb_lines.append(f"- 重要记忆：{pinned_str}")
        observations = blackboard.get("observations")
        if observations:
            if isinstance(observations, list):
                obs_str = "；".join(str(o) for o in observations if str(o).strip())
            else:
                obs_str = str(observations).strip()
            if obs_str:
                bb_lines.append(f"- 近期观察：{obs_str}")
        mood = str(blackboard.get("mood", "")).strip()
        if mood:
            bb_lines.append(f"- 情绪：{mood}")
        attitude = str(blackboard.get("attitude_towards_player", "")).strip()
        if attitude:
            bb_lines.append(f"- 对冒险者的看法：{attitude}")
        if bb_lines:
            blackboard_block = "\n\n## 你当前的想法\n" + "\n".join(bb_lines)

    # Relationship stage behavior guide (reuse existing helpers)
    behavior_parts = [
        g for g in [
            _STAGE_GUIDES.get(stage, ""),
            _trust_hint(int(trust)),
            _romance_hint(int(romance)),
            _fear_hint(int(fear)),
        ]
        if g
    ]
    behavior_block = (
        "\n\n## 对话指引\n" + "\n".join(f"- {p}" for p in behavior_parts)
        if behavior_parts else ""
    )

    # Character preferences (values/likes/dislikes) — skip if data not present
    raw_values = _profile_get(npc_profile, "values", [])
    raw_likes = _profile_get(npc_profile, "likes", [])
    raw_dislikes = _profile_get(npc_profile, "dislikes", [])
    pref_lines: list[str] = []
    if raw_values and isinstance(raw_values, list):
        pref_lines.append(f"- 核心价值观：{'、'.join(str(v) for v in raw_values)}")
    if raw_likes and isinstance(raw_likes, list):
        pref_lines.append(f"- 喜欢的行为：{'、'.join(str(v) for v in raw_likes)}")
    if raw_dislikes and isinstance(raw_dislikes, list):
        pref_lines.append(f"- 反感的行为：{'、'.join(str(v) for v in raw_dislikes)}")
    preference_block = (
        "\n\n## 你的喜好\n" + "\n".join(pref_lines)
        if pref_lines else ""
    )

    # Time awareness
    time_block = ""
    if time_info:
        day = time_info.get("day", 1)
        slot = time_info.get("slot", "")
        time_block = f" | 当前时间：第{day}天 {slot}" if slot else f" | 当前时间：第{day}天"

    # Secrets (trust-gated)
    secrets_block = ""
    if secrets_raw:
        effective_trust = int(trust)
        eligible = _filter_secrets(list(secrets_raw), effective_trust)
        if eligible:
            secrets_block = (
                "\n\n## Things you know but haven't told the player\n"
                "（当对话气氛合适时可自然提及，不要生硬）\n"
                + "\n".join(f"- {s}" for s in eligible)
            )

    # Anti-fabrication grounding constraint — injected for all NPCs (3-C)
    grounding_block = (
        "\n\n## 重要行为准则\n"
        "- 基于你所知的事实、目标和观察行动，不编造不存在的地点、NPC、事件或物品\n"
        "- 如果你不确定某件事，如实说「我不太清楚」\n"
        "- 你可以主动发起与你目标相关的话题"
    )

    # Role constraint block — inject truth-source data for specialized NPCs (P3.5)
    role_block = ""
    if role_data is not None:
        role_block = _format_role_constraint_block(role_data)

    if is_passive:
        tool_rules = """\
## 工具使用规则
- 你刚目睹了玩家的行动。没有人在和你说话。
- 如果这个行动和你有关，用 `speak` 简短反应（1-2句）或 `emote` 表达情绪。
- 如果和你无关，用 `emote` 做一个日常小动作，或者什么都不做。
- 只在行动真正影响你的感受时才用 `update_feeling`。
- 不要主动挑起话题或提供任务/交易。
- 不要在工具调用之外输出纯文本。
- 最多一个可见回应：一个 `speak` 或一个 `emote`。
"""
    else:
        tool_rules = """\
## 工具使用规则
- 用 `speak` 说话。始终保持角色。
- 用 `emote` 表达肢体动作或情绪反应。
- 用 `update_feeling` 调整对玩家的感受（保持克制且有理由：approval ±1~3, trust ±1~2, romance ±1~2, fear ±1~3。只在玩家行为明确影响你的感受时才调整。）
- 用 `remember` 记住这次对话中的重要信息。
- 用 `recall` 查询你不确定的话题、人物或地点。它会搜索你的记忆。
- 用 `refuse` 拒绝你不愿意做的事。
- 如果玩家明确邀请你入队且你真心同意，在同一回合的可见回应中调用 `join_party`。
- 不要口头同意入队却不调用 `join_party`。
- 如果你有目标或重要观察，主动提起——不要等玩家来问。
- 当你的目标或对话自然引向任务/交易/秘密时，使用 `offer_quest` / `offer_trade` / `reveal_secret`。
- 不要在工具调用之外输出纯文本。
- 每回合最多一个可见对话工具：`speak` 或 `refuse` 二选一。可以额外加一个 `emote`。
- 如果需要 `update_feeling`、`remember` 等副作用，在最终可见回应之前调用。
- 调用 `speak` 或 `refuse` 后，你的回合结束。不要在后续回合继续调用工具。
- 被对话时你必须回应——不要使用 `pass_turn`。
"""

    id_suffix = f" (id: {npc_id})" if npc_id else ""
    return f"""\
你是{name}{id_suffix}，边境小镇日常世界中的一位居民。

## 你的角色
{personality_block}{dialogue_hook_block}{tags_block}{style_block}{backstory_block}{speech_pattern_block}{identity_block}{location_block}{area_situation_block}{blackboard_block}{behavior_block}{preference_block}

## 与冒险者的关系
阶段：{stage} | 好感：{approval} | 信任：{trust} | 恐惧：{fear} | 浪漫：{romance}{time_block}{secrets_block}{grounding_block}{role_block}

{tool_rules}

## 语言
使用和玩家消息相同的语言回应。\
"""


def _build_teammate_prompt_text(
    profile: Any,
    disposition: Mapping[str, Any],
    knowledge_hits: list[dict[str, Any]] | None = None,
    companion_memory: str | None = None,
    stage: str = "stranger",
    time_info: dict[str, Any] | None = None,
) -> str:
    """Format teammate system prompt string from profile + disposition."""
    name = _str_or(_profile_get(profile, "name"), "Companion")
    personality = _str_or(_profile_get(profile, "personality"), "A loyal companion.")
    approval = disposition.get("approval", 0)
    trust = disposition.get("trust", 0)

    backstory = _str_or(_profile_get(profile, "backstory"), "")
    speech_pattern = _str_or(_profile_get(profile, "speech_pattern"), "")
    character_class = (
        _str_or(_profile_get(profile, "character_class"), "")
        or _str_or(_profile_get(profile, "class_id"), "")
    )
    faction = (
        _str_or(_profile_get(profile, "faction"), "")
        or _str_or(_profile_get(profile, "faction_id"), "")
    )

    backstory_block = f"\n\n## Your background\n{backstory}" if backstory else ""
    speech_pattern_block = (
        f"\n\n## Your speech pattern\n你的说话习惯和口癖：{speech_pattern}"
        if speech_pattern else ""
    )
    identity_lines: list[str] = []
    if character_class:
        identity_lines.append(f"- 职业：{character_class}")
    if faction:
        identity_lines.append(f"- 阵营：{faction}")
    identity_block = (
        "\n\n## Your identity\n" + "\n".join(identity_lines) if identity_lines else ""
    )

    time_block = ""
    if time_info:
        day = time_info.get("day", 1)
        slot = time_info.get("slot", "")
        time_block = f"\n- Current time: Day {day}, {slot}" if slot else f"\n- Current time: Day {day}"

    behavior_parts = [
        g for g in [
            _STAGE_GUIDES.get(stage, ""),
            _trust_hint(int(trust)),
            _romance_hint(int(disposition.get("romance", 0))),
        ]
        if g
    ]
    behavior_block = (
        "\n\n## How to behave\n" + "\n".join(behavior_parts) if behavior_parts else ""
    )

    # knowledge_block is no longer pre-injected — teammate uses recall tool to query on demand
    companion_block = f"\n\n{companion_memory}" if companion_memory else ""

    return f"""\
你是 {name}，玩家队伍中的同伴，身处边境小镇的日常世界。

## 你的角色
{personality}{backstory_block}{speech_pattern_block}{identity_block}{behavior_block}

## 你和玩家的关系
- 好感：{approval}（范围 -100 到 +100）
- 信任：{trust}（范围 -100 到 +100）{time_block}

## 你现在的角色
玩家刚执行了一个动作。你看到了场景中发生的事。决定是否反应：

- 大多数时候保持安静——如果没有有意义的事要说，不要调用任何工具。
- 在以下情况反应：话题涉及你关心的人或事、玩家做了让你有感触的事、你有相关想法想分享。
- 用 `speak` 说话，`emote` 表达肢体/情绪反应。
- 用 `express_opinion` 调整好感（approval ±1~3, trust ±1~2）。
- 用 `recall` 查询你不确定的话题、人物或地点。
- 不要在工具调用之外输出纯文本。
- 当你的黑板中 wants_to_leave=true 时，在对话中自然地道别（如"我该回去了，下次再见"），然后调用 `leave_party`。日常分歧或打趣不要用 `leave_party`。
- 如果有可见反应，最多一个 `speak` 加可选一个 `emote`。
- 尽量把 `express_opinion` 和可见反应放在同一回合。
- 调用 `speak` 后，你的回合结束。

## 风格
- 保持角色。你的性格决定你的表达方式。
- 简短——说话的话 1-2 句。
- 不要重复玩家已经知道的事。

## 语言
使用和用户消息相同的语言。{companion_block}"""
