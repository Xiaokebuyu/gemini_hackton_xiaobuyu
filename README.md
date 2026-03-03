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
│   ├── main.py                        # FastAPI entry point
│   ├── deps.py                        # Singleton construction & dependency injection
│   ├── interaction_service.py         # App layer: presence validation + execution orchestration
│   ├── interaction_views.py           # Snapshot view builders (shop / quest / dialogue)
│   ├── agent_orchestration.py         # Agentic session orchestration service
│   ├── llm_gemini.py                  # Gemini LLM adapter
│   ├── world_knowledge_graph.py       # World knowledge graph (NetworkX)
│   ├── world_data_loader.py           # Structured world data loader
│   ├── routers/
│   │   ├── sessions.py                # Session management routes
│   │   ├── character.py               # Character creation & panel routes
│   │   ├── panels.py                  # Inventory / map / quest panel routes
│   │   └── gameplay.py                # Gameplay action & streaming routes
│   └── game_core/                     # ★ Hexagonal pure-Python kernel (zero external deps)
│       ├── state/                     # State layer — 10 StateSlices + StateDelta
│       ├── content/                   # Content layer — WorldInstance + 10 ContentRegistries
│       ├── rules/                     # Rules layer — RulesEngine + 16+ CommandHandlers
│       ├── orchestration/             # Orchestration layer — TickCoordinator + 14 SettlementHooks
│       ├── narrative/                 # Narrative layer — AgenticExecutor + RoleToolRegistry
│       ├── adapters/                  # Adapter layer — port protocols + default implementations
│       └── planning/                  # Planning layer — dynamic sub-area generation
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
│  ActionDispatcher · ContextAssembler · 14 SettlementHooks│
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
    │  Presence validation (NPC / board in scene?)
    │  Precondition validation (intent, item, quest)
    ▼
TickCoordinator.process()
    │
    ├─► PipelineOrchestrator
    │       ├─► ContextAssembler (L1–L7 context assembly)
    │       ├─► ActionDispatcher  (action_type → Command)
    │       └─► RulesEngine.execute(Command, state, world)
    │               └─► StateDelta (state change description)
    │
    └─► SettlementHooks (ordered by priority)
            ScheduledEvent → StatusEffect → AIOsiris
            → NarrativePlanner → Encounter → EventCondition
            → NpcSchedule → Relationship → PrivateChatTrigger
            → TimeAdvance → DynamicSubAreaExpiry
            → GmNarration (LLM) → SceneBusReset
    │
    ▼
PipelineResult → SSE event stream → Frontend
```

### Ten State Slices

| Slice | Responsibility |
|-------|---------------|
| TimeSlice | Game time (rounds / hours / days) |
| PlayerSlice | Player location, attributes, HP, spell slots |
| AreaSlice | Current area & sub-location states |
| QuestSlice | Quest states (active / completed / retired) |
| SceneSlice | Current scene description & NPC list |
| RelationsSlice | NPC relationship stage + approval value |
| FlagSlice | Global flags (schemaless dict) |
| EventsSlice | Recent event history |
| PartySlice | Party member list |
| NarrativePlanSlice | Narrative plan & beat state |

### Narrative Layer (AgenticExecutor)

`AgenticExecutor` supports two modes:

- **Single-pass** `run()`: Executes a pre-built `tool_calls` list — used for post-action structured processing
- **Multi-turn agentic loop** `run_agentic()`: LLM autonomously selects tool calls — used for GM narration and NPC dialogue

`RoleToolRegistry` assigns tool sets by role (gm / npc / teammate). The LLM only sees tools available to its assigned role. `InstanceManager` manages NPC instances with LRU eviction.

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
| POST | `.../navigate` | Navigation (move_area / enter / leave sub-location) |
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
| `GOOGLE_APPLICATION_CREDENTIALS` | Firebase credentials path (falls back to local JSON) |
| `GEMINI_FLASH_MODEL` | Flash model name |
| `GEMINI_PRO_MODEL` | Pro model name |

## Running Tests

```bash
# From the backend/ directory
PYTHONPATH=. pytest --ignore=tests/test_api_shell.py --ignore=tests/test_interaction_service.py -v

# Single test file
PYTHONPATH=. pytest tests/test_game_core_scaffold.py -v
```

Test baseline: 290+ passed (`asyncio_mode = "auto"`).

## Design Highlights

1. **Hexagonal pure-Python kernel**: `game_core/` has zero external dependencies — any database, LLM, or framework can be swapped out; unit tests require no infrastructure mocks
2. **Immutable StateDelta**: All state mutations are described by `StateDelta` and applied atomically by `StateContainer.apply()`, enabling replay, auditing, and undo
3. **SettlementHook chain**: 14 ordered hooks form a pluggable post-processing pipeline, each with a single responsibility — new features only require appending a hook
4. **ActionDispatcher registry**: 50+ action-type-to-handler mappings managed centrally, dynamically registrable at runtime without touching the pipeline
5. **FastAPIInputPort normalization**: The adapter layer normalizes HTTP requests into `execution` directives — `game_core` has no awareness of HTTP details
6. **AgenticExecutor dual-mode**: Supports both single-pass tool execution and multi-turn LLM autonomous loops — GM narration and NPC dialogue are independently extensible
7. **RoleToolRegistry isolation**: GM / NPC / teammate tool sets are registered independently; the LLM context is trimmed per role to prevent unauthorized tool access
8. **ContextAssembler L1–L7**: Seven context layers assembled on demand — the narration LLM always receives the minimal sufficient context

## License

MIT
