# AI CRPG Game Engine Backend

An AI-powered CRPG game backend in the style of Baldur's Gate 3, built on a **hexagonal pure-Python kernel** — `game_core` has zero external dependencies, with all external capabilities (LLM, persistence, knowledge graph) injected through adapter ports for maximum testability and extensibility.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Web Framework | FastAPI 0.109 + uvicorn |
| Data Store | Google Cloud Firestore / local JSON |
| AI Models | Google Gemini Flash / Pro (`google-genai`) |
| Graph Algorithms | NetworkX 3.2 (world knowledge graph) |
| Token Counting | tiktoken |

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

Create a `.env` file:

```env
GOOGLE_API_KEY=your_gemini_api_key
# Optional: Firestore (falls back to local JSON persistence without this)
GOOGLE_APPLICATION_CREDENTIALS=./firebase-credentials.json
```

### 3. Run the Server

```bash
# Development mode
uvicorn app.main:app --reload --port 8000
```

Once running:
- API: http://localhost:8000/api/game/worlds
- Docs: http://localhost:8000/docs
- Health: http://localhost:8000/health

### 4. Initialize World Data

World data is stored as structured JSON under `data/<world_id>/structured_new/` and loaded automatically at startup via `world_data_loader`.

## Project Structure

```
backend/
├── app/
│   ├── main.py                        # FastAPI entry point (router mounting)
│   ├── deps.py                        # Singleton construction & dependency injection (composition root)
│   ├── api_models.py                  # Request/response Pydantic models
│   ├── interaction_service.py         # App layer: presence validation + execution orchestration
│   ├── interaction_views.py           # Snapshot view builders (shop/quest/dialogue, etc.)
│   ├── agent_orchestration.py         # Agentic session orchestration service
│   ├── llm_gemini.py                  # Gemini LLM adapter
│   ├── memory_retriever_impl.py       # Knowledge graph memory retrieval adapter
│   ├── world_knowledge_graph.py       # World knowledge graph (NetworkX)
│   ├── world_data_loader.py           # Structured world data loader
│   ├── world_seed.py                  # World catalog registry
│   ├── evaluators.py                  # Condition evaluators
│   ├── narrators.py                   # Narration generators
│   ├── routers/
│   │   ├── sessions.py                # Session management routes
│   │   ├── character.py               # Character creation & panel routes
│   │   ├── panels.py                  # Inventory/map/quest panel routes
│   │   └── gameplay.py                # Gameplay action & streaming routes
│   └── game_core/                     # ★ Hexagonal pure-Python kernel (zero external deps)
│       ├── runtime.py                 # GameRuntime (world loading + session factory)
│       ├── bootstrap.py               # Default component assembly
│       ├── state/                     # State layer
│       │   ├── base.py                # StateContainer + StateDelta
│       │   ├── delta.py               # StateChange application logic
│       │   └── slices/                # 10 StateSlices
│       │       ├── time.py            #   Game time
│       │       ├── player.py          #   Player location/attributes
│       │       ├── area.py            #   Current area & sub-locations
│       │       ├── quests.py          #   Quest states
│       │       ├── scene.py           #   Scene description
│       │       ├── relations.py       #   NPC relationship stage + approval
│       │       ├── flags.py           #   Global flags (schemaless)
│       │       ├── events.py          #   Event history
│       │       ├── party.py           #   Party members
│       │       └── narrative_plan.py  #   Narrative plan state
│       ├── content/                   # Content layer
│       │   ├── world.py               # WorldInstance (aggregates all registries)
│       │   └── registries/            # 10 ContentRegistries
│       │       ├── characters.py      #   NPC definitions
│       │       ├── items.py           #   Items/equipment
│       │       ├── skills.py          #   Skills/spells
│       │       ├── classes.py         #   Character classes
│       │       ├── monsters.py        #   Monsters
│       │       ├── maps.py            #   Maps & areas
│       │       ├── factions.py        #   Factions
│       │       ├── lore.py            #   Lore entries
│       │       ├── quests.py          #   Quest definitions
│       │       └── tag.py             #   Tag system
│       ├── rules/                     # Rules layer
│       │   ├── engine.py              # RulesEngine (dispatches Command → Handler)
│       │   ├── models.py              # Command / ExecuteResult / Roll
│       │   ├── handler_utils.py       # Shared handler utilities
│       │   └── handlers/              # 16+ CommandHandlers
│       │       ├── combat.py          #   Combat (attack/defend/move/flee, etc.)
│       │       ├── skill_check.py     #   Skill checks / saving throws / contests
│       │       ├── navigation.py      #   Area movement & sub-location entry/exit
│       │       ├── inventory.py       #   Inventory (pick up/drop/equip/use)
│       │       ├── economy.py         #   Trade (buy/sell)
│       │       ├── growth.py          #   Progression (XP/level up/ASI/subclass)
│       │       ├── rest.py            #   Rest (short/long)
│       │       ├── crime.py           #   Crime (theft/lockpicking)
│       │       ├── encounter.py       #   Encounter triggering
│       │       ├── container.py       #   Container operations
│       │       ├── world_state.py     #   World state changes (flags/schedules/rumors)
│       │       ├── status_effect.py   #   Status effects
│       │       ├── spell.py           #   Spell system (prepare/cast/concentrate/effects)
│       │       ├── discovery.py       #   Exploration discovery
│       │       ├── hostile_area.py    #   Hostile area entry
│       │       ├── interactable.py    #   Interactable objects
│       │       └── proficiency.py     #   Proficiency checks
│       ├── orchestration/             # Orchestration layer
│       │   ├── tick_coordinator.py    # TickCoordinator (session tick lifecycle)
│       │   ├── pipeline.py            # PipelineOrchestrator (three-stage pipeline)
│       │   ├── action_dispatcher.py   # ActionDispatcher (action_type → Command)
│       │   ├── context_assembler.py   # ContextAssembler (L1–L7 context assembly)
│       │   ├── defaults.py            # Default registries (50+ actions, 14 hooks)
│       │   ├── event_engine.py        # Event trigger engine
│       │   ├── interaction.py         # Interaction presence/precondition validation
│       │   ├── npc_interaction.py     # NPC interaction orchestration
│       │   ├── private_chat.py        # Private chat stream orchestration
│       │   ├── settlement.py          # SettlementContext
│       │   ├── scene_bus.py           # SceneBus (scene signal bus)
│       │   ├── shared_context.py      # SharedContext (per-tick shared data)
│       │   ├── models.py              # PipelineResult / SSEEvent / StructuredAction
│       │   └── hooks/                 # 14 SettlementHooks
│       │       ├── encounter.py       #   Encounter triggering
│       │       ├── event_condition.py #   Event condition evaluation
│       │       ├── relationship.py    #   Relationship stage progression
│       │       ├── gm_narration.py    #   GM narration generation (LLM)
│       │       ├── ai_osiris.py       #   AI Osiris death/resurrection system
│       │       ├── narrative_planner.py #  Narrative plan updates
│       │       ├── npc_schedule.py    #   NPC schedule dispatch
│       │       ├── private_chat_trigger.py # Private chat trigger
│       │       ├── scheduled_event.py #   Scheduled events
│       │       ├── status_effect.py   #   Status effect settlement
│       │       ├── time_advance.py    #   Time advancement
│       │       ├── dynamic_sub_area_expiry.py # Dynamic sub-area expiry
│       │       └── scene_reset.py     #   Scene reset
│       ├── narrative/                 # Narrative layer (LLM integration)
│       │   ├── executor.py            # AgenticExecutor (single-pass + multi-turn LLM loop)
│       │   ├── registry.py            # RoleToolRegistry (per-role tool registration)
│       │   ├── gm_tools.py            # GM tool set
│       │   ├── character_tools.py     # NPC / teammate tool sets
│       │   ├── instance_manager.py    # NPC instance manager (LRU)
│       │   ├── context.py             # AgentContext
│       │   ├── context_builder.py     # Context builder
│       │   ├── context_window.py      # Context window management
│       │   ├── memory_retriever.py    # Memory retrieval Protocol
│       │   ├── models.py              # AgentResult / ToolResult
│       │   ├── role_proxy.py          # Role proxy
│       │   └── tools.py               # AgentTool base class
│       ├── adapters/                  # Adapter layer (port protocols + default impls)
│       │   ├── inbound.py             # InputPort + FastAPIInputPort (normalization)
│       │   ├── outbound.py            # OutputPort Protocol
│       │   ├── persistence.py         # PersistencePort Protocol
│       │   ├── session_store.py       # Session persistence (Firestore / local)
│       │   ├── local_persistence.py   # Local JSON persistence
│       │   ├── firestore_persistence.py # Firestore persistence
│       │   ├── llm.py                 # LlmPort Protocol
│       │   ├── memory_graph_port.py   # Memory graph port
│       │   └── presentation.py        # SSE formatting utilities
│       └── planning/                  # Planning layer (dynamic sub-area generation)
│           ├── planner.py             # Area planner
│           ├── dynamic_sub_area.py    # Dynamic sub-areas
│           └── models.py              # Planning data models
├── tests/                             # Tests (baseline: 290+ passed)
├── data/                              # World data (Goblin Slayer, etc.)
└── requirements.txt
```

## Core Architecture

### Hexagonal Layering

```
┌─────────────────────────────────────────────────────────┐
│  Application Layer (app/)                                │
│  FastAPI routes → InteractionService → InteractionViews  │
├─────────────────────────────────────────────────────────┤
│  Adapter Layer (game_core/adapters/)                     │
│  FastAPIInputPort · LlmPort · PersistencePort · OutputPort│
├─────────────────────────────────────────────────────────┤
│  Orchestration Layer (game_core/orchestration/)          │
│  TickCoordinator → PipelineOrchestrator                  │
│  ActionDispatcher · ContextAssembler · SettlementHooks   │
├─────────────────────────────────────────────────────────┤
│  Rules Layer (game_core/rules/)                          │
│  RulesEngine → 16+ CommandHandlers                       │
├─────────────────────────────────────────────────────────┤
│  Content Layer (game_core/content/)                      │
│  WorldInstance + 10 ContentRegistries                    │
├─────────────────────────────────────────────────────────┤
│  State Layer (game_core/state/)                          │
│  StateContainer + 10 StateSlices + StateDelta            │
└─────────────────────────────────────────────────────────┘
```

### Request Data Flow

```
Player Input (HTTP)
    │
    ▼
FastAPIInputPort.process_action()
    │  Normalize to execution directive (resolved / rejected)
    ▼
InteractionService
    │  Presence validation (NPC/board in scene?)
    │  Precondition validation (intent, item, quest)
    ▼
TickCoordinator.process()
    │
    ├─► PipelineOrchestrator
    │       │
    │       ├─► ContextAssembler (L1–L7 context assembly)
    │       │
    │       ├─► ActionDispatcher (action_type → Command)
    │       │
    │       └─► RulesEngine.execute(Command, state, world)
    │               │  Delegates to matching CommandHandler
    │               └─► StateDelta (state change description)
    │
    ├─► after_engine hooks (PipelineHook)
    │
    └─► SettlementHooks (ordered by priority)
            ScheduledEventHook → StatusEffectHook → AIOsirisHook
            → NarrativePlannerHook → EncounterHook → EventConditionHook
            → NpcScheduleHook → RelationshipHook → PrivateChatTriggerHook
            → TimeAdvanceHook → DynamicSubAreaExpiryHook
            → GmNarrationHook (LLM narration) → SceneBusResetHook
    │
    ▼
PipelineResult → SSE event stream → Frontend
```

### Ten State Slices

| Slice | File | Responsibility |
|-------|------|---------------|
| TimeSlice | `time.py` | Game time (rounds / hours / days) |
| PlayerSlice | `player.py` | Player location, attributes, HP, spell slots |
| AreaSlice | `area.py` | Current area & sub-location states |
| QuestSlice | `quests.py` | Quest states (active / completed / retired) |
| SceneSlice | `scene.py` | Current scene description & NPC list |
| RelationsSlice | `relations.py` | NPC relationship stage + approval value |
| FlagSlice | `flags.py` | Global flags (schemaless dict) |
| EventsSlice | `events.py` | Recent event history |
| PartySlice | `party.py` | Party member list |
| NarrativePlanSlice | `narrative_plan.py` | Narrative plan & beat state |

### Ten Content Registries

| Registry | Content |
|----------|---------|
| CharacterRegistry | NPC definitions (attributes, skills, initial state) |
| ItemRegistry | Items/equipment (type, effects, price) |
| SkillRegistry | Skills/spells (effect, cost, range) |
| ClassRegistry | Character classes (features, progression) |
| MonsterRegistry | Monsters (CR, drops, behavior) |
| MapRegistry | Maps & areas (connectivity, sub-locations) |
| FactionRegistry | Factions (reputation thresholds, stance) |
| LoreRegistry | World lore entries (encyclopedia) |
| QuestRegistry | Quest definitions (stages, conditions, rewards) |
| TagRegistry | Tag system (classification & queries) |

### Narrative Layer (AgenticExecutor)

`AgenticExecutor` supports two modes:

- **Single-pass** `run()`: Executes a pre-built `tool_calls` list — used for post-action structured processing
- **Multi-turn agentic loop** `run_agentic()`: LLM autonomously selects tool calls — used for GM narration and NPC dialogue

`RoleToolRegistry` assigns tool sets by role (gm / npc / teammate). The LLM only sees tools available to its assigned role.

`InstanceManager` manages NPC instances (context window + memory retrieval) with LRU eviction.

## API Endpoints

### World & Session

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/api/game/worlds` | List all worlds |
| GET | `/api/game/{world_id}/sessions` | List sessions |
| POST | `/api/game/{world_id}/sessions` | Create session |
| POST | `/api/game/{world_id}/sessions/{sid}/load` | Load / resume session |
| DELETE | `/api/game/{world_id}/sessions/{sid}` | Delete session |

### Character

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/game/{world_id}/character-creation/options` | Creation options (class / race) |
| POST | `/api/game/{world_id}/sessions/{sid}/character` | Create character |
| GET | `/api/game/{world_id}/sessions/{sid}/character` | Character panel |

### Panels (State Queries)

| Method | Path | Description |
|--------|------|-------------|
| GET | `.../inventory` | Inventory panel |
| GET | `.../map` | Map panel |
| GET | `.../quests` | Quest panel |

### Gameplay Actions (all SSE streaming)

| Method | Path | Description |
|--------|------|-------------|
| POST | `.../navigate` | Navigation action (move_area / enter / leave) |
| POST | `.../action/stream` | Structured action (with GM narration SSE) |
| POST | `.../input/stream` | Text command (alias parsing + SSE) |
| POST | `.../interact/stream` | NPC / quest board interaction (SSE) |
| POST | `.../private_chat/stream` | Private chat stream (SSE) |

## Configuration

### Required

| Variable | Description |
|----------|-------------|
| `GOOGLE_API_KEY` or `GEMINI_API_KEY` | Gemini API key (LLM features degrade gracefully without it) |

### Optional

| Variable | Description |
|----------|-------------|
| `GOOGLE_APPLICATION_CREDENTIALS` | Firebase credentials path (falls back to local JSON persistence) |
| `GEMINI_FLASH_MODEL` | Flash model name |
| `GEMINI_PRO_MODEL` | Pro model name |

## Running Tests

```bash
# Run all tests from the backend/ directory
PYTHONPATH=. pytest --ignore=tests/test_api_shell.py --ignore=tests/test_interaction_service.py -v

# Single test file
PYTHONPATH=. pytest tests/test_game_core_scaffold.py -v

# Filter by name
PYTHONPATH=. pytest tests/test_encounter_handler.py -k "test_name" -v
```

Test baseline: 290+ passed (`asyncio_mode = "auto"`).

## Design Highlights

1. **Hexagonal pure-Python kernel**: `game_core/` has zero external dependencies — any database, LLM, or framework can be swapped out, and unit tests need no infrastructure mocks
2. **Immutable StateDelta**: All state mutations are described by `StateDelta` and applied atomically by `StateContainer.apply()`, enabling replay, auditing, and undo
3. **SettlementHook chain**: 14 ordered hooks form a pluggable post-processing pipeline, each with a single responsibility (relations / encounters / narration / time, etc.) — new features only require appending a hook
4. **ActionDispatcher registry**: 50+ action-type-to-handler mappings managed centrally, dynamically registrable at runtime without touching the pipeline
5. **FastAPIInputPort normalization**: The adapter layer normalizes HTTP requests into `execution` directives (resolved / rejected) — `game_core` has no awareness of HTTP details
6. **AgenticExecutor dual-mode**: Supports both single-pass tool execution and multi-turn LLM autonomous loops — GM narration and NPC dialogue are independently extensible
7. **RoleToolRegistry isolation**: GM / NPC / teammate tool sets are registered independently; the LLM context is trimmed per role to prevent unauthorized tool access
8. **ContextAssembler L1–L7**: Seven context layers (lore / area / scene / player / events / rule results / NPC memory) assembled on demand — the narration LLM always receives the minimal sufficient context

## License

MIT
