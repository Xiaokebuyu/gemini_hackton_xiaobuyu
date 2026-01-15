# 测试说明

## 测试文件

### test_router.py
路由系统测试，包括：
- 首次对话创建新主题
- 继续相同话题路由到现有主题
- Embedding 相似度计算

### test_artifact.py
Artifact 管理测试，包括：
- 解析 Artifact 源索引
- 提取章节内容
- Markdown 结构解析

### test_integration.py
端到端集成测试，包括：
- 完整对话流程
- Artifact 更新流程
- 消息存储和检索

## 运行测试

### 前提条件

1. 安装测试依赖：
```bash
pip install pytest pytest-asyncio
```

2. 配置环境变量（创建 `.env` 文件）：
```bash
GOOGLE_APPLICATION_CREDENTIALS=./firebase-credentials.json
GEMINI_API_KEY=your_gemini_api_key
CLOUDFLARE_ACCOUNT_ID=your_account_id
CLOUDFLARE_API_TOKEN=your_api_token
```

3. 确保 Firebase 凭证文件存在

### 运行所有测试

```bash
cd backend
pytest tests/ -v
```

### 运行特定测试文件

```bash
# 路由测试
pytest tests/test_router.py -v

# Artifact 测试
pytest tests/test_artifact.py -v

# 集成测试
pytest tests/test_integration.py -v
```

### 运行特定测试用例

```bash
pytest tests/test_artifact.py::TestArtifactService::test_parse_artifact_sources -v
```

### 查看详细输出

```bash
pytest tests/ -v -s
```

## 测试覆盖范围

### 单元测试
- ✓ Embedding 相似度计算
- ✓ Artifact 源索引解析
- ✓ 章节内容提取
- ✓ Markdown 结构解析

### 集成测试
- ✓ 完整对话流程
- ✓ 路由决策
- ✓ 消息持久化
- ✓ Artifact 更新
- ✓ 上下文构建

### 端到端测试
- ✓ 会话创建
- ✓ 消息路由
- ✓ 主题管理
- ✓ 数据持久化

## 注意事项

1. **API 配额**: 集成测试会调用实际的 API（Gemini、Cloudflare），请注意 API 配额
2. **Firebase 数据**: 测试会在 Firestore 中创建真实数据，建议使用测试环境
3. **异步测试**: 所有涉及 I/O 的测试都使用 `pytest-asyncio` 标记为异步
4. **测试隔离**: 每个测试使用不同的 user_id 来避免数据污染

## 手动测试

除了自动化测试，还可以使用 FastAPI 的交互式文档进行手动测试：

1. 启动服务：
```bash
cd backend
uvicorn app.main:app --reload
```

2. 访问 API 文档：
```
http://localhost:8000/docs
```

3. 测试流程：
   - 创建会话：POST /api/sessions/{user_id}/create
   - 发送消息：POST /api/chat
   - 查看主题：GET /api/topics/{user_id}
   - 查看 Artifact：GET /api/topics/{user_id}/{thread_id}/artifact

## 故障排查

### 测试失败常见原因

1. **Firebase 连接失败**
   - 检查凭证文件路径
   - 确认 Firebase 项目配置正确

2. **API 调用失败**
   - 检查 API 密钥是否有效
   - 确认网络连接正常

3. **导入错误**
   - 确保在 backend 目录下运行
   - 检查 Python 路径设置

### 调试建议

```bash
# 运行测试并显示详细日志
pytest tests/ -v -s --tb=short

# 只运行失败的测试
pytest tests/ --lf

# 进入调试模式
pytest tests/ --pdb
```
