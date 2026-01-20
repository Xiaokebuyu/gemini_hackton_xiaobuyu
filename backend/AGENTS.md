# Repository Guidelines

## 项目结构与模块组织
- `backend/app/` 是 FastAPI 应用入口；路由在 `backend/app/routers/`，业务逻辑在 `backend/app/services/`。
- 数据模型在 `backend/app/models/`，工具函数在 `backend/app/utils/`。
- 测试用例在仓库根目录的 `tests/`。
- 文档位于 `design2/` 和 `IMPLEMENTATION_SUMMARY.md`。

## 构建、测试与本地开发命令
- `pip install -r backend/requirements.txt` 安装运行依赖。
- `cd backend && uvicorn app.main:app --reload --port 8000` 本地启动 API（热重载）。
- `cd backend && ./run.sh` 进行环境检查并启动服务。
- `PYTHONPATH=backend pytest tests/ -v` 运行测试套件。

## 编码风格与命名规范
- Python 使用 4 空格缩进。
- 保持模块 docstring，并仅添加必要的简短注释。
- 命名规则：模块/函数/变量使用 `snake_case`，类与 Pydantic 模型使用 `PascalCase`。
- 未配置格式化或 lint 工具；请遵循既有风格并保持 import 整洁。

## 测试指南
- 测试框架为 `pytest` + `pytest-asyncio`；异步测试需 `@pytest.mark.asyncio`。
- 测试文件位于 `tests/`，命名建议为 `test_*.py`。
- 集成测试可能调用真实 Firestore 与外部 API；请使用测试凭证并隔离 `user_id`。

## 提交与 Pull Request 规范
- 提交信息遵循 Conventional Commits（如 `feat: add memory cleanup`、`fix: handle empty state`）。
- PR 需包含简短摘要、测试结果、配置变更说明（如适用）。
- 关联相关 issue；仅在涉及 UI 变更时附截图。

## 安全与配置提示
- 关键环境变量：`GEMINI_API_KEY`、`CLOUDFLARE_ACCOUNT_ID`、`CLOUDFLARE_API_TOKEN`、`GOOGLE_APPLICATION_CREDENTIALS`（见 `backend/app/config.py`）。
- 默认凭证路径为 `backend/firebase-credentials.json`；禁止提交任何密钥或凭证。
