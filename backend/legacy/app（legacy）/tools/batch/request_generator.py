"""
Phase 3: Generate Gemini Batch API request JSONL.

Creates batch request file with each entry paired with global
summary context for consistent entity ID usage.

Run:
    cd backend
    python -m app.tools.batch.request_generator \
        --entries data/goblin_slayer/entries.jsonl \
        --summary data/goblin_slayer/global_summary.json \
        --output data/goblin_slayer/batch_requests.jsonl \
        --model gemini-2.0-flash
"""
import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def load_prompt_template() -> str:
    """Load the detail extraction prompt template."""
    template_path = Path(__file__).parent / "templates" / "detail_extract_prompt.txt"
    if template_path.exists():
        return template_path.read_text(encoding="utf-8")

    # Fallback inline template
    return """你是一个世界观知识图谱提取器。

## 任务
从以下世界观条目中提取详细的图谱数据（节点和边）。

## 全局实体摘要
以下是已知的所有实体，**你必须使用这些 ID**：
{global_summary}

## 当前条目信息
- 条目关键词: {entry_keys}
- 条目分类: {entry_comment}

## 条目内容
{content}

## 输出格式（JSON）
{{
  "nodes": [
    {{
      "id": "person_女神官",
      "type": "person",
      "name": "女神官",
      "importance": 0.8,
      "properties": {{}}
    }}
  ],
  "edges": [
    {{
      "id": "edge_xxx",
      "source": "person_女神官",
      "target": "person_哥布林杀手",
      "relation": "companion",
      "weight": 0.9,
      "properties": {{}}
    }}
  ]
}}
"""


def format_summary_context(summary: Dict[str, Any], max_chars: int = 30000) -> str:
    """
    Format summary as compact context for batch requests.

    Args:
        summary: Global summary with entities and relations
        max_chars: Maximum characters for context

    Returns:
        Compact text representation
    """
    lines = []

    # Entity list
    lines.append("### 已知实体（必须使用以下 ID）")
    for entity in summary.get("entities", []):
        entity_id = entity.get("id", "")
        name = entity.get("name", "")
        entity_type = entity.get("type", "")
        aliases = entity.get("aliases", [])

        alias_str = f" 别名: {', '.join(aliases[:5])}" if aliases else ""
        lines.append(f"- `{entity_id}`: {name} [{entity_type}]{alias_str}")

    # Relation summary (condensed)
    lines.append("")
    lines.append("### 主要关系")
    relations = summary.get("key_relations", [])
    for rel in relations[:100]:  # Limit to top 100
        lines.append(f"- {rel.get('source')} --{rel.get('relation')}--> {rel.get('target')}")

    result = "\n".join(lines)

    # Truncate if too long
    if len(result) > max_chars:
        result = result[:max_chars] + "\n... (truncated)"

    return result


def build_batch_request(
    entry: Dict[str, Any],
    summary_context: str,
    prompt_template: str,
    model: str,
) -> Dict[str, Any]:
    """
    Build a single batch request.

    Args:
        entry: Parsed entry from lorebook_prep
        summary_context: Formatted global summary context
        prompt_template: Prompt template string
        model: Target model name (stored for reference, actual model set at job level)

    Returns:
        Batch API request object
    """
    # Format prompt
    entry_keys = ", ".join(entry.get("entry_keys", [])) or "无"
    entry_comment = entry.get("entry_comment", "") or "无分类"
    content = entry.get("content", "")

    prompt = prompt_template.format(
        global_summary=summary_context,
        entry_keys=entry_keys,
        entry_comment=entry_comment,
        content=content,
    )

    # Build request object (Gemini Batch API format)
    # Note: model is specified at job creation, not per-request
    # Use snake_case for config fields as per API docs
    request = {
        "key": entry.get("chunk_id", "unknown"),
        "request": {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ],
            "generation_config": {
                "response_mime_type": "application/json",
                "temperature": 0.2,
            }
        }
    }

    return request


def generate_batch_requests(
    entries_path: Path,
    summary_path: Path,
    output_path: Path,
    model: str = "gemini-2.0-flash",
    summary_context_max_chars: int = 30000,
) -> Dict[str, Any]:
    """
    Generate batch request JSONL file.

    Args:
        entries_path: Path to entries JSONL
        summary_path: Path to global summary JSON
        output_path: Output path for batch requests
        model: Target model name
        summary_context_max_chars: Max chars for summary context

    Returns:
        Generation statistics
    """
    # Load entries
    entries = []
    with entries_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    # Load summary
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    # Format summary context
    summary_context = format_summary_context(summary, summary_context_max_chars)

    # Load prompt template
    prompt_template = load_prompt_template()

    # Generate requests
    requests = []
    for entry in entries:
        request = build_batch_request(
            entry=entry,
            summary_context=summary_context,
            prompt_template=prompt_template,
            model=model,
        )
        requests.append(request)

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for request in requests:
            f.write(json.dumps(request, ensure_ascii=False))
            f.write("\n")

    # Calculate statistics
    total_prompt_chars = sum(
        len(r["request"]["contents"][0]["parts"][0]["text"])
        for r in requests
    )

    stats = {
        "total_requests": len(requests),
        "model": model,
        "summary_context_chars": len(summary_context),
        "total_prompt_chars": total_prompt_chars,
        "avg_prompt_chars": total_prompt_chars // len(requests) if requests else 0,
        "output_file": str(output_path),
    }

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Gemini Batch API request JSONL"
    )
    parser.add_argument(
        "--entries", "-e",
        required=True,
        help="Input entries JSONL file"
    )
    parser.add_argument(
        "--summary", "-s",
        required=True,
        help="Global summary JSON file"
    )
    parser.add_argument(
        "--output", "-o",
        required=True,
        help="Output batch requests JSONL file"
    )
    parser.add_argument(
        "--model", "-m",
        default="gemini-2.0-flash",
        help="Target model (default: gemini-2.0-flash)"
    )
    parser.add_argument(
        "--summary-max-chars",
        type=int,
        default=30000,
        help="Max characters for summary context (default: 30000)"
    )
    args = parser.parse_args()

    entries_path = Path(args.entries)
    summary_path = Path(args.summary)
    output_path = Path(args.output)

    if not entries_path.exists():
        print(f"Error: Entries file not found: {entries_path}")
        return
    if not summary_path.exists():
        print(f"Error: Summary file not found: {summary_path}")
        return

    stats = generate_batch_requests(
        entries_path=entries_path,
        summary_path=summary_path,
        output_path=output_path,
        model=args.model,
        summary_context_max_chars=args.summary_max_chars,
    )

    print(f"Batch request generation complete:")
    print(f"  Total requests: {stats['total_requests']}")
    print(f"  Model: {stats['model']}")
    print(f"  Summary context: {stats['summary_context_chars']:,} chars")
    print(f"  Total prompt chars: {stats['total_prompt_chars']:,}")
    print(f"  Avg prompt chars: {stats['avg_prompt_chars']:,}")
    print(f"\nOutput: {stats['output_file']}")


if __name__ == "__main__":
    main()
