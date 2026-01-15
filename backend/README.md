# LLM 记忆系统后端

基于 Firestore 的中断驱动上下文系统，实现 LLM 的长期记忆管理。

## 功能特性

- **智能主题路由**: 使用 Embedding + LLM 两阶段路由策略
- **Artifact 管理**: Markdown 格式的知识文档自动构建和更新
- **上下文优化**: 根据对话主题动态加载相关历史记录
- **交叉主题支持**: 自动识别和处理跨主题对话

## 技术栈

- FastAPI (Web 框架)
- Google Cloud Firestore (数据存储)
- Gemini 2.5 Flash Lite (路由判断)
- Cloudflare Workers AI (向量嵌入)

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
│   │   ├── router_service.py
│   │   ├── artifact_service.py
│   │   └── llm_service.py
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

**GET /api/topics/{user_id}**

获取用户的所有主题。

**GET /api/topics/{user_id}/{thread_id}**

获取特定主题的详细信息。

## 核心概念

### Session (会话)
短期对话窗口，包含当前会话的所有消息。

### Topic Thread (主题线程)
长期知识积累单元，跨会话持久化。每个主题包含一个 Artifact 文档。

### Artifact (知识文档)
Markdown 格式的结构化知识文档，随对话自动更新，包含源消息索引。

### 路由策略
1. **粗筛**: 使用 Embedding 计算相似度，找到候选主题
2. **精判**: 使用 Flash-Lite 做最终路由决策

## 开发说明

### 添加新的 API 端点

1. 在 `app/routers/` 下创建新的路由文件
2. 在 `app/main.py` 中注册路由

### 扩展数据模型

1. 在 `app/models/` 下定义 Pydantic 模型
2. 在相应的 service 中实现数据库操作

## License

MIT
