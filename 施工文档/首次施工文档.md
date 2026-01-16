# LLM 记忆系统实现总结

## 项目概述

基于 Firestore 的中断驱动上下文系统，实现了 LLM 的长期记忆管理功能。

## 已实现功能

### ✅ 阶段一：项目基础设施

- [x] 项目目录结构搭建
- [x] 依赖包配置（requirements.txt）
- [x] 环境配置管理（config.py）
- [x] 数据模型定义（Session, Topic, Message）
- [x] FastAPI 应用入口（main.py）

### ✅ 阶段二：Firestore 基础服务

- [x] Session CRUD 操作
  - 创建会话
  - 获取会话
  - 更新会话当前主题
  
- [x] Message CRUD 操作
  - 添加消息
  - 按会话获取消息
  - 按主题获取消息
  - 按消息ID获取单条消息
  
- [x] Topic CRUD 操作
  - 创建主题
  - 获取主题
  - 获取所有主题
  - 更新 Artifact
  - 更新 Embedding
  
- [x] Artifact 版本控制
  - 保存版本
  - 获取版本列表

### ✅ 阶段三：路由系统

- [x] 两阶段路由策略
  - Embedding 粗筛（Cloudflare Workers AI）
  - Flash-Lite 精判（Gemini 2.0 Flash）
  
- [x] 路由决策执行
  - 路由到现有主题
  - 创建新主题
  - 创建交叉主题
  
- [x] Embedding 服务
  - Cloudflare Embedding API 集成
  - 余弦相似度计算
  - 批量相似度计算

### ✅ 阶段四：Artifact 管理

- [x] Artifact 解析
  - 源索引解析（`<!-- sources: ... -->`）
  - 章节内容提取
  - Markdown 结构解析
  
- [x] Artifact 更新
  - LLM 判断是否需要更新
  - 自动添加源索引
  - 版本历史保存
  
- [x] 上下文构建
  - 从 Artifact 加载相关章节
  - 加载源消息详情
  - 构建完整上下文

### ✅ 阶段五：上下文构建与对话流程

- [x] ContextBuilder 服务
  - 系统角色定义
  - Artifact 上下文构建
  - 最近对话历史构建
  - 对话历史格式化
  
- [x] LLM 服务
  - 路由决策
  - 相关章节查找
  - Artifact 更新判断
  - Artifact 内容更新
  - 对话响应生成

### ✅ 阶段六：API 接口

- [x] 聊天 API（/api/chat）
  - 完整对话流程
  - 自动路由
  - 上下文加载
  - Artifact 更新
  - 消息持久化
  
- [x] 会话管理 API
  - 创建会话
  - 获取会话消息
  
- [x] 主题管理 API
  - 获取用户所有主题
  - 获取主题详情
  - 获取主题 Artifact
  - 获取 Artifact 版本历史

### ✅ 阶段七：测试

- [x] 单元测试
  - 路由系统测试
  - Artifact 解析测试
  - 相似度计算测试
  
- [x] 集成测试
  - 完整对话流程测试
  - Artifact 更新流程测试
  - 消息存储检索测试
  
- [x] 测试文档
  - 测试说明
  - 运行指南
  - 故障排查

## 项目结构

```
gemini-hackton/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                    # FastAPI 入口
│   │   ├── config.py                  # 配置管理
│   │   ├── models/                    # 数据模型
│   │   │   ├── session.py
│   │   │   ├── topic.py
│   │   │   └── message.py
│   │   ├── services/                  # 业务逻辑
│   │   │   ├── firestore_service.py   # Firestore 操作
│   │   │   ├── router_service.py      # 路由服务
│   │   │   ├── artifact_service.py    # Artifact 管理
│   │   │   ├── context_builder.py     # 上下文构建
│   │   │   └── llm_service.py         # LLM 调用
│   │   ├── routers/                   # API 路由
│   │   │   ├── chat.py                # 聊天接口
│   │   │   └── topics.py              # 主题管理
│   │   └── utils/                     # 工具函数
│   │       └── embedding.py           # Embedding 工具
│   ├── requirements.txt               # 依赖包
│   ├── README.md                      # 项目文档
│   └── run.sh                         # 启动脚本
├── tests/                             # 测试文件
│   ├── test_router.py
│   ├── test_artifact.py
│   ├── test_integration.py
│   └── README.md
├── design2                            # 设计文档
├── .gitignore
└── IMPLEMENTATION_SUMMARY.md          # 本文档
```

## 核心技术

### 数据存储
- **Firestore**: NoSQL 文档数据库，存储会话、消息、主题

### LLM 服务
- **Gemini 2.0 Flash**: 路由判断、Artifact 更新、对话生成
- **Cloudflare Workers AI**: 文本向量化

### 后端框架
- **FastAPI**: 高性能 Web 框架
- **Pydantic**: 数据验证
- **Google Cloud SDK**: Firestore 客户端

## API 端点

### 聊天接口

**POST /api/chat**
```json
{
  "user_id": "user_123",
  "session_id": "sess_001",
  "message": "Python 的列表推导式怎么用?"
}
```

响应：
```json
{
  "session_id": "sess_001",
  "thread_id": "thread_abc123",
  "thread_title": "Python编程",
  "response": "列表推导式的基本语法是...",
  "is_new_topic": false,
  "artifact_updated": true
}
```

### 主题管理

**GET /api/topics/{user_id}**
- 获取用户的所有主题

**GET /api/topics/{user_id}/{thread_id}**
- 获取主题详细信息

**GET /api/topics/{user_id}/{thread_id}/artifact**
- 获取主题的 Artifact 内容

**GET /api/topics/{user_id}/{thread_id}/versions**
- 获取 Artifact 历史版本

## 工作流程

### 对话处理流程

```
用户消息
    ↓
创建/获取会话
    ↓
路由决策 ← Embedding 粗筛
    ↓      ← LLM 精判
确定主题
    ↓
加载 Artifact ← 找相关章节
    ↓          ← 加载源消息
构建上下文
    ↓
LLM 生成回复
    ↓
保存消息
    ↓
Artifact 更新判断 ← 是否有新知识
    ↓
更新 Artifact（如需要）
    ↓
返回响应
```

### 路由策略

```
用户输入
    ↓
生成 Embedding
    ↓
计算与现有主题的相似度
    ↓
获取 Top-3 候选主题
    ↓
相似度 > 0.85? ──是──→ 直接路由
    ↓ 否
LLM 精判
    ↓
├─ 路由到现有主题
├─ 创建新主题
└─ 创建交叉主题
```

## 快速开始

### 1. 安装依赖

```bash
cd backend
pip install -r requirements.txt
```

### 2. 配置环境

创建 `.env` 文件：

```bash
GOOGLE_APPLICATION_CREDENTIALS=./firebase-credentials.json
GEMINI_API_KEY=your_gemini_api_key
CLOUDFLARE_ACCOUNT_ID=your_account_id
CLOUDFLARE_API_TOKEN=your_api_token
```

### 3. 启动服务

```bash
# 方式1: 使用启动脚本
chmod +x run.sh
./run.sh

# 方式2: 直接运行
uvicorn app.main:app --reload --port 8000
```

### 4. 访问 API 文档

```
http://localhost:8000/docs
```

## 测试

### 运行所有测试

```bash
cd backend
pytest tests/ -v
```

### 运行特定测试

```bash
pytest tests/test_router.py -v
pytest tests/test_artifact.py -v
pytest tests/test_integration.py -v
```

## 设计亮点

### 1. 中断驱动的上下文系统
- 用户消息作为"中断请求"
- 主题作为"进程"，拥有独立上下文
- 后端作为"调度器"，负责上下文切换

### 2. 两阶段路由策略
- **粗筛阶段**: Embedding 快速计算相似度
- **精判阶段**: LLM 理解语义做最终决策
- 平衡性能和准确性

### 3. Artifact 知识管理
- Markdown 格式，人类可读
- 嵌入式索引（`<!-- sources: ... -->`）
- 版本控制，可追溯
- 按需加载，节省 Token

### 4. 智能上下文构建
- 骨架：Artifact 提供结构化知识
- 血肉：源消息提供详细对话
- 动态加载相关章节，避免信息过载

## 技术决策

### ✅ 已确定的设计决策

- ✅ **Topic 粒度**: 宽泛的大类（"Python"级别）
- ✅ **Topic 路由**: Embedding + Flash-Lite 两阶段
- ✅ **Artifact 结构**: Markdown + 嵌入索引
- ✅ **Artifact 更新**: 主模型同步判断
- ✅ **交叉 Topic**: 创建父 Topic 聚合
- ✅ **消息查询**: `thread_id` 字段 + 索引
- ✅ **Embedding 存储**: Firestore 直接存储
- ✅ **Sessions 关系**: 会话窗口 + 长期记忆分离

### 📝 暂不实现的功能

- Token 管理与限制
- BigQuery 冷热分离
- 前端界面
- 用户认证系统
- 多用户并发处理
- 向量数据库优化

## 性能考虑

### Embedding 相似度阈值
- **初始值**: 0.85
- **调优策略**: 记录路由决策，根据数据调整

### 消息查询限制
- 默认最多返回 50 条历史消息
- 可根据需求调整

### Artifact 更新频率
- 仅在有新知识时更新
- 使用轻量 prompt 判断

## 扩展建议

### 短期优化
1. 添加缓存层（Redis）缓存热点主题
2. 实现 Artifact 差量更新
3. 添加用户认证和权限控制
4. 实现流式响应（Server-Sent Events）

### 长期规划
1. BigQuery 集成做长期分析
2. 向量数据库（Pinecone/Qdrant）优化检索
3. 多模态支持（图片、文件）
4. 前端界面开发
5. 分布式部署和负载均衡

## 贡献指南

### 添加新功能
1. 在 `app/services/` 创建服务类
2. 在 `app/routers/` 添加 API 路由
3. 更新 `app/main.py` 注册路由
4. 编写测试用例

### 代码规范
- 使用 Type Hints
- 编写 docstring
- 遵循 PEP 8 规范
- 添加单元测试

## 许可证

MIT

## 联系方式

项目地址: /home/xiaokebuyu/workplace/gemini-hackton

---

**实现完成时间**: 2026年1月14日
**实现状态**: ✅ 所有核心功能已完成
**下一步**: 部署测试和性能优化
