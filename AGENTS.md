# Repository Guidelines

本仓库是基于 FastAPI 的 LLM 记忆系统后端，使用 Firestore 持久化并配套 pytest 测试。

## Project Structure & Module Organization
- `backend/` 为服务端代码入口，核心实现位于 `backend/app/`。
- `backend/app/routers/` 定义 HTTP 接口；`backend/app/services/` 为业务逻辑（路由、Artifact、Firestore）。
- `backend/app/models/` 与 `backend/app/utils/` 分别放置数据模型与工具函数。
- `tests/`（仓库根目录）为测试用例；`design2/` 与 `IMPLEMENTATION_SUMMARY.md` 为文档。

## Build, Test, and Development Commands
- `pip install -r backend/requirements.txt` 安装运行依赖。
- `cd backend && uvicorn app.main:app --reload --port 8000` 本地启动 API。
- `cd backend && ./run.sh` 进行环境检查并启动服务。

## Coding Style & Naming Conventions
- Python 使用 4 空格缩进；保持模块 docstring 与必要的简短注释。
- 命名规则：模块/函数/变量使用 `snake_case`，类与 Pydantic 模型使用 `PascalCase`。
- 当前未配置格式化或 lint 工具，请遵循既有风格并保持 import 整洁。

## Testing Guidelines
- 测试框架为 `pytest` + `pytest-asyncio`，异步测试需 `@pytest.mark.asyncio`。
- 测试目录在 `tests/`。示例：`PYTHONPATH=backend pytest tests/ -v`。
- 集成测试会调用真实 Firestore 与外部 API，请使用测试凭证并隔离 `user_id`。

## Commit & Pull Request Guidelines
- 提交信息遵循 Conventional Commit（如 `feat: ...`），建议 `type: 简短说明`（`feat`/`fix`/`docs`/`chore`）。
- PR 需包含简短摘要、测试结果、配置变更说明，并在适用时关联 issue。

## Security & Configuration Tips
- 关键环境变量：`GEMINI_API_KEY`、`CLOUDFLARE_ACCOUNT_ID`、`CLOUDFLARE_API_TOKEN`、`GOOGLE_APPLICATION_CREDENTIALS`（见 `backend/app/config.py`）。
- 默认凭证路径为 `backend/firebase-credentials.json`，禁止提交任何密钥或凭证。
