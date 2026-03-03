# AI CRPG Game Engine Backend

An AI-powered CRPG game backend in the style of Baldur's Gate 3, built on a **hexagonal pure-Python kernel** — `game_core` has zero external dependencies, with all external capabilities (LLM, persistence, knowledge graph) injected through adapter ports for maximum testability and extensibility.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Web Framework | FastAPI 0.109 + uvicorn |
| Data Store | Google Cloud Firestore / local JSON |
| AI Models | Google Gemini Flash (`google-genai`) |
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
│   ├── agent_orchestration.py         # Agentic session orchestration (NPC / GM / Teammate)
│   ├── narrators.py                   # GM narration LLM adapter (Settlement narrator)
│   ├── evaluators.py                  # Agentic AI Osiris evaluator (causal judgment)
│   ├── llm_gemini.py                  # Gemini LLM adapter
│   ├── world_knowledge_graph.py       # World knowledge graph (NetworkX BFS spreading activation)
│   ├── world_data_loader.py           # Structured world data loader
│   ├── routers/
│   │   ├── sessions.py                # Session management routes
│   │   ├── character.py               # Character creation & panel routes
│   │   ├── panels.py                  # Inventory / map / quest panel routes
│   │   └── gameplay.py                # Gameplay action & streaming routes
│   └── game_core/                     # ★ Hexagonal pure-Python kernel (zero external deps)
│       ├── state/                     # State layer — 10 StateSlices + StateDelta
│       ├── content/                   # Content layer — WorldInstance + 10 Registries + 43 typed dataclasses
│       ├── rules/                     # Rules layer — RulesEngine + 22 handler modules
│       ├── orchestration/             # Orchestration layer — TickCoordinator + 13 SettlementHooks
│       ├── narrative/                 # Narrative layer — AgenticExecutor + 18 agent tools (3 roles)
│       ├── adapters/                  # Adapter layer — port protocols + default implementations
│       └── planning/                  # Planning layer — dynamic sub-area generation
├── tests/                             # 1044 tests passed
├── data/                              # World data (Goblin Slayer, etc.)
└── requirements.txt
```

## Game Systems

The engine implements a comprehensive CRPG ruleset modeled on D&D 5e. All mechanics are turn-based — each player action advances time by a fractional tick (1 tick = 6 seconds of game time), and settlement hooks fire when a full tick accumulates.

### Combat

Turn-based initiative combat with action economy. Players can **attack**, **defend**, **disengage**, **dash**, **shove**, **flee**, or **use combat items**. Attack resolution rolls d20 + modifier vs. target AC; damage varies by weapon/spell dice. Status effects (stun, paralysis, poison, bleed) apply real mechanical consequences — stunned characters cannot act. Monster AI controls flee behavior and tactical decisions based on `ai_personality` and `flee_threshold`.

### Spell & Magic

D&D 5e spell slot system with concentration tracking. Players prepare spells from their class list, cast using level-appropriate slots, and manage concentration (casting a new concentration spell breaks the previous one). Spells support saving throws (target rolls vs. spell DC), AOE multi-targeting, upcast scaling, and status effect application.

### Exploration

Area-based navigation with sub-locations. Players move between connected areas, enter sub-locations (tavern, shop, shrine), and interact with environmental objects. Containers can be looted, traps disarmed via skill checks, and hidden discoveries revealed by passive perception. The **DynamicSubAreaManager** generates temporary locations at runtime (hidden chambers, encounter zones) that expire after a set number of ticks.

### Economy & Trading

Gold-based trading with NPC shops. NPCs maintain shop inventories with stock counts and optional refresh cycles. Players buy/sell items; shop stock depletes on purchase and can refresh on long rest or time-based triggers.

### Character Growth

XP-based leveling (1–20) with class feature trees. On level-up, players gain HP, unlock class features (`level_features` mapped per level), and choose subclasses at level 3. Ability Score Improvements at milestone levels. Class resources (rage, ki, superiority dice) scale per level with recovery on rest.

### NPC Interaction & Dialogue

Agentic 6-step pipeline: player message → NPC agent response (LLM with tool calls) → GM observation → teammate reactions → dialogue options presented → player chooses. NPC dispositions track **approval**, **trust**, **fear**, and **romance** independently. Relationship stages progress from stranger → acquaintance → friend → close_friend → soulmate, with stage transitions evaluated at settlement time.

### Private Chat

NPC-initiated intimate conversations triggered when romance >= 60, trust >= 50, or relationship stage is intimate. Cooldown of 6 ticks between triggers. Emits `npc_wants_to_chat` SSE event; execution follows a similar pipeline to NPC interaction but without party member reactions.

### Narrative & World Events

Reactive storytelling driven by settlement hooks. The **NarrativePlanner** sequences story beats; **GmNarrationHook** generates scene narration via LLM; the **EventEngine** evaluates 8 condition types (flag, quest_state, location, item, disposition, time_elapsed, custom, has_party_member) to trigger scheduled events. NPC schedules drive daily movement patterns (sleep → work → social hours).

### World Knowledge Graph

NetworkX-based knowledge graph with BFS spreading activation. Content-layer relationships (character→area, character→faction, faction→faction) form the base graph; NPC interactions dynamically add triple edges (subject→predicate→object). Memory retrieval provides contextually relevant world knowledge to NPC system prompts, enabling NPCs to "know" about events and relationships they've witnessed.

---

## Documentation

| Directory | Contents |
|-----------|----------|
| `app/博德之门3架构设计规范/` | 14 architecture design specs (content/state/rules/orchestration/narrative layers, AI-Osiris, NPC runtime, etc.) |
| `app/施工记录（持续更新）/` | Development logs — per-layer change records (D-Rxx), decision rationale, active TODO list |
| `app/增量执行计划/` | Incremental correction plans (R-1a through R-5) from design-doc alignment audits |

---

## Core Architecture

### Hexagonal Layering

```
┌─────────────────────────────────────────────────────────┐
│  Application Layer (app/)                                │
│  FastAPI routes → InteractionService → InteractionViews  │
│  AgentOrchestration · Narrators · Evaluators             │
├─────────────────────────────────────────────────────────┤
│  Adapter Layer (game_core/adapters/)                     │
│  FastAPIInputPort · LlmPort · PersistencePort · OutputPort│
├─────────────────────────────────────────────────────────┤
│  Orchestration Layer (game_core/orchestration/)          │
│  TickCoordinator → PipelineOrchestrator                  │
│  ActionDispatcher · ContextAssembler · EventEngine       │
│  13 SettlementHooks (P10–P90 priority chain)             │
├─────────────────────────────────────────────────────────┤
│  Rules Layer (game_core/rules/)                          │
│  RulesEngine → 59 action command types                   │
├─────────────────────────────────────────────────────────┤
│  Content Layer (game_core/content/)                      │
│  WorldInstance + 10 Registries + 43 typed dataclasses    │
├─────────────────────────────────────────────────────────┤
│  State Layer (game_core/state/)                          │
│  StateContainer + 10 StateSlices + StateDelta            │
├─────────────────────────────────────────────────────────┤
│  Narrative Layer (game_core/narrative/)                   │
│  AgenticExecutor + RoleToolRegistry + InstanceManager    │
│  18 agent tools across GM / NPC / Teammate roles         │
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
    │       ├─► ContextAssembler (L0–L7 context layers)
    │       ├─► ActionDispatcher  (action_type → Command)
    │       └─► RulesEngine.execute(Command, state, world)
    │               └─► StateDelta (state change description)
    │
    └─► SettlementHooks (ordered by priority P10–P90)
            P10 ScheduledEvent → P20 StatusEffect → P30 AIOsiris
            → P35 NarrativePlanner → P40 Encounter
            → P50 Relationship → P60 NpcSchedule
            → P65 EventCondition → P70 TimeAdvance
            → P75 PrivateChatTrigger → P80 DynamicSubAreaExpiry
            → P90 GmNarration (LLM) → SceneBusReset
    │
    ▼
PipelineResult → SSE event stream → Frontend
```

### Ten State Slices

| Slice | Responsibility |
|-------|---------------|
| TimeSlice | Game time (rounds / hours / days), absolute tick counter |
| PlayerSlice | Location, attributes, HP, spell slots, inventory, active effects |
| AreaSlice | Current area & sub-location states, hostile tracking |
| QuestSlice | Quest states (active / completed / retired), dynamic quests |
| SceneSlice | Current scene description & NPC presence list |
| RelationsSlice | NPC dispositions (approval/trust/fear/romance), relationship stages, shop stock |
| FlagSlice | Global flags (schemaless dict) |
| EventsSlice | Event history, rumors, triggered event tracking |
| PartySlice | Party members, shared experiences, critical moments |
| NarrativePlanSlice | Narrative plan, current beat, chapter progression |

### Content Layer (10 Registries, 43 Typed Dataclasses)

| Registry | Key Types | Count |
|----------|-----------|-------|
| MapRegistry | AreaTemplate, SubLocationTemplate, InteractableTemplate, Connection, HostileConfig, EncounterEntry | 12 types |
| CharacterRegistry | CharacterTemplate, NpcAttack, ShopInventory, ShopEntry | 4 types |
| MonsterRegistry | MonsterTemplate, MonsterAttack, LootEntry | 3 types |
| ClassRegistry | ClassTemplate, SubclassTemplate, RaceTemplate, BackgroundTemplate, Feature, ResourceConfig, SpellcastingConfig | 7 types |
| SkillRegistry | SkillTemplate, SkillEffect, SkillCost, StatusEffectTemplate | 4 types |
| ItemRegistry | ItemTemplate, WeaponData, ArmorData, ConsumableData, AccessoryData | 5 types |
| QuestRegistry | MilestoneTemplate, MilestoneCondition | 2 types |
| LoreRegistry | LoreEntry, WorldRule, ChapterMeta, InitialEvent | 4 types |
| FactionRegistry | FactionTemplate | 1 type |
| TagRegistry | TagDimension | 1 type |
| (shared) | Effect, LootTableDef | 2 types |

### Narrative Layer (AgenticExecutor)

`AgenticExecutor` supports two modes:

- **Single-pass** `run()`: Executes a pre-built `tool_calls` list — used for post-action structured processing
- **Multi-turn agentic loop** `run_agentic()`: LLM autonomously selects tool calls — used for GM narration and NPC dialogue

Three role-based tool sets managed by `RoleToolRegistry`:

| Role | Tools | Purpose |
|------|-------|---------|
| **GM** | DescribeEnvironment, Narrate, Comment, PassTurn, SuggestOptions | Scene narration, option generation |
| **NPC** | Speak, Emote, UpdateFeeling, Remember, OfferQuest, OfferTrade, Refuse, RevealSecret | Dialogue, relationship, commerce |
| **Teammate** | Speak, Emote, ExpressOpinion, SuggestTactic, ShareMemory, RequestAction | Party interaction, tactical advice |

`InstanceManager` manages NPC conversation instances with LRU eviction. `WorldKnowledgeGraph` provides BFS spreading-activation memory retrieval for contextual NPC knowledge.

### LLM Integration

LLM features degrade gracefully — the system runs fully deterministic without an API key.

| Component | Purpose | Fallback |
|-----------|---------|----------|
| GmNarrationHook | Settlement-phase scene narration | Skipped (no narration) |
| AgentOrchestrationService | NPC dialogue, GM reactions, Teammate responses | Deterministic stub responses |
| AgenticAIOsirisEvaluator | Causal event judgment (did player's action cause X?) | BasicAIOsirisEvaluator (rule-based) |
| AgenticNarrativePlanner | Story beat planning via LLM | Deterministic next-beat fallback |

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
| `GEMINI_FLASH_MODEL` | Flash model name override |

## Running Tests

```bash
# From the backend/ directory
PYTHONPATH=. pytest --ignore=tests/test_api_shell.py --ignore=tests/test_interaction_service.py -v

# Single test file
PYTHONPATH=. pytest tests/test_game_core_scaffold.py -v
```

Test baseline: **1044 passed**.

## Design Highlights

1. **Hexagonal pure-Python kernel**: `game_core/` has zero external dependencies — any database, LLM, or framework can be swapped; unit tests require no infrastructure mocks
2. **Immutable StateDelta**: All state mutations are described by `StateDelta` and applied atomically by `StateContainer.apply()`, enabling replay, auditing, and undo
3. **SettlementHook chain**: 13 ordered hooks (P10–P90) form a pluggable post-processing pipeline — new features only require appending a hook at the right priority
4. **59 action command types**: Centrally managed action-type-to-handler mappings via `ActionDispatcher`, dynamically registrable at runtime
5. **43 typed content dataclasses**: All content templates are `@dataclass(slots=True)` with defensive loading (load_issues collection, backward-compatible defaults)
6. **AgenticExecutor dual-mode**: Supports both single-pass tool execution and multi-turn LLM autonomous loops — GM, NPC, and Teammate are independently extensible
7. **RoleToolRegistry isolation**: GM / NPC / Teammate tool sets are registered independently; the LLM context is trimmed per role to prevent unauthorized tool access
8. **ContextAssembler L0–L7**: Eight context layers assembled on demand — the narration LLM always receives the minimal sufficient context
9. **WorldKnowledgeGraph**: NetworkX-based spreading activation retrieves contextually relevant world knowledge for NPC conversations
10. **Graceful LLM degradation**: Every LLM integration point has a deterministic fallback — the entire game loop works without an API key

## License

MIT
