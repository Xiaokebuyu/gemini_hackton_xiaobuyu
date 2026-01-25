# Repository Guidelines

## Project Structure & Module Organization
- `app/` contains the FastAPI application. Core areas: `routers/` for HTTP endpoints, `services/` for business logic, `models/` for Pydantic data models, `mcp/` for memory gateway components, and `utils/` for shared helpers.
- `tests/` holds pytest tests (currently focused on graph/spreading activation).
- `examples/` and `design2/` contain supporting materials and experiments.
- Documentation lives in `README.md`, `ARCHITECTURE.md`, and `MEMORY_GATEWAY_INTEGRATION.md`.
- Runtime config and entry points: `.env`, `firebase-credentials.json`, `requirements.txt`, `run.sh`, `run_mcp_server.py`.

## Build, Test, and Development Commands
- `pip install -r requirements.txt` — install Python dependencies.
- `uvicorn app.main:app --reload --port 8000` — run the API locally.
- `./run.sh` — quick-start script that validates env/config and launches the server.
- `pytest -v` or `pytest tests/test_spreading_activation.py -v` — run tests.

## Coding Style & Naming Conventions
- Python with 4‑space indentation and module-level docstrings (see `app/` modules).
- Use Pydantic models for typed data structures in `app/models`.
- Prefer descriptive, lowercase module names (e.g., `spreading_activation.py`) and route modules in `app/routers`.
- No formatter/linter config is present; keep changes PEP 8–friendly and consistent with surrounding code.

## Testing Guidelines
- Framework: pytest.
- Name tests `test_*.py` and test functions `test_*` (see `tests/test_spreading_activation.py`).
- Add focused unit tests for new services or algorithms; integration tests may require Firestore/Gemini credentials.

## Commit & Pull Request Guidelines
- Commit messages in history are short, often Chinese, with occasional Conventional Commit prefixes (e.g., `feat:`). Match the existing style and keep messages concise.
- No formal PR template in the repo; include a brief summary, testing performed, and any config/credential notes.

## Security & Configuration Tips
- Ensure `.env` includes `GEMINI_API_KEY` and Firebase credentials path; `run.sh` validates these.
- Avoid committing secrets; keep `firebase-credentials.json` and `.env` local.
