<p align="center">
  <h1 align="center">AI CRPG Engine</h1>
  <p align="center">
    <strong>An AI-powered CRPG game engine inspired by Baldur's Gate 3</strong>
  </p>
  <p align="center">
    <img src="https://img.shields.io/badge/python-3.13-blue?logo=python&logoColor=white" alt="Python 3.13" />
    <img src="https://img.shields.io/badge/LLM-Gemini_Flash-4285F4?logo=google&logoColor=white" alt="Gemini Flash" />
    <img src="https://img.shields.io/badge/tests-3043_passed-brightgreen" alt="Tests" />
    <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License" />
    <img src="https://img.shields.io/badge/react-19-61DAFB?logo=react&logoColor=white" alt="React 19" />
  </p>
  <p align="center">
    English | <a href="./README.md">中文</a>
  </p>
</p>

---

A full-stack CRPG engine built on a **hexagonal pure-Python kernel** (zero external dependencies), featuring a **3-role LLM Agent system** (GM / NPC / Teammate), a **D&D 5e rule subset**, and **real-time SSE streaming narrative**. Every LLM feature degrades gracefully — the game runs fully functional without an API key.

## At a Glance

| Metric | Value |
|--------|-------|
| Backend Python modules | 203 |
| Core kernel modules (zero deps) | 167 |
| Command types | ~96 |
| Settlement Hooks | 25 |
| Content dataclasses | 45+ |
| Tests passed | 3,043 |
| Backend code | ~74,000 lines |
| Test code | ~78,000 lines |
| Frontend code | ~8,800 lines |
| SSE event types | 40+ |
| Zustand stores | 12 |

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    Frontend · React 19 + Zustand + Tailwind       │
│            12 Stores · useGameStream (SSE consumer)               │
└────────────────────────────┬─────────────────────────────────────┘
                             │ HTTP / SSE
┌────────────────────────────▼─────────────────────────────────────┐
│                    Application Layer · FastAPI                     │
│    deps.py (DI) · GeminiAdapter · AgentOrchestration · Narrators  │
│    WorldKnowledgeGraph · AIOsirisEvaluator · ViewBuilders         │
└────────────────────────────┬─────────────────────────────────────┘
                             │ Port Protocols
┌────────────────────────────▼─────────────────────────────────────┐
│                   game_core/ · Hexagonal Kernel                    │
│                                                                    │
│    L1 State ──► L2 Content ──► L3 Rules ──► L4 Orchestration      │
│                                   │               │                │
│                               L5 Narrative ◄─────┘                │
│                                   │                                │
│                               L6 Planning                          │
│                                                                    │
│                            Adapters / Ports                        │
└──────────────────────────────────────────────────────────────────┘
```

The kernel (`game_core/`) contains 167 Python modules with **zero external imports**. LLM, database, and SSE output are all injected via Port Protocols — swap any provider without touching the kernel.

### Tick Lifecycle

```
Player Input
    │
    ▼
ActionDispatcher → Command (~96 types)
    │
    ▼
RulesEngine.execute() → StateDelta
    │
    ▼
StateContainer.apply()                 ← single write entry point
    │
    ▼  (accumulated_time >= 1.0)
Settlement Hook Chain  P20 → P90
    ├─ P30  AIOsiris           Causal judgment (LLM)
    ├─ P35  NarrativePlanner   Story planning
    ├─ P45  PassivePerception  Auto-detect hidden objects
    ├─ P50  EventCondition     Trigger evaluation
    ├─ P60  NpcSchedule        NPC daily movement
    ├─ P63  Campfire           Campfire dialogue on long rest
    ├─ P65  Relationship       Stage transitions + auto-dismiss
    ├─ P75  PrivateChatTrigger Initiate NPC private chat
    └─ P80  GmNarration        LLM/template narration
    │
    ▼
SSE Event Stream → Frontend
```

## Key Design

### 1. Three-Role LLM Agent System

| Role | Tools | Can Modify State? | Info Visibility |
|------|-------|:-----------------:|-----------------|
| **GM** | narrate, comment, suggest_options, describe_environment, pass_turn | No (SSE only) | Full (L0–L7) |
| **NPC** | speak, emote, update_feeling, remember, offer_quest, offer_trade, refuse | Yes (disposition, quests) | Restricted (no quests/plan) |
| **Teammate** | speak, emote, express_opinion, leave_party | Yes (disposition, leave) | Restricted (no quest details) |

**Agent behavior is controlled by system architecture, not prompts:**

- **Tool routing** — `OfferTradeTool.applicable_traits = ["merchant"]`: only merchant NPCs have the trade tool registered. The LLM literally cannot call a tool that doesn't exist in its declaration.
- **Protocol constraints** — Max 1 speak + 1 emote per turn; loop terminates on visible output.
- **State isolation** — `RoleStateProxy` blocks NPC access to `quests` / `narrative_plan` at the code level.
- **Graceful degradation** — Every LLM point has a deterministic fallback. No API key = game still runs.

### 2. L0–L7 Context Engineering

Each agent call assembles 7 context layers with **per-role visibility filtering**:

| Layer | Content | GM | NPC | Teammate |
|-------|---------|:--:|:---:|:--------:|
| L0 | World lore & factions | All | All | All |
| L1 | Chapter progress & milestones | All | Hidden | Available only |
| L2 | Area environment | All | All | All |
| L3 | Location details | All | All | All |
| L4 | Dynamic state (dispositions, relations) | Global | Self only | Self + party |
| L5 | Scene bus (recent dialogue) | All | Filtered | Filtered |
| L6 | Memory retrieval (KG) | None | BFS top-5 | BFS top-5 |
| L7 | Engine results (dice, commands) | All | Hidden | Hidden |

### 3. Three-Tier Memory Architecture

```
Short-term ─── ContextWindow (per-actor FIFO, 32K tokens)
                  │  overflow → write_episode()
                  ▼
Long-term ──── Per-actor Knowledge Graph (NetworkX)
                  │  LLM extracts [subject, relation, object, weight] triples
                  │  BFS spreading activation (decay=0.8) → top-5 into system prompt
                  ▲
                  │  merged query
Global ──────── Static Knowledge Graph
                  │  seeded from content layer (11 edge types)
                  +
External ────── Directive Queue (injected by Planning layer)
                  NPC consumes directives without knowing the source
```

### 4. StateDelta Atomic State

All state mutations are described as `StateDelta` (list of `StateChange`) and applied through a single entry point. No setters, no direct modification.

```python
delta = StateDelta(changes=[
    StateChange("player", "set", "hp", 45),
    StateChange("relations", "add", "npc_dispositions.goblin_chef.approval", -10),
], reason="combat_attack")
state.apply(delta)  # the only write path
```

Benefits: full audit trail, safe concurrent reads by 25 Settlement Hooks, dry-run capability, snapshot replay.

### 5. Emergent Narrative

No central scriptwriter. 25 Settlement Hooks execute independently by priority, 6 Planning subsystems produce 18 types of Directives, and their interactions generate unpredictable but coherent narrative.

## Game Systems

### Combat

Turn-based D&D 5e combat: d20 attack rolls, AC comparison, damage dice, advantage/disadvantage, critical hits. `BattleGrid` implements Dijkstra pathfinding (weighted terrain costs), A* search (Manhattan heuristic), and Bresenham line-of-sight. Monster AI uses personality-driven decision trees (aggressive / defensive / cowardly) with target scoring.

### Spells & Magic

Spell slot system with concentration tracking, saving throws, AOE targeting, upcast scaling, and status effects (stun, paralysis, poison, bleed with mechanical consequences).

### Exploration

Area-based navigation with sub-locations and rooms. Containers, traps (disarm via skill check), hidden discoveries (passive perception auto-detect), dynamic sub-areas generated at runtime via `DynamicSubAreaManager`.

### Relationships

Four-dimensional NPC dispositions: approval, trust, fear, romance. Six relationship stages with bidirectional transitions:

```
stranger → acquaintance → friend → close_friend → intimate
                 ↓              ↓            ↓           ↓
                cold ──────► hostile ──────► enemy
                               ↑ auto-dismiss from party
```

### Economy & Growth

Gold-based trading with NPC shops (stock depletion, refresh cycles). XP leveling (1–20), class feature trees, subclass selection at level 3, ASI at milestone levels.

## Tech Stack

### Backend

| Component | Technology |
|-----------|-----------|
| Language | Python 3.13 |
| Framework | FastAPI + uvicorn |
| LLM | Google Gemini Flash (`google-genai`) |
| Knowledge Graph | NetworkX 3.2 |
| Persistence | Google Cloud Firestore / local JSON |
| Testing | pytest (3,043 tests) |

### Frontend

| Component | Technology |
|-----------|-----------|
| Language | TypeScript |
| Framework | React 19 + Vite |
| State Management | Zustand 5 (12 stores) |
| Styling | Tailwind CSS |
| Real-time | SSE (Server-Sent Events) |
| Animation | Framer Motion |

## Project Structure

```
.
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI entry point
│   │   ├── deps.py                    # Dependency injection root
│   │   ├── llm_gemini.py             # Gemini LLM adapter
│   │   ├── agent_orchestration.py    # 3-role agent orchestration
│   │   ├── narrators.py             # GM settlement narrator
│   │   ├── evaluators.py            # AI-Osiris causal evaluator
│   │   ├── world_knowledge_graph.py  # NetworkX knowledge graph
│   │   ├── interaction_service.py    # Interaction validation & orchestration
│   │   ├── routers/                  # API route handlers
│   │   │
│   │   └── game_core/               # ★ Hexagonal kernel (zero external deps)
│   │       ├── state/               #   L1: 10 StateSlices + StateDelta
│   │       ├── content/             #   L2: 11 Registries + 45+ dataclasses
│   │       ├── rules/               #   L3: RulesEngine + 27 Handlers (~96 commands)
│   │       ├── orchestration/       #   L4: TickCoordinator + 25 Settlement Hooks
│   │       ├── narrative/           #   L5: AgenticExecutor + L0-L7 ContextBuilder
│   │       ├── planning/            #   L6: 6 Subsystems + 18 Directive types
│   │       └── adapters/            #   Port Protocols + null implementations
│   │
│   ├── tests/                       # 3,043 tests, ~78,000 lines
│   └── data/                        # World data (structured JSON)
│
└── frontend/
    └── src/
        ├── pages/                   # GamePage (main route)
        ├── game/                    # Game UI components
        ├── hooks/                   # useGameStream (SSE consumer)
        ├── stores/                  # 12 Zustand stores
        ├── types/                   # SSE event types (40+)
        └── lib/                     # SSE client
```

## Quick Start

### Prerequisites

- Python 3.13+
- Node.js 18+

### Backend

```bash
cd backend

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment (optional — game runs without API key)
cat > .env << 'EOF'
GOOGLE_API_KEY=your_gemini_api_key
EOF

# Run server
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Verify

| URL | Description |
|-----|-------------|
| http://localhost:8000/health | Health check |
| http://localhost:8000/docs | API documentation (Swagger) |
| http://localhost:5173 | Frontend |

### Run Tests

```bash
cd backend
PYTHONPATH=. pytest --ignore=tests/test_api_shell.py --ignore=tests/test_interaction_service.py -v
```

## API Endpoints

### Session Management

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/api/game/worlds` | List worlds |
| POST | `/api/game/{world_id}/sessions` | Create session |
| POST | `.../sessions/{sid}/resume` | Resume session |
| DELETE | `.../sessions/{sid}` | Delete session |

### Gameplay (SSE Streaming)

| Method | Path | Description |
|--------|------|-------------|
| POST | `.../act` | Structured action |
| POST | `.../navigate` | Area navigation |
| POST | `.../interact` | NPC / board interaction |
| POST | `.../private_chat` | Private chat |
| POST | `.../text_input` | Free text input |
| GET | `.../scene` | Scene state |

### Panels

| Method | Path | Description |
|--------|------|-------------|
| GET | `.../character` | Character panel |
| GET | `.../inventory` | Inventory panel |
| GET | `.../quests` | Quest panel |
| GET | `.../map` | Map panel |

### Combat

| Method | Path | Description |
|--------|------|-------------|
| POST | `.../encounter` | Encounter decision (fight / flee / stealth) |
| POST | `.../combat` | Combat action |
| GET | `.../combat` | Combat state |

## Configuration

| Variable | Required | Description |
|----------|:--------:|-------------|
| `GOOGLE_API_KEY` | No | Gemini API key. Without it, all LLM features degrade to deterministic fallbacks — the game runs fully functional. |
| `GOOGLE_APPLICATION_CREDENTIALS` | No | Firebase credentials path. Falls back to local JSON persistence. |

> **No API key? No problem.** The entire game loop — combat, exploration, trading, quests, NPC schedules — works without any external service. LLM adds richer narration, NPC personality, and dynamic story planning on top.

## Design Highlights

1. **Hexagonal kernel** — `game_core/` has zero external imports. 3,043 tests pass in CI without API keys, databases, or mocks.
2. **Agent safety by architecture** — Tool routing (`applicable_traits`), protocol constraints (max 1 speak/emote), state isolation (`RoleStateProxy`), deterministic fallbacks. Behavior boundaries are enforced by code, not prompts.
3. **L0–L7 context engineering** — 7-layer context assembly with per-role visibility matrix. Each agent sees only the information appropriate to its role.
4. **Three-tier memory** — FIFO working memory (32K) + NetworkX knowledge graph (BFS spreading activation) + directive injection. NPCs maintain persistent memory across sessions.
5. **StateDelta immutability** — All mutations described as deltas, applied through a single `apply()` entry point. Full audit trail, safe concurrent reads, dry-run capability.
6. **Settlement Hook chain** — 25 hooks at priorities P20–P90. New features = insert a hook at the right priority. Zero modification to core loop.
7. **Emergent narrative** — 6 planning subsystems + 18 directive types. No central scriptwriter — narrative emerges from independent hook/planner interactions.
8. **Dual-path SSE streaming** — Discrete events (drain buffer) + continuous text chunks (direct passthrough). Per-session `asyncio.Lock` + Queue-driven concurrency.
9. **D&D 5e combat engine** — Grid combat with Dijkstra/A* pathfinding, Bresenham LoS, personality-driven monster AI, advantage/disadvantage dice.
10. **Production-grade degradation** — Every LLM integration point has a deterministic fallback. The system is designed to enhance with AI, not depend on it.

## License

MIT
