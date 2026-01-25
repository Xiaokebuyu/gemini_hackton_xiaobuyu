# LLM 记忆系统后端

> 说明：旧的 MCP 记忆网关已隔离到 `app/legacy_mcp`，默认不再挂载到主 API。

基于 Firestore 的滑动窗口归档系统，实现 LLM 的长期记忆管理。

## 功能特性

- **Session 内归档**: 会话内滑动窗口归档，控制上下文长度
- **Artifact 管理**: Markdown 知识文档自动生成与合并
- **上下文重构**: LLM 使用 [NEED_CONTEXT] 触发按需历史回溯
- **可控上下文**: 活跃窗口 + Artifact 汇总，避免无限膨胀

## 技术栈

- FastAPI (Web 框架)
- Google Cloud Firestore (数据存储)
- Gemini (对话与归档分析)
- Cloudflare Workers AI (向量嵌入，预留)

## 快速开始

### 1. 安装依赖

```bash
cd backend
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填入实际的 API 密钥：

```bash
cp .env.example .env
```

### 3. 配置 Firebase

将 Firebase 服务账号凭证文件放在 `backend/` 目录下，命名为 `firebase-credentials.json`。

### 4. 运行服务

```bash
uvicorn app.main:app --reload --port 8000
```

服务将在 http://localhost:8000 启动。

API 文档可访问: http://localhost:8000/docs

## 项目结构

```
backend/
├── app/
│   ├── main.py              # FastAPI 应用入口
│   ├── config.py            # 配置管理
│   ├── models/              # 数据模型
│   │   ├── session.py
│   │   ├── topic.py
│   │   └── message.py
│   ├── services/            # 业务逻辑
│   │   ├── firestore_service.py
│   │   ├── artifact_service.py
│   │   ├── llm_service.py
│   │   ├── context_builder.py
│   │   ├── archive_service.py
│   │   └── context_loop.py
│   ├── routers/             # API 路由
│   │   ├── chat.py
│   │   └── topics.py
│   └── utils/               # 工具函数
│       └── embedding.py
├── requirements.txt
└── .env
```

## API 端点

### 聊天接口

**POST /api/chat**

发送消息并获取 AI 回复。

请求体:
```json
{
  "user_id": "user_123",
  "session_id": "sess_001",
  "message": "Python 的列表推导式怎么用?"
}
```

### 主题管理

**GET /api/sessions/{user_id}/{session_id}/topics**

获取会话内所有主题。

**GET /api/sessions/{user_id}/{session_id}/topics/{thread_id}**

获取特定主题的详细信息。

## 核心概念

### Session (会话)
短期对话窗口，包含当前会话的所有消息与主题。

### Topic (主题)
会话内的知识单元，每个主题包含一个 Artifact 文档。

### Artifact (知识文档)
Markdown 格式的结构化知识文档，归档时生成，包含源消息索引。

### 上下文策略
1. **滑动窗口**: 最近 N 条未归档消息
2. **归档摘要**: 通过 Artifact 汇总历史
3. **按需回溯**: [NEED_CONTEXT] 触发加载原始消息

## 开发说明

### 添加新的 API 端点

1. 在 `app/routers/` 下创建新的路由文件
2. 在 `app/main.py` 中注册路由

### 扩展数据模型

1. 在 `app/models/` 下定义 Pydantic 模型
2. 在相应的 service 中实现数据库操作

## License

MIT
