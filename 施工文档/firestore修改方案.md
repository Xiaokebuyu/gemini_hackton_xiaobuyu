好，让我仔细梳理一下现有代码后给出完整方案。

---

# 施工方案

## 一、架构变化总览

```
【现有架构】                          【新架构】

User                                  User
├── sessions                          └── sessions
│   └── messages                          ├── messages
└── topic_threads  ← 跨Session              │   └── is_archived 字段
    └── artifact_versions                 └── topics  ← Session内
                                              └── artifact_versions
```

## 二、数据结构变化

### 2.1 Firestore 结构

```
users/{user_id}/
└── sessions/{session_id}/
    ├── created_at
    ├── updated_at
    │
    ├── messages/{message_id}/        # 改动：新增 is_archived
    │   ├── role
    │   ├── content
    │   ├── timestamp
    │   ├── is_excluded
    │   ├── is_archived (新增)        ← 标记是否已归档
    │   └── thread_id (保留，归档后填充)
    │
    └── topics/{thread_id}/           # 改动：从 User 子集合移到 Session 子集合
        ├── title
        ├── current_artifact
        ├── created_at
        └── artifact_versions/{version_id}/
```

### 2.2 Model 改动

**`message.py`**
```python
# 新增字段
is_archived: bool = False
```

**`topic.py`**
```python
# 删除字段（不再需要跨 Session）
- summary_embedding
- parent_thread_ids
- child_thread_ids
```

**`session.py`**
```python
# 删除字段（不再需要当前主题跟踪）
- current_thread_id
```

---

## 三、文件级改动清单

### 3.1 删除

| 文件 | 原因 |
|------|------|
| `services/router_service.py` | 不再需要实时路由 |
| `utils/embedding.py` | 热记忆不再需要 embedding 相似度匹配 |
| `utils/__init__.py` | 重写，移除 embedding 导出 |

### 3.2 新增

| 文件 | 职责 |
|------|------|
| `services/archive_service.py` | 归档逻辑：检测阈值、截取消息、调用子代理分析、创建/合并 Topic |
| `services/context_loop.py` | 上下文循环：解析 `[NEED_CONTEXT]` 标记、注入历史、重构上下文 |

### 3.3 修改

| 文件 | 改动项 |
|------|--------|
| `config.py` | 删除路由配置，新增窗口配置 |
| `models/message.py` | 新增 `is_archived` 字段 |
| `models/topic.py` | 删除 `summary_embedding`、`parent_thread_ids`、`child_thread_ids` |
| `models/session.py` | 删除 `current_thread_id` |
| `services/firestore_service.py` | Topic 路径改到 Session 下；新增归档相关方法 |
| `services/llm_service.py` | 删除路由相关方法；新增归档分析方法 |
| `services/artifact_service.py` | 简化，聚焦解析和加载 |
| `services/context_builder.py` | 重构，适配新的上下文构建逻辑 |
| `services/__init__.py` | 更新导出 |
| `routers/chat.py` | 重写主流程 |
| `routers/topics.py` | 调整 Topic 查询路径（需要 session_id） |

---

## 四、各模块详细设计

### 4.1 `config.py`

```python
# 删除
- embedding_threshold: float = 0.85
- max_candidate_topics: int = 3

# 新增
+ active_window_size: int = 20       # 活跃窗口大小
+ archive_threshold: int = 40        # 触发归档的总消息数
+ max_context_retry: int = 3         # 上下文重构最大次数
+ context_request_pattern: str = r'\[NEED_CONTEXT:\s*(.+?)\]'  # 标记正则
```

### 4.2 `services/firestore_service.py`

**删除方法：**
- `update_topic_embedding()`

**修改方法：**
- `create_topic()` — 路径改为 `sessions/{session_id}/topics/`
- `get_topic()` — 需要 `session_id` 参数
- `get_all_topics()` — 需要 `session_id` 参数
- `update_artifact()` — 路径调整
- `save_artifact_version()` — 路径调整
- `get_artifact_versions()` — 路径调整

**新增方法：**
```python
async def get_active_messages(
    self, user_id: str, session_id: str, limit: int
) -> List[Message]:
    """获取未归档的消息（按时间倒序，取最新 limit 条）"""

async def get_pending_archive_messages(
    self, user_id: str, session_id: str, 
    active_window_size: int, archive_threshold: int
) -> List[Message]:
    """获取待归档的消息（超出活跃窗口的部分）"""

async def mark_messages_archived(
    self, user_id: str, session_id: str, 
    message_ids: List[str], thread_id: str
) -> None:
    """批量标记消息为已归档，并关联 thread_id"""

async def get_messages_by_ids(
    self, user_id: str, session_id: str, 
    message_ids: List[str]
) -> List[Message]:
    """根据消息ID列表批量获取消息"""

async def count_active_messages(
    self, user_id: str, session_id: str
) -> int:
    """统计未归档消息数量"""
```

### 4.3 `services/archive_service.py` (新增)

```python
class ArchiveService:
    """归档服务 - 负责消息归档和 Artifact 创建"""
    
    async def check_should_archive(
        self, user_id: str, session_id: str
    ) -> bool:
        """检查是否达到归档阈值"""
    
    async def execute_archive(
        self, user_id: str, session_id: str
    ) -> Optional[str]:
        """
        执行归档流程：
        1. 获取待归档消息
        2. 调用子代理分析
        3. 创建或合并 Topic/Artifact
        4. 标记消息已归档
        返回：新创建或更新的 thread_id
        """
    
    async def analyze_messages(
        self, messages: List[Message]
    ) -> Dict[str, Any]:
        """
        子代理分析消息块：
        - 提取主题
        - 生成 Artifact 内容
        - 返回 {title, artifact_content, message_ids}
        """
    
    async def find_mergeable_topic(
        self, user_id: str, session_id: str, 
        new_title: str, new_content: str
    ) -> Optional[TopicThread]:
        """查找可合并的现有 Topic（基于标题相似度或内容重叠）"""
    
    async def merge_artifact(
        self, existing_artifact: str, 
        new_content: str, 
        message_ids: List[str]
    ) -> str:
        """合并两个 Artifact 内容"""
```

### 4.4 `services/context_loop.py` (新增)

```python
class ContextLoop:
    """上下文循环 - 处理动态上下文重构"""
    
    def parse_context_request(
        self, response: str
    ) -> Optional[str]:
        """
        解析回复中的 [NEED_CONTEXT: xxx] 标记
        返回：请求的关键词，如 "列表推导式"
        """
    
    def strip_context_request(
        self, response: str
    ) -> str:
        """移除回复中的 [NEED_CONTEXT: xxx] 标记"""
    
    async def resolve_context_request(
        self, user_id: str, session_id: str,
        request_key: str
    ) -> List[Message]:
        """
        根据请求关键词找到对应的历史消息：
        1. 遍历 Session 内所有 Topic 的 Artifact
        2. 匹配 section 标题或内容
        3. 提取 sources 索引
        4. 加载对应消息
        """
    
    async def run_loop(
        self, user_id: str, session_id: str,
        initial_context: str, user_query: str
    ) -> str:
        """
        运行上下文循环：
        1. 生成回复
        2. 检查是否有 [NEED_CONTEXT] 标记
        3. 有则加载历史、重构上下文、重新生成
        4. 最多重试 max_context_retry 次
        5. 返回最终回复
        """
```

### 4.5 `services/llm_service.py`

**删除方法：**
- `route_decision()` — 不再需要
- `generate_topic_summary()` — 不再需要

**修改方法：**
- `generate_response()` — 简化，System Prompt 中加入 `[NEED_CONTEXT]` 使用说明

**新增方法：**
```python
async def analyze_for_archive(
    self, messages: List[Dict[str, str]]
) -> Dict[str, Any]:
    """
    子代理：分析消息块，返回主题和 Artifact 内容
    返回：{
        "title": "主题标题",
        "artifact": "Markdown 内容（带 sources 索引）",
        "summary": "简短摘要（用于合并判断）"
    }
    """

async def should_merge_topics(
    self, existing_title: str, existing_summary: str,
    new_title: str, new_summary: str
) -> bool:
    """判断两个 Topic 是否应该合并"""

async def merge_artifacts(
    self, existing_artifact: str,
    new_artifact: str
) -> str:
    """合并两个 Artifact"""
```

### 4.6 `services/artifact_service.py`

**保留方法：**
- `parse_artifact_sources()` — 解析 `<!-- sources: -->` 索引
- `extract_section_content()` — 提取章节内容

**删除方法：**
- `should_update_artifact()` — 改由 archive_service 处理
- `update_artifact()` — 改由 archive_service 处理
- `build_context_from_artifact()` — 移到 context_builder

**新增方法：**
```python
async def find_section_by_keyword(
    self, artifact: str, keyword: str
) -> Optional[Tuple[str, List[str]]]:
    """
    根据关键词在 Artifact 中查找匹配的 section
    返回：(section_title, source_message_ids) 或 None
    """
```

### 4.7 `services/context_builder.py`

**重构：**

```python
class ContextBuilder:
    """上下文构建器"""
    
    def build_system_prompt(self) -> str:
        """
        构建系统提示词，包含 [NEED_CONTEXT] 使用说明：
        "如果你需要回顾之前讨论过的具体内容，
         请在回复中使用 [NEED_CONTEXT: 关键词] 标记，
         系统会为你加载相关的历史对话。"
        """
    
    async def build_context(
        self, user_id: str, session_id: str,
        additional_messages: Optional[List[Message]] = None
    ) -> str:
        """
        构建完整上下文：
        1. System Prompt
        2. Session 内所有 Topic 的 Artifact
        3. 额外加载的历史消息（如果有）
        4. 活跃窗口消息
        """
    
    async def load_all_artifacts(
        self, user_id: str, session_id: str
    ) -> str:
        """加载 Session 内所有 Topic 的 Artifact"""
    
    async def load_active_window(
        self, user_id: str, session_id: str
    ) -> List[Message]:
        """加载活跃窗口的消息"""
    
    def format_messages_for_context(
        self, messages: List[Message]
    ) -> str:
        """将消息列表格式化为上下文文本"""
```

### 4.8 `routers/chat.py`

**重写主流程：**

```python
@router.post("/chat")
async def chat(request: ChatRequest):
    """
    主对话接口 - 新流程：
    
    1. 获取或创建 Session
    2. 保存用户消息（is_archived=False）
    3. 检查是否需要归档
       - 是 → 执行归档 → 更新 Artifact
    4. 构建上下文
    5. 运行上下文循环（处理 [NEED_CONTEXT] 标记）
    6. 保存助手回复
    7. 返回响应
    """
```

**响应模型调整：**

```python
class ChatResponse(BaseModel):
    session_id: str
    response: str
    archived: bool           # 本次是否触发了归档
    context_loads: int       # 上下文重构次数
    # 删除：thread_id, thread_title, is_new_topic, artifact_updated
```

### 4.9 `routers/topics.py`

**路径调整（需要 session_id）：**

```
GET /api/sessions/{user_id}/{session_id}/topics
GET /api/sessions/{user_id}/{session_id}/topics/{thread_id}
GET /api/sessions/{user_id}/{session_id}/topics/{thread_id}/artifact
GET /api/sessions/{user_id}/{session_id}/topics/{thread_id}/versions
```

---

## 五、上下文结构设计

最终发给主模型的上下文：

```
=== System Prompt ===
你是一个有记忆的AI助手...
如果需要回顾具体内容，使用 [NEED_CONTEXT: 关键词] 标记。

=== 知识文档 ===
# Topic: Python编程
## 列表推导式 <!-- sources: msg_001, msg_002 -->
基本语法是...

# Topic: Docker部署
## 容器配置 <!-- sources: msg_015 -->
...

=== 加载的历史对话 ===（如果有 [NEED_CONTEXT] 触发）
[msg_001] USER: Python的列表推导式怎么用?
[msg_002] ASSISTANT: 列表推导式的基本语法是...

=== 最近对话 ===
USER: ...
ASSISTANT: ...
USER: ...（最新 20 条）
```

---

## 六、测试更新

| 文件 | 改动 |
|------|------|
| `test_router.py` | **删除**（路由不再存在） |
| `test_artifact.py` | 保留，新增 `find_section_by_keyword` 测试 |
| `test_integration.py` | 重写，测试新流程 |
| `test_archive.py` | **新增**，测试归档逻辑 |
| `test_context_loop.py` | **新增**，测试上下文循环 |

---

## 七、施工顺序

```
Phase 1: 基础改动
  ├── 1.1 修改 config.py
  ├── 1.2 修改 models/（message, topic, session）
  └── 1.3 修改 firestore_service.py（路径调整 + 新方法）

Phase 2: 核心服务
  ├── 2.1 删除 router_service.py, utils/embedding.py
  ├── 2.2 新增 archive_service.py
  ├── 2.3 新增 context_loop.py
  ├── 2.4 修改 llm_service.py
  ├── 2.5 修改 artifact_service.py
  └── 2.6 重构 context_builder.py

Phase 3: API 层
  ├── 3.1 重写 chat.py
  └── 3.2 调整 topics.py

Phase 4: 收尾
  ├── 4.1 更新 services/__init__.py
  ├── 4.2 更新测试文件
  └── 4.3 更新文档
```

---

有需要调整或细化的地方吗？