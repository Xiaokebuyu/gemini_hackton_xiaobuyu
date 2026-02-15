"""
World Graph Package -- 世界活图

Step C1: 数据模型 (models.py, constants.py)  ✅
Step C2: WorldGraph 容器 (world_graph.py)    ✅
Step C3: GraphBuilder (graph_builder.py)     ✅
Step C4: BehaviorEngine (behavior_engine.py) ✅
Step C5: EventPropagator (event_propagation.py) ✅
Step C6: Snapshot (snapshot.py)              ✅
后续步骤: C7 (管线集成)
"""
from app.world.models import (
    # Enums
    WorldNodeType,
    WorldEdgeType,
    TriggerType,
    ActionType,
    EventStatus,
    ChapterStatus,
    # Behavior system
    Action,
    Behavior,
    BehaviorResult,
    # Event system
    EventObjective,
    EventStage,
    EventOutcome,
    # Core
    WorldNode,
    WorldEvent,
    TickContext,
    # C4 新增
    EvalResult,
    TickResult,
)

from app.world.world_graph import WorldGraph

from app.world.graph_builder import GraphBuilder

from app.world.behavior_engine import (
    ConditionEvaluator,
    ActionExecutor,
    BehaviorEngine,
)

from app.world.event_propagation import EventPropagator

from app.world.snapshot import (
    WorldSnapshot,
    EdgeChangeRecord,
    capture_snapshot,
    restore_snapshot,
    snapshot_to_dict,
    dict_to_snapshot,
)

from app.world.constants import (
    ABILITY_SCORES,
    SKILLS,
    DAMAGE_TYPES,
    CONDITIONS,
    ALIGNMENTS,
    EQUIPMENT_SLOTS,
    PROFICIENCY_BY_LEVEL,
    XP_BY_LEVEL,
    FULL_CASTER_SPELL_SLOTS,
    HALF_CASTER_SPELL_SLOTS,
    EXHAUSTION_EFFECTS,
    default_character_state,
    default_npc_state,
    default_player_state,
)

__all__ = [
    # Graph container
    "WorldGraph",
    # Builder
    "GraphBuilder",
    # Engine (C4)
    "ConditionEvaluator",
    "ActionExecutor",
    "BehaviorEngine",
    # Propagation (C5)
    "EventPropagator",
    # Snapshot (C6)
    "WorldSnapshot",
    "EdgeChangeRecord",
    "capture_snapshot",
    "restore_snapshot",
    "snapshot_to_dict",
    "dict_to_snapshot",
    # Enums
    "WorldNodeType",
    "WorldEdgeType",
    "TriggerType",
    "ActionType",
    "EventStatus",
    "ChapterStatus",
    # Behavior
    "Action",
    "Behavior",
    "BehaviorResult",
    # Event
    "EventObjective",
    "EventStage",
    "EventOutcome",
    # Core
    "WorldNode",
    "WorldEvent",
    "TickContext",
    "EvalResult",
    "TickResult",
    # Constants
    "ABILITY_SCORES",
    "SKILLS",
    "DAMAGE_TYPES",
    "CONDITIONS",
    "ALIGNMENTS",
    "EQUIPMENT_SLOTS",
    "PROFICIENCY_BY_LEVEL",
    "XP_BY_LEVEL",
    "FULL_CASTER_SPELL_SLOTS",
    "HALF_CASTER_SPELL_SLOTS",
    "EXHAUSTION_EFFECTS",
    "default_character_state",
    "default_npc_state",
    "default_player_state",
]
