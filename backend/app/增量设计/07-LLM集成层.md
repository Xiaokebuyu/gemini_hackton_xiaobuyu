# LLM 集成层

## 模块概要

Gemini LLM 接入层：适配器、executor、context window、narrator 实现。无 LLM 时优雅降级。

### 核心模块

| 模块 | 位置 | 职责 |
|------|------|------|
| LlmPort | `adapters/llm.py` | LLM 抽象端口 Protocol |
| GeminiLlmProvider | `app/llm_gemini.py` | google-genai SDK 具体实现，统一使用 gemini-3-flash-preview |
| AgenticExecutor | `narrative/executor.py` | 统一 agent 执行器：single-pass + multi-turn agentic loop |
| ContextWindow | `narrative/context_window.py` | 每 actor 200K token 滑动窗口工作记忆 + graphize 溢出检测 |
| InstanceManager | `narrative/instance_manager.py` | 每 NPC LRU 实例池（session 隔离） |
| AgenticGmNarrator | `app/narrators.py` | Settlement GM 叙述 LLM 实现 |
| AgenticNarrativePlanner | `app/narrators.py` | Planner LLM 实现（multi-turn agent + design_skill 工具） |
| MilestoneOutlineGenerator | `app/narrators.py` | 里程碑大纲 LLM 生成器 |
| AgentOrchestrationService | `app/agent_orchestration.py` | 应用层 NPC/GM/Teammate agent 编排 |
| deps.py | `app/deps.py` | 依赖注入：检测 GOOGLE_API_KEY / GEMINI_API_KEY 自动启用 LLM |

### 记忆与知识图谱

| 模块 | 位置 | 职责 |
|------|------|------|
| MemoryRetriever | `narrative/memory_retriever.py` | L6 记忆召回 Protocol |
| WorldKnowledgeGraph | `app/world_knowledge_graph.py` | 静态世界知识图谱（NetworkX + BFS 扩散激活） |
| KnowledgeGraphMemoryRetriever | `app/memory_retriever_impl.py` | MemoryRetriever 具体实现（wrap MemoryGraphPort） |
| MemoryGraphPort | `adapters/memory_graph_port.py` | 知识图谱持久化/检索端口 |

### 降级行为

- 无 LLM 时：跳过 Agent 反应，使用确定性 evaluator/fallback
- Planner 无 LLM 时：只执行确定性预处理（GC/despawn/auto-escalation），不生成新 directive
- GM 无 LLM 时：跳过叙述生成
