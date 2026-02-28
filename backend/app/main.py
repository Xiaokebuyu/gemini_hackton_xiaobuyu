"""Game Core API entry point."""

from app.deps import app  # noqa: F401 — re-export for uvicorn & tests

from app.routers import sessions, character, panels, gameplay

app.include_router(sessions.router)
app.include_router(character.router)
app.include_router(panels.router)
app.include_router(gameplay.router)
