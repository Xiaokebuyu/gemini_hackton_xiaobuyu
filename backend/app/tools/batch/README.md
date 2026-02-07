# 世界书批量导入工具

使用 Gemini Batch API（50% 成本）将世界书 JSON 转换为知识图谱数据。

## 工具列表

| 文件 | 用途 |
|------|------|
| `lorebook_prep.py` | 解析 SillyTavern Lorebook JSON，输出 markdown 和 JSONL |
| `global_summary.py` | 用 1M 上下文分析完整世界书，生成全局实体摘要 |
| `request_generator.py` | 生成 Batch API 请求文件 |
| `job_manager.py` | 提交/监控/下载批量任务 |
| `result_processor.py` | 处理 LLM 输出，合并去重，生成最终图谱 |

---

## lorebook_prep.py

解析 SillyTavern/TavernAI Lorebook 格式的 JSON 文件。

```bash
python -m app.tools.batch.lorebook_prep \
  --input <世界书.json> \
  --output-dir <输出目录/>
```

**参数：**
- `-i, --input` - 输入的 Lorebook JSON 文件
- `-o, --output-dir` - 输出目录
- `--max-entry-chars` - 单条目最大字符数，超过会切分（默认 4000）

**输出：**
- `worldbook_full.md` - 完整世界书的 markdown 格式
- `entries.jsonl` - 每行一个条目的 JSONL 文件
- `prep_stats.json` - 解析统计信息

---

## global_summary.py

利用 Gemini 的大上下文窗口（1M tokens）分析完整世界书，提取全局实体和关系摘要。这一步确保后续批量处理时实体 ID 一致。

```bash
python -m app.tools.batch.global_summary \
  --input <worldbook_full.md> \
  --output <global_summary.json> \
  --model gemini-3-flash-preview
```

**参数：**
- `-i, --input` - 输入的 markdown 文件（来自 lorebook_prep）
- `-o, --output` - 输出的摘要 JSON 文件
- `-m, --model` - 使用的模型（默认 gemini-2.0-flash）

**输出：**
- `global_summary.json` - 包含 entities、key_relations、alias_map
- `summary_context.txt` - 紧凑格式，用于批量请求的上下文

---

## request_generator.py

生成符合 Gemini Batch API 格式的 JSONL 请求文件。每个条目会附带全局摘要作为上下文。

```bash
python -m app.tools.batch.request_generator \
  --entries <entries.jsonl> \
  --summary <global_summary.json> \
  --output <batch_requests.jsonl> \
  --model gemini-3-flash-preview
```

**参数：**
- `-e, --entries` - 条目 JSONL 文件（来自 lorebook_prep）
- `-s, --summary` - 全局摘要 JSON（来自 global_summary）
- `-o, --output` - 输出的批量请求 JSONL
- `-m, --model` - 目标模型（仅记录，实际模型在提交时指定）
- `--summary-max-chars` - 摘要上下文最大字符数（默认 30000）

---

## job_manager.py

管理 Gemini Batch API 任务的完整生命周期。

### 提交任务

```bash
python -m app.tools.batch.job_manager submit \
  --input <batch_requests.jsonl> \
  --display-name "任务名称" \
  --model gemini-3-flash-preview \
  --job-file <保存任务信息.json>
```

### 查看状态

```bash
python -m app.tools.batch.job_manager status \
  --job-name batches/xxxxxx
```

### 监控并下载结果

```bash
python -m app.tools.batch.job_manager monitor \
  --job-name batches/xxxxxx \
  --output <batch_results.jsonl> \
  --poll-interval 60
```

### 列出所有任务

```bash
python -m app.tools.batch.job_manager list
```

---

## result_processor.py

处理批量任务的输出，解析 LLM 响应，合并重复节点/边，生成最终图谱数据。

```bash
python -m app.tools.batch.result_processor \
  --input <batch_results.jsonl> \
  --output <extracted_graphs.jsonl> \
  --merge-by-name \
  --dedupe-edges \
  --report <merge_report.json>
```

**参数：**
- `-i, --input` - 批量结果 JSONL（来自 job_manager monitor）
- `-o, --output` - 输出图谱文件（.json 或 .jsonl）
- `--merge-by-name` - 按 (type, name) 合并相同实体
- `--dedupe-edges` - 按 (source, target, relation) 去重边
- `--drop-orphan-edges` - 删除引用不存在节点的边
- `--report` - 输出处理报告

---

## 完整流程示例

```bash
# 1. 解析世界书
python -m app.tools.batch.lorebook_prep \
  -i "哥布林杀手.json" \
  -o data/gs/

# 2. 生成全局摘要
python -m app.tools.batch.global_summary \
  -i data/gs/worldbook_full.md \
  -o data/gs/global_summary.json \
  -m gemini-3-flash-preview

# 3. 生成批量请求
python -m app.tools.batch.request_generator \
  -e data/gs/entries.jsonl \
  -s data/gs/global_summary.json \
  -o data/gs/batch_requests.jsonl

# 4. 提交任务
python -m app.tools.batch.job_manager submit \
  -i data/gs/batch_requests.jsonl \
  -n "gs-worldbook" \
  -m gemini-3-flash-preview

# 5. 查看状态 / 监控下载
python -m app.tools.batch.job_manager status -j batches/xxx
python -m app.tools.batch.job_manager monitor -j batches/xxx -o data/gs/results.jsonl

# 6. 处理结果
python -m app.tools.batch.result_processor \
  -i data/gs/results.jsonl \
  -o data/gs/graph.jsonl \
  --merge-by-name --dedupe-edges

# 7. 导入 Firestore
python -m app.tools.graph_importer \
  --input data/gs/graph.jsonl \
  --world goblin_slayer \
  --graph ontology \
  --validate
```

---

## 支持的模型

- `gemini-3-flash-preview` - 推荐，快速便宜
- `gemini-2.0-flash` - 备选

---

## 快捷状态检查

项目根目录有 `check_batch.sh` 脚本：

```bash
./check_batch.sh                    # 检查默认任务
./check_batch.sh batches/你的任务ID  # 检查指定任务
```
