# AI RPG Game Engine Backend

An AI-powered interactive TRPG game backend with intelligent memory management, knowledge graphs, and dynamic narrative orchestration.

Built on a **Flash-Only v2 architecture** — a single Gemini Flash model handles intent analysis, operation execution, and narrative generation in one call, balancing low latency with narrative coherence.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Web Framework | FastAPI 0.109 |
| Data Store | Google Cloud Firestore |
| AI Models | Google Gemini 3 Flash / Pro |
| Graph Algorithms | NetworkX 3.2 |
| Tool Protocol | MCP (Model Context Protocol) 1.25 |
| Token Counting | tiktoken |

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

Create a `.env` file:

```env
GEMINI_API_KEY=your_gemini_api_key
GOOGLE_APPLICATION_CREDENTIALS=./firebase-credentials.json
```

Place your Firebase service account credentials in the project root as `firebase-credentials.json`.

### 3. Run the Server

```bash
# Development mode (default stdio transport, MCP subprocesses auto-start)
uvicorn app.main:app --reload --port 8000

# Or use the startup script
bash 启动服务/run_fastapi.sh
```

Once running:
- API: http://localhost:8000/api/game/worlds
- Docs: http://localhost:8000/docs
- Health: http://localhost:8000/health

### 4. Initialize World Data

```bash
# Extract structured world data from a SillyTavern lorebook JSON
python -m app.tools.init_world_cli extract \
    --input "worldbook.json" \
    --output data/goblin_slayer/structured/ \
    --model gemini-3-pro-preview \
    --direct --relabel-edges --enrich-entities

# Load into Firestore
python -m app.tools.init_world_cli load \
    --world "goblin_slayer" \
    --data-dir data/goblin_slayer/structured/
```

## Project Structure

```
backend/
├── app/
│   ├── main.py                    # FastAPI entry point & lifecycle management
│   ├── config.py                  # Environment variables & model configuration
│   ├── dependencies.py            # Dependency injection (singletons)
│   ├── models/                    # Pydantic data models (24 models)
│   │   ├── admin_protocol.py      #   Flash protocol (IntentType, FlashOperation, AnalysisPlan)
│   │   ├── game.py                #   Game state (GamePhase, SceneState, PlayerInput/Response)
│   │   ├── narrative.py           #   Narrative (Mainline, Chapter, StoryEvent, Condition)
│   │   ├── graph.py               #   Graph nodes/edges (MemoryNode, MemoryEdge)
│   │   ├── graph_scope.py         #   6 graph scope types for unified addressing
│   │   ├── npc_instance.py        #   NPC instance (dual-layer cognition model)
│   │   ├── state_delta.py         #   Incremental state changes
│   │   └── ...
│   ├── routers/
│   │   └── game_v2.py             # Unified router, mounted at /api/game
│   ├── services/
│   │   ├── admin/                 # Core orchestration layer (12 files)
│   │   │   ├── admin_coordinator.py   # Main orchestrator (entry point)
│   │   │   ├── flash_cpu_service.py   # Flash intent analysis + operation execution
│   │   │   ├── story_director.py      # Two-phase event evaluation
│   │   │   ├── condition_engine.py    # 8 structured conditions + FLASH_EVALUATE
│   │   │   ├── state_manager.py       # In-memory snapshots + StateDelta tracking
│   │   │   ├── world_runtime.py       # World state runtime
│   │   │   ├── event_service.py       # Structured event ingestion to graph
│   │   │   ├── event_llm_service.py   # Natural language event 3-step pipeline
│   │   │   └── recall_orchestrator.py # Multi-scope memory recall
│   │   ├── memory_graph.py        # NetworkX graph container (indexes + queries)
│   │   ├── spreading_activation.py # Spreading activation algorithm
│   │   ├── memory_graphizer.py    # Auto-graphization of conversations (LLM extraction)
│   │   ├── graph_store.py         # Firestore graph persistence (GraphScope path resolution)
│   │   ├── instance_manager.py    # NPC instance pool (LRU, dual-layer cognition)
│   │   ├── context_window.py      # Working memory sliding window (200K tokens)
│   │   ├── mcp_client_pool.py     # MCP connection pool (health checks + auto-reconnect)
│   │   ├── party_service.py       # Party management
│   │   ├── teammate_response_service.py # Concurrent teammate responses
│   │   └── ...
│   ├── mcp/                       # MCP tool servers
│   │   ├── game_tools_server.py   #   Game Tools (9 tool modules)
│   │   └── tools/                 #   graph, narrative, navigation, npc, party,
│   │                              #   passerby, time, character, inventory
│   ├── combat/                    # D&D-style combat system
│   │   ├── combat_engine.py       #   Turn-based combat engine
│   │   ├── combat_mcp_server.py   #   Combat MCP server
│   │   ├── ai_opponent.py         #   Personality-driven enemy AI
│   │   ├── dice.py                #   d20 dice system
│   │   └── models/                #   Combatant, Action, CombatSession
│   ├── prompts/                   # LLM prompt templates (10 files)
│   │   ├── flash_analysis.md      #   Intent analysis (strict JSON output)
│   │   ├── flash_gm_narration.md  #   GM narration generation
│   │   ├── teammate_response.md   #   Teammate responses
│   │   └── ...
│   └── tools/                     # CLI utilities
│       ├── init_world_cli.py      #   World data extraction + loading
│       ├── game_master_cli.py     #   Interactive GM testing
│       └── worldbook_graphizer/   #   Unified extraction pipeline (Batch API support)
├── tests/                         # 46+ test files
├── data/                          # World data (Goblin Slayer)
├── 启动服务/                      # Startup scripts
└── requirements.txt
```

## Core Architecture

### Flash-Only v2 Data Flow

Entry point: `AdminCoordinator.process_player_input_v2()`

```
Player Input
    │
    ▼
┌─────────────────────────────────────────────────┐
│ 1. Collect Base Context                         │
│    World state · Session · Scene · Party · Chapter │
└────────────────────┬────────────────────────────┘
                     ▼
┌─────────────────────────────────────────────────┐
│ 2. StoryDirector Pre-Evaluation                 │
│    Mechanical conditions → auto_fired_events    │
│    Semantic conditions  → pending_flash_conditions │
└────────────────────┬────────────────────────────┘
                     ▼
┌─────────────────────────────────────────────────┐
│ 3. Flash One-Shot Analysis                      │
│    intent + operations + memory_seeds           │
│    + Flash condition evaluation + context_package │
└────────────────────┬────────────────────────────┘
                     ▼
         ┌───────────┴───────────┐
         ▼                       ▼
┌──────────────────┐  ┌──────────────────────┐
│ 4a. Memory Recall │  │ 4b. Execute Flash Ops │
│ Spreading activ.  │  │ MCP tool calls        │
│ Multi-scope merge │  │ State updates         │
└────────┬─────────┘  └──────────┬───────────┘
         └───────────┬───────────┘
                     ▼
┌─────────────────────────────────────────────────┐
│ 5. StoryDirector Post-Evaluation                │
│    Merge results → fired_events + chapter_transition │
└────────────────────┬────────────────────────────┘
                     ▼
┌─────────────────────────────────────────────────┐
│ 6. Flash GM Narration (2–4 sentences)           │
│    Full context + execution summary + memories  │
└────────────────────┬────────────────────────────┘
                     ▼
┌─────────────────────────────────────────────────┐
│ 7. Concurrent Teammate Responses                │
│    Each teammate decides whether to respond → reply │
└────────────────────┬────────────────────────────┘
                     ▼
┌─────────────────────────────────────────────────┐
│ 8. Event Distribution to Teammate Graphs        │
│    (perspective-aware transformation)           │
└────────────────────┬────────────────────────────┘
                     ▼
             CoordinatorResponse → Frontend
```

### Six Core Systems

#### 1. NPC Dual-Layer Cognition

Each NPC operates with two independent cognitive layers:

| Layer | Name | Implementation | Capacity | Purpose |
|-------|------|---------------|----------|---------|
| L1 | Synchronous Working Memory | `ContextWindow` | 200K tokens | Real-time conversation context |
| L2 | Subconscious Memory Graph | `MemoryGraph` + Spreading Activation | Unlimited | Long-term semantic memory |

**Auto-graphization**: When working memory reaches 90% → `MemoryGraphizer` uses LLM to extract old messages into graph nodes → frees token space.

**Three-Tier NPC Model** (`NPCTierConfig`):

| Tier | Thinking | Context Window | Memory Graph | Use Case |
|------|----------|---------------|-------------|----------|
| Passerby | None | None | None | Crowd NPCs, bystanders |
| Secondary | Medium | Shared | Yes | Recurring characters |
| Main | Low | Full 200K | Full + spreading activation | Protagonist-level characters |

Instances are managed by `InstanceManager` with LRU eviction (default 20 instances), with forced graphization before eviction.

#### 2. Knowledge Graph & Memory Retrieval

**GraphScope Unified Addressing** — 6 hierarchical scope types:

```
world                          → World-level knowledge
chapter(cid)                   → Chapter narrative
area(cid, aid)                 → Regional information
location(cid, aid, lid)        → Specific locations
character(char_id)             → Character personal memories
camp                           → Party shared knowledge
```

**Spreading Activation Algorithm** (`spreading_activation.py`):
1. Start from seed nodes with activation = 1.0
2. Propagate along edges with decay (0.9×) + cross-perspective/cross-chapter penalties
3. Hub penalty for high-degree nodes (> 10 edges)
4. Nodes above threshold enter result subgraph

**RecallOrchestrator** loads multi-scope graphs in parallel → merges → runs spreading activation → returns relevant memories.

#### 3. Story Director & Event System

**Two-Phase Evaluation**:

| Phase | Timing | Evaluation | Output |
|-------|--------|-----------|--------|
| Pre-evaluation | Before Flash analysis | 8 mechanical conditions | `PreDirective` (auto_fired_events + pending_flash) |
| Post-evaluation | After Flash execution | Merge all results | `StoryDirective` (fired_events + chapter_transition) |

**8 Structured Condition Types**: LOCATION / NPC_INTERACTED / TIME_PASSED / ROUNDS_ELAPSED / PARTY_CONTAINS / EVENT_TRIGGERED / OBJECTIVE_COMPLETED / GAME_STATE, with AND/OR/NOT nesting support.

**Pacing Control** (PacingConfig): `subtle_environmental` → `npc_reminder` → `direct_prompt` → `forced_event` — a 4-level escalation ensuring natural story progression.

#### 4. Combat System

D&D 5e-style turn-based combat, **pure logic with no LLM**:

- **d20 Resolution**: Attack roll + modifier vs target AC
- **Initiative Order**: d20 + DEX modifier
- **Distance System**: engaged / close / near / far / distant
- **AI Opponents**: Personality-driven (aggressive / defensive / tactical), affecting target selection and flee thresholds
- **Status Effects**: Poison, advantage/disadvantage, etc.

#### 5. MCP Tool Layer

Two MCP servers managed via `MCPClientPool` singleton:

| Server | Port | Tool Modules |
|--------|------|-------------|
| Game Tools | 9101 | graph, narrative, navigation, npc, party, passerby, time, character, inventory |
| Combat | 9102 | combat engine, enemy templates, ability checks |

**Transport support**: stdio (default) / streamable-http / sse
**Health checks**: Ping probe + auto-reconnect + 30s cooldown
**Tool timeouts**: Default 20s, `npc_respond` 90s

#### 6. Party System

- Teammates concurrently decide whether to respond each turn
- Positions auto-sync with player navigation
- Events distributed to each teammate's graph with perspective transformation
- Supported roles: LEADER / SUPPORT / SCOUT / TANK, etc.

## API Endpoints

All endpoints mounted at `/api/game`:

### World & Session

| Method | Path | Description |
|--------|------|-------------|
| GET | `/worlds` | List all worlds |
| POST | `/{world_id}/sessions` | Create session |
| GET | `/{world_id}/sessions` | List sessions |
| POST | `/{world_id}/sessions/{sid}/resume` | Resume session |

### Core Gameplay

| Method | Path | Description |
|--------|------|-------------|
| POST | `.../input` | Main entry (JSON response) |
| POST | `.../input/stream` | Main entry (SSE streaming) |
| POST | `.../scene` | Update scene |
| GET | `.../context` | Get game context |

### Character & Navigation

| Method | Path | Description |
|--------|------|-------------|
| GET | `.../character-creation/options` | Character creation options |
| POST | `.../character` | Create character |
| GET | `.../location` | Current location |
| POST | `.../navigate` | Navigate |
| POST | `.../sub-location/enter` | Enter sub-location |

### Dialogue & Combat

| Method | Path | Description |
|--------|------|-------------|
| POST | `.../dialogue/start` | Start NPC dialogue |
| POST | `.../dialogue/end` | End dialogue |
| POST | `.../private-chat/stream` | Private teammate chat (SSE) |
| POST | `.../combat/trigger` | Trigger combat |
| POST | `.../combat/action` | Combat action |
| POST | `.../combat/resolve` | Resolve combat |

### Party & Narrative

| Method | Path | Description |
|--------|------|-------------|
| POST | `.../party` | Create party |
| POST | `.../party/add` | Add teammate |
| GET | `.../narrative/progress` | Narrative progress |
| POST | `.../narrative/trigger-event` | Trigger story event |

### Other

| Method | Path | Description |
|--------|------|-------------|
| GET | `.../time` | Game time |
| POST | `.../time/advance` | Advance time |
| GET | `.../passersby` | Scene passersby |
| POST | `.../passersby/dialogue` | Talk to passerby |
| GET | `.../history` | Session history |
| POST | `/{world_id}/events/ingest` | Structured event ingestion |
| POST | `/{world_id}/events/ingest-natural` | Natural language event |

## Firestore Data Structure

```
worlds/{world_id}/
├── graphs/world/nodes/, edges/                                     ← GraphScope.world()
├── chapters/{cid}/graph/nodes/, edges/                             ← GraphScope.chapter()
├── chapters/{cid}/areas/{aid}/graph/nodes/, edges/                 ← GraphScope.area()
├── chapters/{cid}/areas/{aid}/locations/{lid}/graph/nodes/, edges/ ← GraphScope.location()
├── characters/{char_id}/nodes/, edges/, instances/, dispositions/  ← GraphScope.character()
├── camp/graph/nodes/, edges/                                       ← GraphScope.camp()
├── maps/{map_id}/locations/{location_id}/
├── sessions/{session_id}/state/, events/
└── mainlines/{mainline_id}/...
```

## Configuration

### Required Environment Variables

| Variable | Description |
|----------|-------------|
| `GEMINI_API_KEY` | Gemini API key |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to Firebase credentials |

### Model Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_FLASH_MODEL` | `gemini-3-flash-preview` | Primary Flash model |
| `ADMIN_FLASH_MODEL` | `gemini-3-flash-preview` | Admin layer model |
| `ADMIN_FLASH_THINKING_LEVEL` | `high` | Flash thinking level |
| `NPC_PASSERBY_MODEL` | (same as flash) | Passerby NPC model |
| `NPC_SECONDARY_MODEL` | (same as flash) | Secondary NPC model |
| `NPC_MAIN_MODEL` | (same as flash) | Main NPC model |

### MCP Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_TOOLS_TRANSPORT` | `stdio` | Game Tools transport |
| `MCP_COMBAT_TRANSPORT` | `stdio` | Combat transport |
| `MCP_TOOL_TIMEOUT_SECONDS` | `20` | Default tool timeout |
| `MCP_NPC_TOOL_TIMEOUT_SECONDS` | `90` | NPC tool timeout |

### Instance Pool Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `INSTANCE_POOL_MAX_INSTANCES` | `20` | Max NPC instances |
| `INSTANCE_POOL_CONTEXT_WINDOW_SIZE` | `200000` | Context window tokens |
| `INSTANCE_POOL_GRAPHIZE_THRESHOLD` | `0.8` | Graphization trigger threshold |

## Running Tests

```bash
# All tests
pytest -v

# Single test file
pytest tests/test_spreading_activation.py -v

# E2E tests (requires MCP HTTP services running)
bash 启动服务/run_mcp_services.sh
bash 启动服务/run_e2e_tests.sh
```

## World Data Extraction

Generate all structured files from a SillyTavern lorebook JSON in one step:

```bash
# Batch API mode (recommended, 50% cost savings)
python -m app.tools.init_world_cli extract \
    --input "worldbook.json" \
    --output data/goblin_slayer/structured/ \
    --model gemini-3-pro-preview \
    --thinking-level none \
    --relabel-edges --enrich-entities

# Direct mode (real-time results)
python -m app.tools.init_world_cli extract \
    --input "worldbook.json" \
    --output data/goblin_slayer/structured/ \
    --model gemini-3-pro-preview \
    --direct --relabel-edges --enrich-entities
```

Output files: maps.json, characters.json, world_map.json, character_profiles.json, world_graph.json, prefilled_graph.json, chapters_v2.json, monsters.json, items.json, skills.json

## Interactive Development Tools

```bash
python -m app.tools.game_master_cli              # Full game management REPL
python -m app.tools.game_master_cli --setup-demo # Initialize demo world
python -m app.tools.flash_natural_cli            # Flash service testing
python -m app.tools.gm_natural_cli               # GM narration testing
```

## Design Highlights

1. **Flash-Only Architecture**: A single model call handles intent analysis + operation planning + condition evaluation, reducing latency and multi-model orchestration complexity
2. **Dual-Layer NPC Cognition**: Working memory (real-time conversation) + long-term memory graph (semantic retrieval), with auto-graphization enabling unlimited conversation capacity
3. **GraphScope Unified Addressing**: 6 hierarchical scopes covering world → chapter → area → location → character → camp, with one API for all graph operations
4. **Spreading Activation Retrieval**: Graph-theory-based memory recall with cross-perspective/cross-chapter decay, capturing narrative causality better than vector similarity
5. **Two-Phase Event System**: Mechanical conditions (deterministic) + semantic conditions (LLM-judged), balancing controllability and flexibility
6. **Pacing Control Engine**: 4-level progressive escalation from environmental hints to forced events, ensuring natural story flow
7. **Perspective-Aware Event Distribution**: The same event is transformed into participant/witness/bystander perspectives before writing to different character graphs
8. **MCP Tool Abstraction**: Game logic exposed via MCP protocol, supporting stdio/HTTP/SSE transports for easy independent testing and extension

## License

MIT
