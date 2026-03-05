"""Game Core API entry point."""

from fastapi.staticfiles import StaticFiles

from app.deps import app  # noqa: F401 — re-export for uvicorn & tests
from app.routers import sessions, character, panels, gameplay, combat, images
from app.routers.images import CACHE_DIR

app.include_router(sessions.router)
app.include_router(character.router)
app.include_router(panels.router)
app.include_router(gameplay.router)
app.include_router(combat.router)
app.include_router(images.router)

CACHE_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static/images", StaticFiles(directory=str(CACHE_DIR)), name="static_images")
