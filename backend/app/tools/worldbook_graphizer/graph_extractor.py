"""
知识图谱提取器 (Batch API 版本)

使用 Gemini Batch API 从酒馆卡片条目中提取实体和关系，生成 world_graph.json。

流程:
1. 解析酒馆卡片 -> entries.jsonl
2. 全局摘要生成 -> global_summary.json (使用 1M 上下文)
3. 生成批量请求 -> batch_requests.jsonl
4. 提交并监控批量任务
5. 处理结果并合并 -> world_graph.json
"""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import time

from google import genai
from google.genai import types

from app.config import settings
from app.models.graph import GraphData, MemoryEdge, MemoryNode
from .models import WorldbookEntry, TavernCardData
from .tavern_card_parser import TavernCardParser, GRAPHABLE_TYPES


# 默认使用 gemini-3-flash-preview
DEFAULT_MODEL = "gemini-3-flash-preview"


class GraphExtractor:
    """知识图谱提取器 (Batch API 版本)"""

    # 每批处理的条目数量
    BATCH_SIZE = 30

    def __init__(
        self,
        model: str = None,
        api_key: str = None,
        verbose: bool = True,
    ):
        """
        初始化提取器

        Args:
            model: Gemini 模型名称 (默认: gemini-3-flash-preview)
            api_key: API 密钥
            verbose: 是否输出详细信息
        """
        self.client = genai.Client(api_key=api_key or settings.gemini_api_key)
        self.model = model or DEFAULT_MODEL
        self.verbose = verbose

        # 加载 prompt 模板
        self.entity_prompt = self._load_prompt("entity_extraction.md")
        self.relation_prompt = self._load_prompt("relation_extraction.md")

    def _load_prompt(self, filename: str) -> str:
        """加载 prompt 模板"""
        template_path = Path(__file__).parent / "prompts" / filename
        if template_path.exists():
            return template_path.read_text(encoding="utf-8")
        raise FileNotFoundError(f"Prompt template not found: {template_path}")

    def _log(self, message: str) -> None:
        """输出日志"""
        if self.verbose:
            print(message)

    async def build_graph(self, json_path: Path, output_dir: Optional[Path] = None) -> GraphData:
        """
        从酒馆卡片文件构建完整知识图谱 (使用 Batch API)

        Args:
            json_path: 酒馆卡片 JSON 文件路径
            output_dir: 中间文件输出目录 (可选)

        Returns:
            GraphData: 知识图谱数据
        """
        if output_dir is None:
            output_dir = json_path.parent / "batch_temp"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Phase 1: 解析酒馆卡片
        self._log(f"[Phase 1] Parsing tavern card: {json_path}")
        parser = TavernCardParser()
        data = parser.parse(json_path)

        if self.verbose:
            parser.print_summary(data)

        # 获取可图谱化的条目
        graphable_entries = parser.get_graphable_entries(data)
        self._log(f"\nGraphable entries: {len(graphable_entries)}")

        # 保存 entries.jsonl
        entries_path = output_dir / "entries.jsonl"
        self._save_entries_jsonl(graphable_entries, entries_path)

        # 生成 worldbook markdown 用于全局摘要
        worldbook_md = self._format_worldbook_markdown(graphable_entries)
        worldbook_path = output_dir / "worldbook_full.md"
        worldbook_path.write_text(worldbook_md, encoding="utf-8")
        self._log(f"  Worldbook markdown: {len(worldbook_md):,} chars")

        # Phase 2: 生成全局摘要 (使用 1M 上下文)
        self._log("\n[Phase 2] Generating global summary...")
        summary = self._generate_global_summary(worldbook_md)
        summary_path = output_dir / "global_summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        self._log(f"  Entities: {len(summary.get('entities', []))}")
        self._log(f"  Relations: {len(summary.get('key_relations', []))}")

        # Phase 3: 生成批量请求
        self._log("\n[Phase 3] Generating batch requests...")
        requests_path = output_dir / "batch_requests.jsonl"
        self._generate_batch_requests(graphable_entries, summary, requests_path)

        # Phase 4: 提交并监控批量任务
        self._log("\n[Phase 4] Submitting batch job...")
        results_path = output_dir / "batch_results.jsonl"
        self._run_batch_job(requests_path, results_path)

        # Phase 5: 处理结果
        self._log("\n[Phase 5] Processing results...")
        nodes, edges = self._process_batch_results(results_path)
        self._log(f"  Raw nodes: {len(nodes)}")
        self._log(f"  Raw edges: {len(edges)}")

        # 合并去重
        merged_nodes, id_alias_map = self._merge_nodes(nodes)
        merged_edges = self._merge_edges(edges, id_alias_map, set(n["id"] for n in merged_nodes))

        self._log(f"  Merged nodes: {len(merged_nodes)}")
        self._log(f"  Merged edges: {len(merged_edges)}")

        # 转换为 GraphData
        graph_data = self._to_graph_data(merged_nodes, merged_edges)

        return graph_data

    def _save_entries_jsonl(self, entries: List[WorldbookEntry], path: Path) -> None:
        """保存条目为 JSONL 格式"""
        with path.open("w", encoding="utf-8") as f:
            for i, entry in enumerate(entries):
                data = {
                    "chunk_id": f"entry_{i}",
                    "entry_type": entry.entry_type,
                    "entry_name": entry.entry_name,
                    "entry_keys": entry.key[:10],
                    "entry_comment": entry.comment,
                    "content": entry.content,
                }
                f.write(json.dumps(data, ensure_ascii=False))
                f.write("\n")

    def _format_worldbook_markdown(self, entries: List[WorldbookEntry]) -> str:
        """格式化条目为 markdown"""
        sections = []
        for entry in entries:
            header = entry.comment or f"[{entry.entry_type}]"
            keys = ", ".join(entry.key[:5]) if entry.key else ""
            section = f"""## {header}
关键词: {keys}

{entry.content[:3000]}

---
"""
            sections.append(section)
        return "\n".join(sections)

    def _generate_global_summary(self, worldbook_content: str) -> Dict[str, Any]:
        """使用 1M 上下文生成全局实体摘要"""
        prompt = f"""你是一个世界观分析专家。请分析以下完整的世界书，提取所有实体和关键关系。

## 任务
1. 识别所有实体（人物、地点、组织、物品、种族、神祇、怪物、概念等）
2. 为每个实体分配确定性 ID（格式：{{type}}_{{主要名称拼音或英文}}）
3. 收集每个实体的所有别名/关键词
4. 识别实体间的主要关系

## 实体类型
- character: 有名有姓的角色
- location: 地点/区域
- faction: 组织/势力
- deity: 神祇
- race: 种族
- monster: 怪物类型
- item: 重要物品
- concept: 抽象概念/规则

## 关系类型
- companion_of: 同伴/战友
- enemy_of: 敌对
- member_of: 从属于组织
- located_at: 位于地点
- worships: 信仰神祇
- rules: 统治/管理
- ally_of: 同盟关系
- native_to: 原生于

## 输出格式（JSON）
{{
  "entities": [
    {{
      "id": "character_priestess",
      "type": "character",
      "name": "女神官",
      "aliases": ["小神官", "地母神信徒"],
      "brief": "地母神的见习神官，哥布林杀手的队友"
    }}
  ],
  "key_relations": [
    {{"source": "character_priestess", "target": "character_goblin_slayer", "relation": "companion_of"}},
    {{"source": "character_priestess", "target": "deity_earth_mother", "relation": "worships"}}
  ]
}}

## 世界书内容
{worldbook_content}
"""

        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        )

        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=config,
        )

        text = self._extract_response_text(response)
        return self._parse_json(text) or {"entities": [], "key_relations": []}

    def _generate_batch_requests(
        self,
        entries: List[WorldbookEntry],
        summary: Dict[str, Any],
        output_path: Path,
    ) -> None:
        """生成批量请求 JSONL"""
        # 格式化摘要上下文
        summary_context = self._format_summary_context(summary)

        prompt_template = """你是一个世界观知识图谱提取器。

## 任务
从以下世界观条目中提取详细的图谱数据（节点和边）。

## 全局实体摘要
以下是已知的所有实体，**你必须优先使用这些 ID**：
{global_summary}

## 当前条目信息
- 条目类型: {entry_type}
- 条目名称: {entry_name}
- 条目关键词: {entry_keys}

## 条目内容
{content}

## 输出格式（JSON）
{{
  "nodes": [
    {{
      "id": "type_name_id",
      "type": "character|location|faction|deity|race|monster|item|concept",
      "name": "中文名称",
      "importance": 0.0-1.0,
      "properties": {{"description": "简短描述"}}
    }}
  ],
  "edges": [
    {{
      "id": "edge_source_target_relation",
      "source": "源实体ID",
      "target": "目标实体ID",
      "relation": "companion_of|enemy_of|member_of|located_at|worships|rules|ally_of|native_to|related_to",
      "weight": 0.0-1.0,
      "properties": {{}}
    }}
  ]
}}

## 注意
1. 只提取该条目明确提到的实体和关系
2. 优先使用全局摘要中已有的实体 ID
3. 新实体 ID 格式：{{type}}_{{name_pinyin_or_english}}
"""

        with output_path.open("w", encoding="utf-8") as f:
            for i, entry in enumerate(entries):
                prompt = prompt_template.format(
                    global_summary=summary_context,
                    entry_type=entry.entry_type or "unknown",
                    entry_name=entry.entry_name or entry.comment,
                    entry_keys=", ".join(entry.key[:5]) or "无",
                    content=entry.content[:4000],
                )

                request = {
                    "key": f"entry_{i}",
                    "request": {
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generation_config": {
                            "response_mime_type": "application/json",
                            "temperature": 0.2,
                        }
                    }
                }
                f.write(json.dumps(request, ensure_ascii=False))
                f.write("\n")

        self._log(f"  Generated {len(entries)} batch requests")

    def _format_summary_context(self, summary: Dict[str, Any], max_chars: int = 30000) -> str:
        """格式化摘要为上下文"""
        lines = ["### 已知实体（必须使用以下 ID）"]
        for entity in summary.get("entities", []):
            entity_id = entity.get("id", "")
            name = entity.get("name", "")
            entity_type = entity.get("type", "")
            aliases = entity.get("aliases", [])
            alias_str = f" 别名: {', '.join(aliases[:3])}" if aliases else ""
            lines.append(f"- `{entity_id}`: {name} [{entity_type}]{alias_str}")

        lines.append("")
        lines.append("### 主要关系")
        for rel in summary.get("key_relations", [])[:50]:
            lines.append(f"- {rel.get('source')} --{rel.get('relation')}--> {rel.get('target')}")

        result = "\n".join(lines)
        if len(result) > max_chars:
            result = result[:max_chars] + "\n... (truncated)"
        return result

    def _run_batch_job(self, requests_path: Path, results_path: Path) -> None:
        """提交并监控批量任务"""
        # 上传文件
        self._log(f"  Uploading {requests_path}...")
        uploaded_file = self.client.files.upload(
            file=str(requests_path),
            config=types.UploadFileConfig(
                display_name=f"graph-extract-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
                mime_type='jsonl'
            )
        )
        self._log(f"  Uploaded: {uploaded_file.name}")

        # 创建批量任务
        self._log(f"  Creating batch job with model {self.model}...")
        batch_job = self.client.batches.create(
            model=self.model,
            src=uploaded_file.name,
            config=types.CreateBatchJobConfig(
                display_name="tavern-card-graph-extraction"
            )
        )
        job_name = batch_job.name
        self._log(f"  Job created: {job_name}")

        # 监控任务
        self._log("  Monitoring job progress...")
        while True:
            batch_job = self.client.batches.get(name=job_name)
            state = batch_job.state.name if hasattr(batch_job.state, 'name') else str(batch_job.state)

            # 获取进度
            progress = ""
            if hasattr(batch_job, 'batch_stats') and batch_job.batch_stats:
                stats = batch_job.batch_stats
                succeeded = getattr(stats, 'succeeded_request_count', 0) or 0
                total = getattr(stats, 'total_request_count', 0) or 0
                if total > 0:
                    progress = f" ({succeeded}/{total})"

            timestamp = datetime.now().strftime("%H:%M:%S")
            self._log(f"  [{timestamp}] State: {state}{progress}")

            if state in {"JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED", "JOB_STATE_CANCELLED"}:
                break

            time.sleep(30)

        # 下载结果
        if state == "JOB_STATE_SUCCEEDED":
            result_file = batch_job.dest.file_name
            self._log(f"  Downloading results from {result_file}...")
            content = self.client.files.download(file=result_file)

            if isinstance(content, bytes):
                results_path.write_bytes(content)
            elif hasattr(content, 'read'):
                results_path.write_bytes(content.read())
            else:
                results_path.write_text(str(content), encoding="utf-8")

            self._log(f"  Results saved to {results_path}")
        else:
            raise RuntimeError(f"Batch job failed with state: {state}")

    def _process_batch_results(self, results_path: Path) -> Tuple[List[Dict], List[Dict]]:
        """处理批量结果"""
        all_nodes = []
        all_edges = []

        with results_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)
                    response = data.get("response", {})

                    # 提取文本
                    text = ""
                    if "candidates" in response:
                        for candidate in response["candidates"]:
                            content = candidate.get("content", {})
                            for part in content.get("parts", []):
                                if "text" in part:
                                    text += part["text"]

                    if not text:
                        continue

                    # 解析 JSON
                    result = self._parse_json(text)
                    if not result:
                        continue

                    # 提取节点和边
                    nodes = result.get("nodes", [])
                    edges = result.get("edges", [])

                    # 验证并添加
                    for node in nodes:
                        if node.get("id") and node.get("type"):
                            all_nodes.append(node)

                    for edge in edges:
                        if edge.get("source") and edge.get("target"):
                            all_edges.append(edge)

                except Exception as e:
                    self._log(f"  Warning: Failed to parse result: {e}")

        return all_nodes, all_edges

    def _merge_nodes(self, nodes: List[Dict]) -> Tuple[List[Dict], Dict[str, str]]:
        """合并重复节点"""
        node_map: Dict[str, Dict] = {}
        name_key_map: Dict[Tuple[str, str], str] = {}
        id_alias_map: Dict[str, str] = {}

        for node in nodes:
            node_id = node.get("id", "")
            if not node_id:
                continue

            # 按 (type, name) 合并
            key = (node.get("type", ""), node.get("name", "").lower())
            if key[1] and key in name_key_map:
                canonical_id = name_key_map[key]
                id_alias_map[node_id] = canonical_id
                if canonical_id in node_map:
                    # 合并属性
                    existing = node_map[canonical_id]
                    existing["importance"] = max(
                        existing.get("importance", 0),
                        node.get("importance", 0)
                    )
                continue

            if key[1]:
                name_key_map[key] = node_id

            if node_id in node_map:
                existing = node_map[node_id]
                existing["importance"] = max(
                    existing.get("importance", 0),
                    node.get("importance", 0)
                )
            else:
                node_map[node_id] = node.copy()

        return list(node_map.values()), id_alias_map

    def _merge_edges(
        self,
        edges: List[Dict],
        id_alias_map: Dict[str, str],
        valid_node_ids: set,
    ) -> List[Dict]:
        """合并重复边"""
        edge_map: Dict[str, Dict] = {}
        edge_key_map: Dict[Tuple[str, str, str], str] = {}

        for edge in edges:
            # 应用别名
            source = id_alias_map.get(edge["source"], edge["source"])
            target = id_alias_map.get(edge["target"], edge["target"])

            # 验证节点存在
            if source not in valid_node_ids or target not in valid_node_ids:
                continue

            # 避免自环
            if source == target:
                continue

            edge = edge.copy()
            edge["source"] = source
            edge["target"] = target

            # 生成 ID
            if not edge.get("id"):
                rel = edge.get("relation", "related")
                edge["id"] = f"edge_{source[:20]}_{target[:20]}_{rel}"

            # 按 (source, target, relation) 去重
            key = (source, target, edge.get("relation", ""))
            if key in edge_key_map:
                continue
            edge_key_map[key] = edge["id"]
            edge_map[edge["id"]] = edge

        return list(edge_map.values())

    def _to_graph_data(self, nodes: List[Dict], edges: List[Dict]) -> GraphData:
        """转换为 GraphData"""
        memory_nodes = []
        for node in nodes:
            memory_nodes.append(MemoryNode(
                id=node.get("id", ""),
                type=node.get("type", "unknown"),
                name=node.get("name", ""),
                importance=float(node.get("importance", 0.5)),
                properties=node.get("properties", {}),
                created_at=datetime.now(),
                updated_at=datetime.now(),
            ))

        memory_edges = []
        for edge in edges:
            memory_edges.append(MemoryEdge(
                id=edge.get("id", ""),
                source=edge.get("source", ""),
                target=edge.get("target", ""),
                relation=edge.get("relation", "related_to"),
                weight=float(edge.get("weight", 0.5)),
                properties=edge.get("properties", {}),
                created_at=datetime.now(),
            ))

        return GraphData(nodes=memory_nodes, edges=memory_edges)

    def _extract_response_text(self, response) -> str:
        """从响应中提取文本"""
        text = ""
        if hasattr(response, 'candidates') and response.candidates:
            for part in response.candidates[0].content.parts:
                if hasattr(part, 'text') and part.text:
                    if not (hasattr(part, 'thought') and part.thought):
                        text += part.text
        return text

    def _parse_json(self, text: str) -> Optional[Dict[str, Any]]:
        """解析 JSON 响应"""
        text = text.strip()
        if not text:
            return None

        def try_parse(s: str) -> Optional[Dict]:
            try:
                result = json.loads(s)
                # 如果是列表，取第一个包含 nodes/edges 的 dict
                if isinstance(result, list):
                    for item in result:
                        if isinstance(item, dict) and ("nodes" in item or "edges" in item):
                            return item
                    return result[0] if result and isinstance(result[0], dict) else None
                return result
            except json.JSONDecodeError:
                try:
                    fixed = re.sub(r",(\s*[}\]])", r"\1", s)
                    return json.loads(fixed)
                except json.JSONDecodeError:
                    return None

        result = try_parse(text)
        if result:
            return result

        # 从代码块提取
        if "```json" in text:
            match = re.search(r'```json\s*([\s\S]*?)\s*```', text)
            if match:
                result = try_parse(match.group(1))
                if result:
                    return result

        if "```" in text:
            match = re.search(r'```\s*([\s\S]*?)\s*```', text)
            if match:
                result = try_parse(match.group(1))
                if result:
                    return result

        # 查找 JSON 对象
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            result = try_parse(match.group(0))
            if result:
                return result

        return None

    def save_graph(self, graph_data: GraphData, output_path: Path) -> None:
        """保存图谱到文件"""
        output_path.parent.mkdir(parents=True, exist_ok=True)

        def serialize(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

        data = graph_data.model_dump()
        output_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=serialize),
            encoding="utf-8",
        )
        self._log(f"\nGraph saved to: {output_path}")
        self._log(f"  Nodes: {len(graph_data.nodes)}")
        self._log(f"  Edges: {len(graph_data.edges)}")
