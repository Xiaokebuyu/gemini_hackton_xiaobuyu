# LLM 热记忆系统重构 - 施工文档

## 一、重构目标

将 **跨 Session 的主题路由系统** 改为 **Session 内的滑动窗口归档系统**，解决对话过长导致的注意力下降问题。

### 核心变化

| 维度 | 重构前 | 重构后 |
|------|--------|--------|
| Topic 生命周期 | 跨 Session 永久存在 | 限于 Session 内 |
| 主题识别时机 | 每条消息实时路由 | 累积后批量归档分析 |
| 上下文管理 | 可能无限增长 | 固定窗口 + Artifact 压缩 |
| 历史加载方式 | 预先加载相关章节 | 主模型通过标记主动请求 |
| Embedding | 用于实时路由 | 暂不使用，保留给 BigQuery |

---

## 二、系统架构

### 2.1 新数据结构

```
users/{user_id}/
└── sessions/{session_id}/
    ├── created_at
    ├── updated_at
    │
    ├── messages/{message_id}/
    │   ├── role              # user / assistant
    │   ├── content
    │   ├── timestamp
    │   ├── is_excluded       # 是否被用户排除
    │   ├── is_archived       # 【新增】是否已归档
    │   └── thread_id         # 归档后填充，关联到 Topic
    │
    └── topics/{thread_id}/   # 【改动】从 User 级别移到 Session 级别
        ├── title
        ├── summary           # 【新增】简短摘要，用于合并判断
        ├── current_artifact
        ├── created_at
        └── artifact_versions/{version_id}/
            ├── content
            ├── created_at
            └── message_ids
```

### 2.2 滑动窗口机制

```
消息流（按时间）:
[1][2][3]...[20][21][22]...[39][40][41]...

                    ↓ 达到 40 条

[已归档区]        [待处理区]      [活跃区]
  空               1-20           21-40

                    ↓ 触发归档

[已归档区]        [待处理区]      [活跃区]
  1-20              空            21-40
    ↓
 生成 Artifact
 标记 is_archived=true
 关联 thread_id
```

### 2.3 上下文重构机制

```
用户消息
    ↓
检查消息数 >= archive_threshold?
    ├─ 是 → 执行归档 → 生成/合并 Artifact
    └─ 否 → 跳过
    ↓
构建上下文:
  ├── System Prompt（含 [NEED_CONTEXT] 使用说明）
  ├── Session 内所有 Artifact
  └── 活跃窗口消息（最新 N 条未归档消息）
    ↓
主模型生成回复
    ↓
解析回复，检测 [NEED_CONTEXT: xxx] 标记
    ├─ 有标记 → 查找匹配的 Artifact Section
    │            → 提取 sources 中的 message_ids
    │            → 加载原始消息
    │            → 注入上下文
    │            → 重新生成（最多 N 次）
    └─ 无标记 → 返回回复
    ↓
保存助手消息
    ↓
返回给用户
```

---

## 三、文件清单

### 3.1 目录结构（重构后）

```
backend/
├── app/
│   ├── __init__.py                    # 不变
│   ├── main.py                        # 不变
│   ├── config.py                      # 【修改】
│   │
│   ├── models/
│   │   ├── __init__.py                # 【修改】
│   │   ├── session.py                 # 【修改】
│   │   ├── topic.py                   # 【修改】
│   │   └── message.py                 # 【修改】
│   │
│   ├── services/
│   │   ├── __init__.py                # 【修改】
│   │   ├── firestore_service.py       # 【修改】
│   │   ├── llm_service.py             # 【修改】
│   │   ├── artifact_service.py        # 【修改】
│   │   ├── context_builder.py         # 【重写】
│   │   ├── archive_service.py         # 【新增】
│   │   ├── context_loop.py            # 【新增】
│   │   └── router_service.py          # 【保留但禁用】移到 _deprecated/
│   │
│   ├── routers/
│   │   ├── __init__.py                # 不变
│   │   ├── chat.py                    # 【重写】
│   │   └── topics.py                  # 【修改】
│   │
│   └── utils/
│       ├── __init__.py                # 【修改】
│       └── embedding.py               # 【保留】BigQuery 预留
│
├── tests/
│   ├── __init__.py                    # 不变
│   ├── README.md                      # 【修改】
│   ├── test_artifact.py               # 【修改】
│   ├── test_archive.py                # 【新增】
│   ├── test_context_loop.py           # 【新增】
│   ├── test_integration.py            # 【重写】
│   └── _deprecated/
│       └── test_router.py             # 【移动】
│
├── requirements.txt                   # 【修改】
├── run.sh                             # 不变
└── README.md                          # 【修改】
```

### 3.2 改动分类汇总

| 类型 | 文件 |
|------|------|
| **新增** | `archive_service.py`, `context_loop.py`, `test_archive.py`, `test_context_loop.py` |
| **重写** | `context_builder.py`, `chat.py`, `test_integration.py` |
| **修改** | `config.py`, `models/*`, `firestore_service.py`, `llm_service.py`, `artifact_service.py`, `topics.py`, `utils/__init__.py` |
| **保留禁用** | `router_service.py`, `embedding.py`, `test_router.py` |
| **不变** | `main.py`, `run.sh`, `routers/__init__.py`, `app/__init__.py` |

---

## 四、各文件详细设计

### 4.1 `config.py`

```python
"""配置管理模块"""

class Settings(BaseModel):
    
    # === Firebase 配置 ===
    google_application_credentials: str = ...
    firestore_database: str = ...
    
    # === Gemini API 配置 ===
    gemini_api_key: str = ...
    gemini_flash_model: str = "gemini-2.0-flash-exp"
    gemini_main_model: str = "gemini-2.0-flash-exp"
    
    # === 热记忆配置（当前使用）===
    active_window_size: int = 20           # 活跃窗口大小
    archive_threshold: int = 40            # 触发归档的消息总数
    max_context_retry: int = 3             # 上下文重构最大重试次数
    context_request_pattern: str = r'\[NEED_CONTEXT:\s*(.+?)\]'
    
    # === 冷记忆配置（BigQuery 阶段启用）===
    # cloudflare_account_id: str = ...
    # cloudflare_api_token: str = ...
    # cloudflare_embedding_model: str = ...
    # embedding_threshold: float = 0.85
    # max_candidate_topics: int = 3
    
    # === API 配置 ===
    api_prefix: str = "/api"
    cors_origins: list = ["*"]
```

### 4.2 `models/message.py`

```python
"""消息数据模型"""

class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class MessageBase(BaseModel):
    role: MessageRole
    content: str
    thread_id: Optional[str] = None   # 归档后填充
    is_excluded: bool = False
    is_archived: bool = False         # 【新增】


class MessageCreate(MessageBase):
    pass


class Message(MessageBase):
    message_id: str
    timestamp: datetime = Field(default_factory=datetime.now)
```

### 4.3 `models/topic.py`

```python
"""主题数据模型"""

class TopicBase(BaseModel):
    title: str
    summary: str = ""                 # 【新增】简短摘要
    current_artifact: str = ""


class TopicCreate(TopicBase):
    pass


class TopicThread(TopicBase):
    thread_id: str
    created_at: datetime = Field(default_factory=datetime.now)
    
    # 【删除】以下字段不再需要
    # summary_embedding: Optional[List[float]] = None
    # parent_thread_ids: List[str] = []
    # child_thread_ids: List[str] = []
    # last_active_at: datetime


class ArtifactVersion(BaseModel):
    version_id: str
    content: str
    created_at: datetime = Field(default_factory=datetime.now)
    message_ids: List[str] = Field(default_factory=list)
```

### 4.4 `models/session.py`

```python
"""会话数据模型"""

class SessionBase(BaseModel):
    pass
    # 【删除】current_thread_id 不再需要


class SessionCreate(SessionBase):
    pass


class Session(SessionBase):
    session_id: str
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
```

### 4.5 `services/firestore_service.py`

```python
"""Firestore 数据库服务"""

class FirestoreService:
    
    # ==================== Session 操作 ====================
    
    async def create_session(self, user_id: str) -> Session:
        """创建新会话"""
    
    async def get_session(self, user_id: str, session_id: str) -> Optional[Session]:
        """获取会话"""
    
    async def update_session_timestamp(self, user_id: str, session_id: str) -> None:
        """更新会话时间戳"""
    
    # ==================== Message 操作 ====================
    
    async def add_message(self, user_id: str, session_id: str, message: MessageCreate) -> str:
        """添加消息"""
    
    async def get_active_messages(self, user_id: str, session_id: str, limit: int) -> List[Message]:
        """【新增】获取未归档的消息（按时间正序）"""
    
    async def count_active_messages(self, user_id: str, session_id: str) -> int:
        """【新增】统计未归档消息数量"""
    
    async def get_pending_archive_messages(
        self, user_id: str, session_id: str,
        active_window_size: int
    ) -> List[Message]:
        """【新增】获取待归档的消息（活跃窗口之外的未归档消息）"""
    
    async def mark_messages_archived(
        self, user_id: str, session_id: str,
        message_ids: List[str], thread_id: str
    ) -> None:
        """【新增】批量标记消息为已归档"""
    
    async def get_messages_by_ids(
        self, user_id: str, session_id: str, message_ids: List[str]
    ) -> List[Message]:
        """【新增】根据 ID 列表批量获取消息"""
    
    # 【保留】以下方法
    async def get_messages_by_session(self, ...) -> List[Message]: ...
    async def get_message_by_id(self, ...) -> Optional[Message]: ...
    
    # 【删除】以下方法
    # async def get_messages_by_thread(self, ...) -> List[Message]:
    #     不再需要，改用 get_messages_by_ids
    
    # ==================== Topic 操作（路径调整）====================
    
    async def create_topic(self, user_id: str, session_id: str, topic: TopicCreate) -> str:
        """创建主题 - 【改动】路径改为 sessions/{session_id}/topics/"""
    
    async def get_topic(self, user_id: str, session_id: str, thread_id: str) -> Optional[TopicThread]:
        """获取主题 - 【改动】需要 session_id"""
    
    async def get_all_topics(self, user_id: str, session_id: str) -> List[TopicThread]:
        """获取所有主题 - 【改动】需要 session_id"""
    
    async def update_topic_artifact(
        self, user_id: str, session_id: str, thread_id: str, artifact: str
    ) -> None:
        """更新 Artifact - 【改动】需要 session_id"""
    
    async def update_topic_summary(
        self, user_id: str, session_id: str, thread_id: str, summary: str
    ) -> None:
        """【新增】更新主题摘要"""
    
    # 【删除】以下方法
    # async def update_topic_embedding(self, ...): 不再需要
    
    # ==================== Artifact Version 操作（路径调整）====================
    
    async def save_artifact_version(
        self, user_id: str, session_id: str, thread_id: str,
        artifact: str, message_ids: List[str]
    ) -> str:
        """保存版本 - 【改动】需要 session_id"""
    
    async def get_artifact_versions(
        self, user_id: str, session_id: str, thread_id: str, limit: int
    ) -> List[ArtifactVersion]:
        """获取版本列表 - 【改动】需要 session_id"""
```

### 4.6 `services/archive_service.py`（新增）

```python
"""归档服务 - 负责消息归档和 Artifact 创建"""

class ArchiveService:
    
    def __init__(self):
        self.firestore = FirestoreService()
        self.llm = LLMService()
        self.artifact = ArtifactService()
    
    async def check_should_archive(self, user_id: str, session_id: str) -> bool:
        """
        检查是否需要归档
        条件：未归档消息数 >= archive_threshold
        """
    
    async def execute_archive(self, user_id: str, session_id: str) -> Optional[str]:
        """
        执行归档流程
        
        步骤:
        1. 获取待归档消息（超出活跃窗口的部分）
        2. 调用子代理分析消息，生成主题和 Artifact
        3. 查找是否有可合并的现有 Topic
        4. 创建新 Topic 或合并到现有 Topic
        5. 保存 Artifact 版本
        6. 标记消息为已归档
        
        返回: thread_id（新建或合并的）
        """
    
    async def analyze_messages(self, messages: List[Message]) -> Dict[str, Any]:
        """
        子代理分析消息块
        
        返回:
        {
            "title": "主题标题（宽泛大类）",
            "summary": "简短摘要（用于合并判断）",
            "artifact": "Markdown 内容（带 <!-- sources: --> 索引）"
        }
        """
    
    async def find_mergeable_topic(
        self, user_id: str, session_id: str,
        new_title: str, new_summary: str
    ) -> Optional[TopicThread]:
        """
        查找可合并的现有 Topic
        
        策略：
        1. 遍历 Session 内所有 Topic
        2. 用 LLM 判断标题和摘要是否属于同一主题
        3. 返回匹配的 Topic 或 None
        """
    
    async def merge_into_topic(
        self, user_id: str, session_id: str, thread_id: str,
        new_artifact: str, message_ids: List[str]
    ) -> None:
        """
        合并新内容到现有 Topic
        
        步骤:
        1. 获取现有 Artifact
        2. 调用 LLM 合并两个 Artifact
        3. 更新 Topic
        4. 保存版本
        """
```

### 4.7 `services/context_loop.py`（新增）

```python
"""上下文循环 - 处理动态上下文重构"""

class ContextLoop:
    
    def __init__(self):
        self.firestore = FirestoreService()
        self.llm = LLMService()
        self.artifact = ArtifactService()
        self.context_builder = ContextBuilder()
    
    async def run(
        self, user_id: str, session_id: str, user_query: str
    ) -> Tuple[str, int]:
        """
        运行上下文循环
        
        返回: (最终回复, 重构次数)
        """
        loaded_messages: List[Message] = []
        retry_count = 0
        
        for attempt in range(settings.max_context_retry):
            # 1. 构建上下文
            context = await self.context_builder.build(
                user_id, session_id, loaded_messages
            )
            
            # 2. 生成回复
            response = await self.llm.generate_response(context, user_query)
            
            # 3. 检查标记
            request_key = self.parse_context_request(response)
            
            if not request_key:
                return response, retry_count
            
            # 4. 加载历史
            messages = await self.resolve_context_request(
                user_id, session_id, request_key
            )
            
            if not messages:
                return self.strip_context_request(response), retry_count
            
            loaded_messages.extend(messages)
            retry_count += 1
        
        return self.strip_context_request(response), retry_count
    
    def parse_context_request(self, response: str) -> Optional[str]:
        """
        解析 [NEED_CONTEXT: xxx] 标记
        返回: 关键词或 None
        """
        match = re.search(settings.context_request_pattern, response)
        return match.group(1) if match else None
    
    def strip_context_request(self, response: str) -> str:
        """移除回复中的 [NEED_CONTEXT: xxx] 标记"""
        return re.sub(settings.context_request_pattern, '', response).strip()
    
    async def resolve_context_request(
        self, user_id: str, session_id: str, keyword: str
    ) -> List[Message]:
        """
        根据关键词查找历史消息
        
        步骤:
        1. 遍历 Session 内所有 Topic
        2. 在每个 Artifact 中搜索匹配的 section
        3. 提取 sources 中的 message_ids
        4. 加载并返回消息
        """
        topics = await self.firestore.get_all_topics(user_id, session_id)
        
        for topic in topics:
            result = self.artifact.find_section_by_keyword(
                topic.current_artifact, keyword
            )
            
            if result:
                section_title, message_ids = result
                messages = await self.firestore.get_messages_by_ids(
                    user_id, session_id, message_ids
                )
                return messages
        
        return []
```

### 4.8 `services/context_builder.py`（重写）

```python
"""上下文构建器"""

class ContextBuilder:
    
    def __init__(self):
        self.firestore = FirestoreService()
    
    async def build(
        self, user_id: str, session_id: str,
        additional_messages: Optional[List[Message]] = None
    ) -> str:
        """
        构建完整上下文
        
        结构:
        1. System Prompt
        2. 所有 Artifact
        3. 额外加载的历史消息（如果有）
        4. 活跃窗口消息
        """
        parts = []
        
        # 1. System Prompt
        parts.append(self.build_system_prompt())
        
        # 2. Artifacts
        artifacts_text = await self.load_all_artifacts(user_id, session_id)
        if artifacts_text:
            parts.append(f"## 知识文档\n\n{artifacts_text}")
        
        # 3. 额外加载的历史
        if additional_messages:
            history_text = self.format_messages(additional_messages, "加载的历史对话")
            parts.append(history_text)
        
        # 4. 活跃窗口
        active_messages = await self.firestore.get_active_messages(
            user_id, session_id, settings.active_window_size
        )
        if active_messages:
            active_text = self.format_messages(active_messages, "最近对话")
            parts.append(active_text)
        
        return "\n\n---\n\n".join(parts)
    
    def build_system_prompt(self) -> str:
        """构建系统提示词"""
        return """## 系统角色

你是一个具有长期记忆能力的智能助手。

**关于知识文档:**
- 你可以访问之前对话积累的知识文档（Artifact）
- 文档以 Markdown 格式组织，包含历史讨论的总结

**关于历史回溯:**
- 如果你需要回顾之前讨论的具体细节（而不仅仅是总结）
- 请在回复中使用 [NEED_CONTEXT: 关键词] 标记
- 例如：[NEED_CONTEXT: 列表推导式的嵌套用法]
- 系统会为你加载相关的原始对话，然后你再继续回答

**注意:**
- 只有在需要具体细节时才使用这个标记
- 如果知识文档中的信息已经足够，直接回答即可"""
    
    async def load_all_artifacts(self, user_id: str, session_id: str) -> str:
        """加载 Session 内所有 Artifact"""
        topics = await self.firestore.get_all_topics(user_id, session_id)
        
        if not topics:
            return ""
        
        parts = []
        for topic in topics:
            if topic.current_artifact:
                parts.append(f"### {topic.title}\n\n{topic.current_artifact}")
        
        return "\n\n".join(parts)
    
    def format_messages(self, messages: List[Message], title: str) -> str:
        """格式化消息列表"""
        lines = [f"## {title}\n"]
        
        for msg in messages:
            role = "USER" if msg.role == MessageRole.USER else "ASSISTANT"
            lines.append(f"**{role}**: {msg.content}\n")
        
        return "\n".join(lines)
```

### 4.9 `services/llm_service.py`（修改）

```python
"""LLM 服务模块"""

class LLMService:
    
    def __init__(self):
        genai.configure(api_key=settings.gemini_api_key)
        self.flash_model = genai.GenerativeModel(settings.gemini_flash_model)
        self.main_model = genai.GenerativeModel(settings.gemini_main_model)
    
    # ==================== 保留的方法 ====================
    
    async def generate_response(self, context: str, user_query: str) -> str:
        """生成回复（保留，略微调整 prompt）"""
    
    async def check_should_update_artifact(self, ...) -> Dict[str, Any]:
        """【保留】可复用于归档判断"""
    
    async def update_artifact(self, ...) -> str:
        """【保留】可复用于合并 Artifact"""
    
    # ==================== 新增的方法 ====================
    
    async def analyze_messages_for_archive(
        self, messages: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """
        【新增】子代理：分析消息块，提取主题和生成 Artifact
        
        返回:
        {
            "title": "Python编程",
            "summary": "讨论了列表推导式和异步编程",
            "artifact": "# Python编程\n\n## 列表推导式 <!-- sources: msg_001 -->..."
        }
        """
    
    async def should_merge_topics(
        self, existing_title: str, existing_summary: str,
        new_title: str, new_summary: str
    ) -> bool:
        """【新增】判断两个 Topic 是否应该合并"""
    
    async def merge_artifacts(
        self, existing_artifact: str, new_artifact: str
    ) -> str:
        """【新增】合并两个 Artifact"""
    
    # ==================== 删除的方法 ====================
    
    # async def route_decision(self, ...) -> Dict[str, Any]:
    #     不再需要实时路由
    
    # async def find_relevant_sections(self, ...) -> List[str]:
    #     移到 artifact_service
    
    # async def generate_topic_summary(self, ...) -> str:
    #     不再需要
```

### 4.10 `services/artifact_service.py`（修改）

```python
"""Artifact 管理服务"""

class ArtifactService:
    
    # ==================== 保留的方法 ====================
    
    def parse_artifact_sources(self, artifact: str) -> Dict[str, List[str]]:
        """解析 <!-- sources: --> 索引（保留）"""
    
    def extract_section_content(self, artifact: str, section_title: str) -> Optional[str]:
        """提取章节内容（保留）"""
    
    # ==================== 新增的方法 ====================
    
    def find_section_by_keyword(
        self, artifact: str, keyword: str
    ) -> Optional[Tuple[str, List[str]]]:
        """
        【新增】根据关键词查找匹配的 section
        
        匹配策略:
        1. 关键词出现在 section 标题中
        2. 关键词出现在 section 内容中
        
        返回: (section_title, message_ids) 或 None
        """
    
    def get_all_sections(self, artifact: str) -> List[Dict[str, Any]]:
        """
        【新增】获取 Artifact 中所有 section
        
        返回:
        [
            {"title": "## 列表推导式", "content": "...", "sources": ["msg_001"]},
            ...
        ]
        """
    
    # ==================== 删除的方法 ====================
    
    # async def should_update_artifact(self, ...) -> bool:
    #     移到 archive_service
    
    # async def update_artifact(self, ...) -> str:
    #     移到 archive_service
    
    # async def build_context_from_artifact(self, ...) -> str:
    #     移到 context_builder
    
    # async def find_relevant_sections(self, ...) -> List[str]:
    #     改为同步的 find_section_by_keyword
    
    # async def load_section_messages(self, ...) -> List[Any]:
    #     移到 context_loop
```

### 4.11 `routers/chat.py`（重写）

```python
"""聊天 API 路由"""

router = APIRouter()

# 服务初始化
firestore = FirestoreService()
archive_service = ArchiveService()
context_loop = ContextLoop()


class ChatRequest(BaseModel):
    user_id: str
    session_id: Optional[str] = None
    message: str


class ChatResponse(BaseModel):
    session_id: str
    response: str
    archived: bool              # 本次是否触发了归档
    archive_topic: Optional[str]  # 归档到的 topic title
    context_loads: int          # 上下文重构次数


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    主对话接口
    
    流程:
    1. 获取或创建 Session
    2. 保存用户消息
    3. 检查并执行归档（如需要）
    4. 运行上下文循环，生成回复
    5. 保存助手消息
    6. 返回响应
    """
    user_id = request.user_id
    message = request.message
    
    # 1. 获取或创建 Session
    if request.session_id:
        session = await firestore.get_session(user_id, request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")
        session_id = request.session_id
    else:
        session = await firestore.create_session(user_id)
        session_id = session.session_id
    
    # 2. 保存用户消息
    user_msg = MessageCreate(
        role=MessageRole.USER,
        content=message,
        is_archived=False
    )
    await firestore.add_message(user_id, session_id, user_msg)
    
    # 3. 检查并执行归档
    archived = False
    archive_topic = None
    
    if await archive_service.check_should_archive(user_id, session_id):
        thread_id = await archive_service.execute_archive(user_id, session_id)
        if thread_id:
            archived = True
            topic = await firestore.get_topic(user_id, session_id, thread_id)
            archive_topic = topic.title if topic else None
    
    # 4. 运行上下文循环
    response_text, context_loads = await context_loop.run(
        user_id, session_id, message
    )
    
    # 5. 保存助手消息
    assistant_msg = MessageCreate(
        role=MessageRole.ASSISTANT,
        content=response_text,
        is_archived=False
    )
    await firestore.add_message(user_id, session_id, assistant_msg)
    
    # 6. 返回响应
    return ChatResponse(
        session_id=session_id,
        response=response_text,
        archived=archived,
        archive_topic=archive_topic,
        context_loads=context_loads
    )
```

### 4.12 `routers/topics.py`（修改）

```python
"""主题管理 API 路由 - 路径调整"""

router = APIRouter()

# 【改动】所有端点增加 session_id 参数

@router.get("/sessions/{user_id}/{session_id}/topics")
async def get_session_topics(user_id: str, session_id: str):
    """获取 Session 内所有主题"""

@router.get("/sessions/{user_id}/{session_id}/topics/{thread_id}")
async def get_topic_detail(user_id: str, session_id: str, thread_id: str):
    """获取主题详情"""

@router.get("/sessions/{user_id}/{session_id}/topics/{thread_id}/artifact")
async def get_topic_artifact(user_id: str, session_id: str, thread_id: str):
    """获取主题 Artifact"""

@router.get("/sessions/{user_id}/{session_id}/topics/{thread_id}/versions")
async def get_artifact_versions(user_id: str, session_id: str, thread_id: str):
    """获取 Artifact 版本历史"""
```

### 4.13 `utils/__init__.py`（修改）

```python
"""工具函数包"""

# 【改动】暂时不导出 embedding，保留文件供 BigQuery 使用

# from .embedding import get_cloudflare_embedding, cosine_similarity
# __all__ = ["get_cloudflare_embedding", "cosine_similarity"]

__all__ = []
```

### 4.14 `utils/embedding.py`（保留）

```python
"""
Embedding 工具函数

当前状态: 热记忆阶段暂不使用
未来用途: BigQuery 冷记忆的语义检索

保留原有代码不变
"""

# ... 原有代码保持不变 ...
```

### 4.15 `services/__init__.py`（修改）

```python
"""业务逻辑服务包"""

from .firestore_service import FirestoreService
from .llm_service import LLMService
from .artifact_service import ArtifactService
from .context_builder import ContextBuilder
from .archive_service import ArchiveService      # 新增
from .context_loop import ContextLoop            # 新增

# 【移除】RouterService 不再导出

__all__ = [
    "FirestoreService",
    "LLMService",
    "ArtifactService",
    "ContextBuilder",
    "ArchiveService",
    "ContextLoop",
]
```

---

## 五、上下文最终结构

发送给主模型的完整上下文：

```
## 系统角色

你是一个具有长期记忆能力的智能助手。

**关于知识文档:**
- 你可以访问之前对话积累的知识文档（Artifact）
...

**关于历史回溯:**
- 如果你需要回顾之前讨论的具体细节
- 请使用 [NEED_CONTEXT: 关键词] 标记
...

---

## 知识文档

### Python编程

## 列表推导式 <!-- sources: msg_001, msg_002 -->
基本语法是 [expr for x in iterable]，可以带条件过滤。

## 异步编程 <!-- sources: msg_015, msg_016 -->
决定使用 asyncio 处理 IO 密集型任务。

### Docker部署

## 基础配置 <!-- sources: msg_025 -->
使用 python:3.9-slim 作为基础镜像。

---

## 加载的历史对话（如果有 [NEED_CONTEXT] 触发）

**USER**: Python的列表推导式怎么用?
**ASSISTANT**: 列表推导式的基本语法是...

---

## 最近对话

**USER**: 刚才说的嵌套写法是什么？
**ASSISTANT**: ...
**USER**: （最新消息）
```

---

## 六、施工顺序

```
Phase 1: 基础层改动（无依赖）
├── 1.1 config.py - 更新配置项
├── 1.2 models/message.py - 添加 is_archived
├── 1.3 models/topic.py - 删除字段，添加 summary
├── 1.4 models/session.py - 删除 current_thread_id
└── 1.5 models/__init__.py - 更新导出

Phase 2: 数据层改动
└── 2.1 firestore_service.py - 路径调整 + 新方法

Phase 3: 服务层改动（有依赖，按顺序）
├── 3.1 artifact_service.py - 简化 + 新方法
├── 3.2 llm_service.py - 删除路由方法 + 新方法
├── 3.3 context_builder.py - 重写
├── 3.4 archive_service.py - 新增
├── 3.5 context_loop.py - 新增
└── 3.6 services/__init__.py - 更新导出

Phase 4: API 层改动
├── 4.1 chat.py - 重写
└── 4.2 topics.py - 路径调整

Phase 5: 收尾
├── 5.1 utils/__init__.py - 暂停导出 embedding
├── 5.2 移动 router_service.py 到 _deprecated/
├── 5.3 requirements.txt - 检查依赖
└── 5.4 README.md - 更新文档

Phase 6: 测试
├── 6.1 移动 test_router.py 到 _deprecated/
├── 6.2 修改 test_artifact.py
├── 6.3 新增 test_archive.py
├── 6.4 新增 test_context_loop.py
├── 6.5 重写 test_integration.py
└── 6.6 更新 tests/README.md
```

---

## 七、预期效果

### 用户体验

1. **对话流畅** — 无需等待路由判断，直接对话
2. **历史可追溯** — 需要细节时，AI 主动请求加载
3. **归档无感知** — 后台自动处理，用户不受影响

### 技术效果

1. **上下文可控** — 活跃窗口固定大小，不会膨胀
2. **知识沉淀** — 重要内容压缩进 Artifact
3. **按需加载** — 历史消息只在需要时才拉取
4. **扩展预留** — Embedding 代码保留，BigQuery 可复用

---

准备好开始施工了吗？