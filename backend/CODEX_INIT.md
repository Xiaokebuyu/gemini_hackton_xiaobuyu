# Codex Init

## Purpose

This document records the operating rules agreed during the current rebuild of the game system.
It is the practical execution baseline for future work in this repository.

## Primary Build Target

- The active system is `app/game_core`.
- New development should be implemented in `app/game_core` and the current API shell in `app/`.
- Do not rebuild on top of archived or legacy code.

## Legacy Policy

- `legacy/` may be consulted only for historical design ideas, naming references, or lessons learned.
- Do not import code from `legacy/`.
- Do not copy modules from `legacy/`.
- Do not create compatibility bridges for `legacy/`.
- If a legacy idea is worth keeping, re-implement it against current `game_core` contracts.

## Design Document Policy

- Design documents define the target architecture and target game experience.
- They are not to be followed blindly when a documented implementation detail is technically harmful.
- If a documented detail would increase coupling, break boundaries, or create high rework risk:
  - stop,
  - call it out explicitly,
  - propose a cleaner implementation,
  - preserve the intended gameplay outcome.
- Preserve gameplay intent first; adjust technical placement when needed.

## Priority Order

- Current priority is **completeness first**, not “playable first”.
- Prefer completing full lifecycle shape, subsystem boundaries, and external interface coverage before optimizing for immediate playability.
- Avoid fake completeness: placeholders are acceptable only when clearly marked as placeholders.

## World Data Policy

- `data/goblin_slayer/structured_new` is **not** currently a valid live world source for gameplay.
- Do not directly wire `structured_new` into live session creation, resume, or gameplay flow.
- Do not distort runtime contracts just to consume incomplete source data.
- First stabilize the consuming runtime and system interfaces.
- After the actual runtime data contract is stable, build or revise the extraction/compilation pipeline to match that contract.

## API Policy

- Prefer a thin API layer.
- Keep route handlers thin and direct; avoid extra service layers unless they clearly reduce coupling.
- The current API may expose:
  - real endpoints where the backend capability is already stable and world content is not required,
  - explicit placeholders where world content or runtime integration is not ready.
- Placeholder endpoints must be honest:
  - normal HTTP placeholders should return explicit service-unavailable semantics,
  - stream placeholders should use real SSE framing when the route is defined as a stream.

## Coupling Rules

- Keep module boundaries strict.
- Do not push session lifecycle rules into generic core containers when they belong in outer lifecycle adapters.
- Keep persistence semantics in persistence/session layers, not in core state abstractions.
- Keep world loading concerns separate from runtime orchestration.
- Keep low-level modules unaware of high-level workflow exceptions unless that exception is fundamental to the abstraction.

## State And Lifecycle Rules

- New session and restored session are both first-class lifecycle paths.
- `Scene` is session-ephemeral and should not be treated as long-term persisted gameplay state.
- Session-specific exceptions should remain in session lifecycle orchestration, not in generic state container behavior.

## Progress Strategy

- Do not continue along only one visible track (for example, only API work) until it becomes disproportionately complete.
- Periodically evaluate the system by subsystem:
  - content,
  - state,
  - rules,
  - orchestration,
  - narrative,
  - planning,
  - presentation,
  - application/session layer.
- Prefer closing the largest structural gaps over polishing one narrow lane.

## Validation Style

- Use smoke checks and targeted verification while the system is still being assembled.
- Do not add broad test scaffolding by default when the user has not asked for it.
- Keep verification proportional to the change.

## Implementation Style

- Favor explicit contracts over implied behavior.
- Favor reusable composition helpers over duplicated assembly logic.
- Favor stable shapes for payloads, responses, and contexts, even when the values are placeholders.
- When an interface is placeholder-only, mark it clearly and keep the future route shape stable.

## Near-Term Direction

- Continue completing the system as a complete architecture, not as a temporary demo.
- Do not treat incomplete world source data as production-ready input.
- When the consuming systems are sufficiently complete, define the actual runtime data contract and then adapt the extraction pipeline to produce exactly that shape.

## Current Status Snapshot

- The active rebuild remains centered on `app/game_core`.
- The default rules-layer handler set has now been brought to a stable extension-point baseline:
  - `CombatHandler`
  - `SkillCheckHandler`
  - `NavigationHandler`
  - `InventoryHandler`
  - `EconomyHandler`
  - `GrowthHandler`
  - `RestHandler`
  - `CrimeHandler`
  - `EncounterHandler`
  - `ContainerHandler`
  - `WorldStateHandler`
  - `StatusEffectHandler`
  - `SpellHandler`
- The default orchestration settlement hook chain has also been brought to a stable extension-point baseline:
  - `ScheduledEventHook`
  - `StatusEffectHook`
  - `AIOsirisHook`
  - `NarrativePlannerHook`
  - `EncounterHook`
  - `EventConditionHook`
  - `NpcScheduleHook`
  - `TimeAdvanceHook`
  - `GmNarrationHook`
  - `SceneBusResetHook`
- “Stable extension-point baseline” means:
  - default registration is safe,
  - the component no longer relies on generic `"not implemented"` as its main behavior,
  - input/output contracts are explicit and test-backed,
  - unsupported depth is expressed as controlled `noop` / `blocked` / `deferred`, not hidden stubs.

## Current Mainline

- The previous mainline was:
  - first bring all default `game_core` components to stable extension points,
  - then deepen the high-complexity gameplay semantics.
- That first stage is now effectively complete for the default rules handlers and default settlement hooks.
- The active mainline has therefore shifted to:
  - keep `app/game_core` as the only real implementation target,
  - preserve the now-stable subsystem boundaries,
  - deepen the highest-complexity systems incrementally without breaking those boundaries,
  - treat the interaction layer as a first-class architecture surface rather than route-local glue,
  - only after runtime contracts are genuinely stable, formalize and adapt the world-data extraction pipeline.
- In practical terms:
  - stop adding broad new placeholder surfaces,
  - keep replacing controlled `deferred` / reduced MVP behavior in the hardest systems with real gameplay logic,
  - keep moving runtime-aware behavior out of adapters and back into the correct application-layer boundaries,
  - continue using proportional, targeted tests instead of broad test scaffolding.

## Immediate Next Tasks

- The first structured interaction layer is now in place:
  - `action/stream` is the structured action entry,
  - `input/stream` supports minimal text-navigation aliases,
  - `interact/stream` supports the first complete NPC/board interaction matrix,
  - `FastAPIInputPort` is now constrained to pure normalization,
  - runtime-aware interaction presence, prechecks, and execution now live in application-layer interaction services.
- The current implementation focus is therefore no longer “make interaction work at all”, but:
  - keep the new interaction boundary stable and extension-friendly,
  - keep `main.py` as a thin route/SSE shell,
  - keep runtime-aware orchestration out of adapters,
  - extend new interaction behavior through the established seams instead of reintroducing route-local business logic.
- The immediate next work should therefore favor:
  - extending interaction through the current split:
    - `FastAPIInputPort` for pure normalization,
    - `app/interaction_service.py` for presence, prechecks, and execution,
    - `app/interaction_views.py` for new snapshot payloads,
  - treating the current NPC quest-intent matrix as the first read-oriented baseline and the board lifecycle as the sole task-write baseline,
  - shifting the next major completion work toward the remaining concrete adapter gaps:
    - `SSEPresentationPort`
    - `FastAPIOutputPort`
    - `FirestorePersistencePort`,
  - continuing to leave real world-data integration and extraction/compilation work until the runtime-facing contracts are stable.

## Working Placement

- `CODEX_INIT.md` should be treated as the top-level operational entrypoint:
  - current rules of engagement,
  - current stage,
  - current mainline,
  - immediate next work.
- Detailed per-subsystem progress should continue to live in:
  - [rules_engine.md](/home/xiaokebuyu/workplace/gemini-hackton/backend/app/施工记录（持续更新）/rules_engine.md)
  - [orchestration.md](/home/xiaokebuyu/workplace/gemini-hackton/backend/app/施工记录（持续更新）/orchestration.md)
  - [state_layer.md](/home/xiaokebuyu/workplace/gemini-hackton/backend/app/施工记录（持续更新）/state_layer.md)
  - [narrative.md](/home/xiaokebuyu/workplace/gemini-hackton/backend/app/施工记录（持续更新）/narrative.md)
  - [content_layer.md](/home/xiaokebuyu/workplace/gemini-hackton/backend/app/施工记录（持续更新）/content_layer.md)
  - [adapters.md](/home/xiaokebuyu/workplace/gemini-hackton/backend/app/施工记录（持续更新）/adapters.md)
- This is the preferred placement:
  - keep `CODEX_INIT.md` concise and decision-oriented,
  - keep subsystem specifics in the rolling construction logs,
  - avoid duplicating full progress detail in multiple top-level files.

## Carry-Forward Execution Lessons

- The most effective pattern so far has been:
  - first stabilize extension points,
  - then deepen semantics.
- In practice, this has reduced rework more than trying to “finish” one hard subsystem too early.

- When a local patch and a more architecture-aligned placement conflict, prefer the option that better matches the target subsystem boundary.
  - Do not automatically choose the smallest local edit if it keeps responsibility in the wrong layer.
  - A slightly larger change is preferred when it meaningfully moves behavior toward the intended architecture and does not create disproportionate risk.

- Prefer controlled boundary behavior over fake implementation.
  - A structured `noop`, `blocked`, `unsupported_*`, or `deferred` result is acceptable.
  - A generic `"not implemented"` on the main path is not.
  - The goal is to make unsupported depth explicit without lying about capability.

- Reuse existing stable state anchors before introducing new state models.
  - If an existing slice can carry the minimum truthful runtime fact cleanly, prefer that first.
  - Only introduce a new slice when the missing state is fundamental, not merely convenient.
  - Example from the current rebuild: minimum combat runtime was first anchored on `AreaSlice.hostile_tracking` instead of creating a premature `combat` slice.

- Close lifecycle loops before widening feature breadth.
  - A partial end-to-end loop is usually more valuable than several disconnected half-features.
  - Prefer “write -> settle -> transition -> observable result” over adding more stubs in adjacent subsystems.

- Use deterministic MVP behavior where deeper systems are not ready.
  - Prefer deterministic thresholds, deterministic rotation, passive-value checks, and controlled defaults
  - over early randomness, early simulation complexity, or premature model-driven behavior.
  - This keeps tests stable and makes later deepening easier to isolate.

- For high-uncertainty orchestration components, prefer:
  - injectable provider/evaluator/narrator interfaces,
  - plus a safe default local no-op implementation.
- This has worked well for hooks such as:
  - `AIOsirisHook`
  - `EventConditionHook`
  - `NpcScheduleHook`
  - `NarrativePlannerHook`
  - `GmNarrationHook`
- The hook boundary becomes stable before the underlying intelligence is real.

- When a handler becomes real, remember to update its outer integration points in the same pass.
  - Most commonly:
    - `DEFAULT_ACTION_COMMAND_TYPES`
    - focused tests
    - the rolling construction log
- This avoids a false “implemented but unreachable” state.

- Keep source and permission constraints at the rules boundary.
  - Do not assume upper layers will always send the correct source.
  - Enforce critical restrictions in the handler when the capability must be protected.
  - But avoid over-constraining normal player paths if the default dispatcher depends on them.

- Keep validation and compute paths explicit and parallel.
  - Validation should reject malformed or unsupported inputs clearly.
  - Compute should return stable metadata that callers can read mechanically.
  - Do not force outer layers to infer outcomes from prose or narrative hints.

- Verification should stay proportional and local-first.
  - Add focused tests for the changed component.
  - Then run the nearest neighboring regression set.
  - Avoid broad, unrelated suite expansion unless the change really crosses those boundaries.

- Use `CODEX_INIT.md` to preserve working strategy, not to duplicate subsystem detail.
  - Put reusable execution lessons here.
  - Put concrete subsystem contracts and per-component decisions in the rolling logs.
