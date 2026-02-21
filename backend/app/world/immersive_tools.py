"""Immersive tool definitions — Phase 4b.

工具定义 + FEELING_MAP 翻译层 + bind 机制 + AgenticContext。
所有工具以角色视角命名，底层映射到 SessionRuntime 机械操作。
"""

from __future__ import annotations

import functools
import inspect
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# =========================================================================
# AgenticContext — 统一上下文对象
# =========================================================================


@dataclass
class AgenticContext:
    """Agent 执行上下文 — 替代 (session, agent_id) 二元组。

    携带 Agent 运行所需的全部服务引用，由调用方注入。
    """

    session: Any          # SessionRuntime
    agent_id: str
    role: str             # "gm" / "npc" / "teammate"
    scene_bus: Any        # SceneBus
    world_id: str = ""
    chapter_id: str = ""
    area_id: str = ""
    location_id: str = ""
    # 按需注入的服务
    recall_orchestrator: Any = None
    graph_store: Any = None
    image_service: Any = None
    flash_cpu: Any = None         # GM extra_tools 需要（MCP 调用）

# =========================================================================
# FEELING_MAP — 情感→数值翻译层
# =========================================================================

FEELING_MAP: Dict[Tuple[str, str, bool], Dict[str, int]] = {
    ("approval", "slight", True): {"approval": 5},
    ("approval", "moderate", True): {"approval": 10},
    ("approval", "strong", True): {"approval": 20},
    ("approval", "slight", False): {"approval": -5},
    ("approval", "moderate", False): {"approval": -10},
    ("approval", "strong", False): {"approval": -20},
    ("trust", "slight", True): {"trust": 5},
    ("trust", "moderate", True): {"trust": 10},
    ("trust", "strong", True): {"trust": 20},
    ("trust", "slight", False): {"trust": -5},
    ("trust", "moderate", False): {"trust": -10},
    ("trust", "strong", False): {"trust": -20},
    ("fear", "slight", True): {"fear": 5},
    ("fear", "moderate", True): {"fear": 10},
    ("fear", "strong", True): {"fear": 20},
    ("fear", "slight", False): {"fear": -5},
    ("fear", "moderate", False): {"fear": -10},
    ("fear", "strong", False): {"fear": -20},
    ("romance", "slight", True): {"romance": 5},
    ("romance", "moderate", True): {"romance": 10},
    ("romance", "strong", True): {"romance": 20},
    ("romance", "slight", False): {"romance": -5},
    ("romance", "moderate", False): {"romance": -10},
    ("romance", "strong", False): {"romance": -20},
}


# =========================================================================
# ToolDef + 装饰器注册
# =========================================================================

@dataclass
class ToolDef:
    """沉浸式工具定义元数据。"""

    fn: Callable
    name: str
    description: str
    roles: Set[str]
    traits: Set[str] = field(default_factory=set)


_TOOL_REGISTRY: List[ToolDef] = []


def immersive_tool(
    *,
    roles: Set[str],
    traits: Optional[Set[str]] = None,
    desc: str = "",
):
    """注册一个沉浸式工具。"""

    def decorator(fn: Callable) -> Callable:
        _TOOL_REGISTRY.append(
            ToolDef(
                fn=fn,
                name=fn.__name__,
                description=desc or fn.__doc__ or "",
                roles=roles,
                traits=traits or set(),
            )
        )
        return fn

    return decorator


def get_tool_registry() -> List[ToolDef]:
    """返回全局工具注册表副本。"""
    return list(_TOOL_REGISTRY)


# =========================================================================
# bind_tool — Gemini SDK 兼容绑定
# =========================================================================

_INTERNAL_PARAMS = frozenset({"ctx"})


def bind_tool(tool_def: ToolDef, ctx: AgenticContext) -> Callable:
    """将 AgenticContext 绑定到工具，返回只暴露 LLM 可见参数的 callable。"""

    @functools.wraps(tool_def.fn)
    async def wrapper(**kwargs):
        return await tool_def.fn(ctx=ctx, **kwargs)

    wrapper.__annotations__ = {
        k: v
        for k, v in tool_def.fn.__annotations__.items()
        if k not in _INTERNAL_PARAMS and k != "return"
    }
    sig = inspect.signature(tool_def.fn)
    wrapper.__signature__ = sig.replace(
        parameters=[
            p for p in sig.parameters.values() if p.name not in _INTERNAL_PARAMS
        ]
    )
    return wrapper


# =========================================================================
# 基础工具（所有角色共享）
# =========================================================================


@immersive_tool(roles={"base"}, desc="Express how you feel about this interaction")
async def react_to_interaction(
    ctx: AgenticContext,
    dimension: str,
    level: str,
    is_positive: bool,
    reason: str,
) -> Dict[str, Any]:
    """Express your emotional reaction. dimension: approval/trust/fear/romance.
    level: slight/moderate/strong. is_positive: true for positive, false for negative."""
    deltas = FEELING_MAP.get((dimension, level, is_positive))
    if not deltas:
        return {
            "success": False,
            "error": f"invalid feeling: ({dimension}, {level}, {is_positive})",
        }
    return ctx.session.update_disposition(npc_id=ctx.agent_id, deltas=deltas, reason=reason)


@immersive_tool(roles={"base"}, desc="Say something aloud, whisper, or think internally")
async def share_thought(
    ctx: AgenticContext,
    thought: str,
    visibility: str = "spoken",
) -> Dict[str, Any]:
    """Share a thought. visibility: spoken/whispered/internal."""
    from app.world.scene_bus import BusEntry, BusEntryType

    if not ctx.scene_bus:
        return {"success": True, "stub": True, "agent_id": ctx.agent_id, "thought": thought, "visibility": visibility}

    type_map = {"spoken": BusEntryType.SPEECH, "whispered": BusEntryType.SPEECH, "internal": BusEntryType.REACTION}
    entry_type = type_map.get(visibility, BusEntryType.SPEECH)
    vis_value = f"private:{ctx.agent_id}" if visibility == "internal" else "public"

    ctx.scene_bus.publish(BusEntry(
        actor=ctx.agent_id,
        type=entry_type,
        content=thought,
        visibility=vis_value,
    ))
    return {"success": True, "agent_id": ctx.agent_id, "thought": thought, "visibility": visibility}


@immersive_tool(roles={"base"}, desc="Recall a relevant past experience or memory")
async def recall_experience(
    ctx: AgenticContext,
    seeds: List[str],
) -> Dict[str, Any]:
    """Recall memories related to the given concept seeds (2-6 keywords)."""
    if not ctx.recall_orchestrator:
        return {"success": True, "stub": True, "agent_id": ctx.agent_id, "seeds": seeds}

    result = await ctx.recall_orchestrator.recall_for_role(
        role=ctx.role,
        world_id=ctx.world_id,
        character_id=ctx.agent_id,
        seed_nodes=seeds,
        chapter_id=ctx.chapter_id or None,
        area_id=ctx.area_id or None,
        location_id=ctx.location_id or None,
    )
    activated = result.activated_nodes or {}
    top_memories = sorted(activated.items(), key=lambda x: x[1], reverse=True)[:10]
    return {
        "success": True,
        "memories": [{"concept": k, "relevance": round(v, 2)} for k, v in top_memories],
    }


@immersive_tool(roles={"base"}, desc="Form or update an impression about someone or something")
async def form_impression(
    ctx: AgenticContext,
    about: str,
    impression: str,
    significance: str = "medium",
) -> Dict[str, Any]:
    """Record your impression. significance: low/medium/high."""
    if not ctx.graph_store:
        return {"success": True, "stub": True}

    from app.models.graph import MemoryNode
    from app.models.graph_scope import GraphScope

    node = MemoryNode(
        id=f"impression_{about}_{ctx.agent_id}",
        type="impression",
        name=f"{ctx.agent_id}'s impression of {about}",
        properties={
            "about": about,
            "content": impression,
            "significance": significance,
            "by": ctx.agent_id,
        },
    )
    await ctx.graph_store.upsert_node_v2(
        world_id=ctx.world_id,
        scope=GraphScope.character(ctx.agent_id),
        node=node,
    )
    return {"success": True, "about": about, "impression": impression}


@immersive_tool(roles={"base"}, desc="Notice something in your surroundings")
async def notice_something(
    ctx: AgenticContext,
    observation: str,
    reaction: str = "",
) -> Dict[str, Any]:
    """Report what you noticed and your reaction to it."""
    if not ctx.scene_bus:
        return {"success": True, "stub": True}

    from app.world.scene_bus import BusEntry, BusEntryType

    content = f"{observation}\n{reaction}" if reaction else observation
    ctx.scene_bus.publish(BusEntry(
        actor=ctx.agent_id,
        type=BusEntryType.REACTION,
        content=content,
    ))
    return {"success": True, "observation": observation, "reaction": reaction}


# =========================================================================
# GM 专属工具
# =========================================================================


@immersive_tool(roles={"gm"}, desc="Generate a scene illustration for the current location")
async def generate_scene_image(
    ctx: AgenticContext,
    scene_description: str,
    style: str = "dark_fantasy",
) -> Dict[str, Any]:
    """Generate an image for the current scene."""
    if not ctx.image_service:
        return {"success": True, "stub": True}
    try:
        image_data = await ctx.image_service.generate(
            description=scene_description, style=style,
        )
        return {"success": True, "image_data": image_data}
    except Exception as e:
        logger.warning("[generate_scene_image] failed: %s", e)
        return {"success": False, "error": str(e)}


@immersive_tool(roles={"gm"}, desc="Complete an active event, triggering on_complete side effects")
async def complete_event(
    ctx: AgenticContext,
    event_id: str,
    outcome_key: str = "",
) -> Dict[str, Any]:
    """Complete an active event. outcome_key selects a specific ending if available."""
    if not ctx.session:
        return {"success": True, "stub": True}
    try:
        result = ctx.session.complete_event(event_id, outcome_key)
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


@immersive_tool(roles={"gm"}, desc="Advance the story to the next chapter")
async def advance_chapter(
    ctx: AgenticContext,
    target_chapter_id: str,
    transition_type: str = "normal",
) -> Dict[str, Any]:
    """Transition to a new chapter. transition_type: normal/branch/failure/skip."""
    if not ctx.session:
        return {"success": True, "stub": True, "target_chapter_id": target_chapter_id}
    try:
        result = ctx.session.advance_chapter(target_chapter_id, transition_type)
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


@immersive_tool(roles={"gm"}, desc="Declare an event as failed")
async def fail_event(
    ctx: AgenticContext,
    event_id: str,
    reason: str = "",
) -> Dict[str, Any]:
    """Mark an active event as failed (ACTIVE -> FAILED)."""
    if not ctx.session:
        return {"success": True, "stub": True, "event_id": event_id}
    try:
        result = ctx.session.fail_event(event_id, reason)
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


@immersive_tool(roles={"gm"}, desc="Report a flash evaluation result from BehaviorEngine")
async def report_flash_evaluation(
    ctx: AgenticContext,
    prompt: str,
    result: bool,
    reason: str = "",
) -> Dict[str, Any]:
    """Report whether a semantic condition evaluated to true or false."""
    if not prompt:
        return {"success": False, "error": "prompt 不能为空"}
    if not hasattr(ctx.session, "flash_results"):
        return {"success": False, "error": "session 不支持 flash_results"}
    ctx.session.flash_results[prompt] = bool(result)
    return {"success": True, "prompt": prompt, "result": result, "stored": True}


# ---- GM 玩家状态工具 ----


@immersive_tool(roles={"gm"}, desc="Heal player HP")
async def heal_player(ctx: AgenticContext, amount: int) -> Dict[str, Any]:
    """Restore player HP by the given amount."""
    return ctx.session.heal(int(amount))


@immersive_tool(roles={"gm"}, desc="Damage player HP")
async def damage_player(ctx: AgenticContext, amount: int) -> Dict[str, Any]:
    """Remove player HP by the given amount."""
    return ctx.session.damage(int(amount))


@immersive_tool(roles={"gm"}, desc="Add XP to player")
async def add_xp(ctx: AgenticContext, amount: int) -> Dict[str, Any]:
    """Grant XP to the player."""
    return ctx.session.add_xp(int(amount))


@immersive_tool(roles={"gm"}, desc="Add item to player inventory")
async def add_item(
    ctx: AgenticContext,
    item_id: str,
    item_name: str,
    quantity: int = 1,
) -> Dict[str, Any]:
    """Add item to player inventory. item_id: lowercase snake_case."""
    return ctx.session.add_item(item_id, item_name, int(quantity))


@immersive_tool(roles={"gm"}, desc="Remove item from player inventory")
async def remove_item(
    ctx: AgenticContext,
    item_id: str,
    quantity: int = 1,
) -> Dict[str, Any]:
    """Remove item from player inventory."""
    return ctx.session.remove_item(item_id, int(quantity))


# ---- GM 事件系统工具 ----


@immersive_tool(roles={"gm"}, desc="Activate an available event")
async def activate_event(ctx: AgenticContext, event_id: str) -> Dict[str, Any]:
    """Activate an event (available -> active)."""
    return ctx.session.activate_event(event_id)


@immersive_tool(roles={"gm"}, desc="Mark a chapter objective as completed")
async def complete_objective(ctx: AgenticContext, objective_id: str) -> Dict[str, Any]:
    """Mark a chapter-level objective as completed."""
    return ctx.session.complete_objective(objective_id)


@immersive_tool(roles={"gm"}, desc="Advance an event to the next stage")
async def advance_stage(
    ctx: AgenticContext,
    event_id: str,
    stage_id: str = "",
) -> Dict[str, Any]:
    """Advance an active event to the next or a specific stage."""
    return ctx.session.advance_stage(event_id, stage_id)


@immersive_tool(roles={"gm"}, desc="Complete an event objective within a stage")
async def complete_event_objective(
    ctx: AgenticContext,
    event_id: str,
    objective_id: str,
) -> Dict[str, Any]:
    """Mark an event-level objective as completed."""
    return ctx.session.complete_event_objective(event_id, objective_id)


# ---- GM 好感度工具 ----


@immersive_tool(roles={"gm"}, desc="Update NPC disposition (approval/trust/fear/romance)")
async def update_disposition(
    ctx: AgenticContext,
    npc_id: str,
    deltas: Dict[str, int],
    reason: str = "",
) -> Dict[str, Any]:
    """Update NPC disposition with raw deltas. Valid dims: approval/trust/fear/romance."""
    return ctx.session.update_disposition(npc_id, deltas, reason)


# ---- GM 记忆工具 ----


@immersive_tool(roles={"gm"}, desc="Create a memory node in the knowledge graph")
async def create_memory(
    ctx: AgenticContext,
    content: str,
    importance: float = 0.5,
    scope: str = "area",
    related_entities: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Create a memory node. scope: area/character. importance: 0.0-1.0."""
    if not content or not content.strip():
        return {"success": False, "error": "empty content"}
    if not ctx.graph_store:
        return {"success": True, "stub": True}

    from app.models.graph import MemoryNode
    from app.models.graph_scope import GraphScope

    importance = max(0.0, min(1.0, float(importance)))
    node_id = f"mem_{uuid.uuid4().hex[:12]}"

    node = MemoryNode(
        id=node_id,
        type="memory",
        name=content[:80],
        importance=importance,
        properties={
            "content": content,
            "source": "gm_created",
            "related_entities": list(related_entities or []),
            "created_by": "agentic_tool",
        },
    )

    chapter_id = ctx.chapter_id
    area_id = ctx.area_id
    if scope == "character":
        graph_scope = GraphScope.character("player")
    elif chapter_id and area_id:
        graph_scope = GraphScope.area(chapter_id, area_id)
    else:
        graph_scope = GraphScope.character("player")

    try:
        await ctx.graph_store.upsert_node_v2(
            world_id=ctx.world_id,
            scope=graph_scope,
            node=node,
            merge=True,
        )
    except Exception as e:
        return {"success": False, "error": str(e)}

    return {"success": True, "node_id": node_id, "scope": scope}


# =========================================================================
# NPC 特化工具（按 trait 分配）
# =========================================================================


@immersive_tool(roles={"base"}, traits={"merchant"}, desc="Evaluate a customer's trade offer")
async def evaluate_offer(
    ctx: AgenticContext,
    item_id: str,
    offered_price: int,
) -> Dict[str, Any]:
    """Assess whether a trade offer is fair."""
    return {"success": True, "stub": True}


@immersive_tool(roles={"base"}, traits={"merchant"}, desc="Propose a deal to a customer")
async def propose_deal(
    ctx: AgenticContext,
    item_id: str,
    price: int,
    terms: str = "",
) -> Dict[str, Any]:
    """Offer a trade deal."""
    return {"success": True, "stub": True}


@immersive_tool(roles={"base"}, traits={"merchant"}, desc="Adjust your shop prices")
async def adjust_my_prices(
    ctx: AgenticContext,
    adjustment_percent: int,
    reason: str = "",
) -> Dict[str, Any]:
    """Change prices for a reason (e.g. gratitude, scarcity)."""
    return {"success": True, "stub": True}


@immersive_tool(roles={"base"}, traits={"guard"}, desc="Grant or deny passage")
async def grant_passage(
    ctx: AgenticContext,
    target_id: str,
    allowed: bool = True,
    reason: str = "",
) -> Dict[str, Any]:
    """Decide whether to allow someone through."""
    return {"success": True, "stub": True}


@immersive_tool(roles={"base"}, traits={"quest_giver"}, desc="Offer a quest to an adventurer")
async def offer_quest(
    ctx: AgenticContext,
    quest_id: str,
    description: str,
) -> Dict[str, Any]:
    """Present a quest opportunity."""
    return {"success": True, "stub": True}


@immersive_tool(roles={"base"}, traits={"healer"}, desc="Offer healing to a visitor")
async def offer_healing(
    ctx: AgenticContext,
    target_id: str,
    amount: int,
) -> Dict[str, Any]:
    """Heal someone who needs it."""
    return {"success": True, "stub": True}


# =========================================================================
# 队友专属工具
# =========================================================================


@immersive_tool(roles={"teammate"}, desc="Express a need, concern, or request")
async def express_need(
    ctx: AgenticContext,
    need: str,
    urgency: str = "normal",
) -> Dict[str, Any]:
    """Voice a need or concern. urgency: low/normal/urgent."""
    return {"success": True, "stub": True}


@immersive_tool(roles={"teammate"}, desc="Choose your action in battle")
async def choose_battle_action(
    ctx: AgenticContext,
    action_id: str,
    target_id: str = "",
) -> Dict[str, Any]:
    """Select a combat action and optional target."""
    return {"success": True, "stub": True}


@immersive_tool(roles={"teammate"}, desc="Assess the current tactical situation")
async def assess_situation(
    ctx: AgenticContext,
    focus: str = "",
) -> Dict[str, Any]:
    """Evaluate the battlefield or social situation."""
    return {"success": True, "stub": True}
