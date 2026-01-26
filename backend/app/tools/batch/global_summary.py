"""
Phase 2: Global summary generation using 1M context.

Uses Gemini Flash's large context window to analyze the complete
worldbook and generate a global entity/relation summary.

Run:
    cd backend
    python -m app.tools.batch.global_summary \
        --input data/goblin_slayer/worldbook_full.md \
        --output data/goblin_slayer/global_summary.json \
        --model gemini-2.0-flash
"""
import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

from google import genai
from google.genai import types

from app.config import settings


def _fix_json_string(text: str) -> str:
    """
    Attempt to fix common JSON issues from LLM output.

    Args:
        text: Potentially malformed JSON string

    Returns:
        Fixed JSON string
    """
    # Remove trailing commas before ] or }
    text = re.sub(r',(\s*[}\]])', r'\1', text)
    return text


def load_prompt_template() -> str:
    """Load the global summary prompt template."""
    template_path = Path(__file__).parent / "templates" / "global_summary_prompt.txt"
    if template_path.exists():
        return template_path.read_text(encoding="utf-8")

    # Fallback inline template
    return """你是一个世界观分析专家。请分析以下完整的世界书，提取所有实体和关键关系。

## 任务
1. 识别所有实体（人物、地点、组织、物品、种族、概念等）
2. 为每个实体分配确定性 ID（格式：{type}_{主要名称}）
3. 收集每个实体的所有别名/关键词
4. 识别实体间的主要关系

## 输出格式（JSON）
{{
  "entities": [
    {{
      "id": "person_女神官",
      "type": "person",
      "name": "女神官",
      "aliases": ["小神官", "地母神信徒"],
      "brief": "简短描述"
    }}
  ],
  "key_relations": [
    {{"source": "entity_id", "target": "entity_id", "relation": "关系类型"}}
  ]
}}

## 世界书内容
{worldbook_content}
"""


def generate_global_summary(
    worldbook_content: str,
    model: str = "gemini-2.0-flash",
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate global entity summary from worldbook content.

    Args:
        worldbook_content: Full worldbook markdown text
        model: Gemini model to use
        api_key: API key (uses settings if not provided)

    Returns:
        Parsed summary with entities and relations
    """
    client = genai.Client(api_key=api_key or settings.gemini_api_key)

    # Build prompt
    template = load_prompt_template()
    prompt = template.format(worldbook_content=worldbook_content)

    # Configure for JSON output
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.2,
    )

    # Generate
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=config,
    )

    # Extract text
    text = ""
    if hasattr(response, 'candidates') and response.candidates:
        for part in response.candidates[0].content.parts:
            if hasattr(part, 'text') and part.text:
                if not (hasattr(part, 'thought') and part.thought):
                    text += part.text

    # Parse JSON with error recovery
    text = text.strip()

    def try_parse(s: str) -> Optional[Dict]:
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            try:
                return json.loads(_fix_json_string(s))
            except json.JSONDecodeError:
                return None

    # Try direct parse
    result = try_parse(text)
    if result:
        return result

    # Try extracting from code block
    if "```json" in text:
        json_start = text.find("```json") + 7
        json_end = text.find("```", json_start)
        if json_end > json_start:
            result = try_parse(text[json_start:json_end].strip())
            if result:
                return result
    elif "```" in text:
        json_start = text.find("```") + 3
        json_end = text.find("```", json_start)
        if json_end > json_start:
            result = try_parse(text[json_start:json_end].strip())
            if result:
                return result

    # Try finding JSON object
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        result = try_parse(match.group(0))
        if result:
            return result

    raise ValueError(f"Failed to parse JSON response.\nResponse preview: {text[:500]}...")


def validate_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and clean up the summary.

    Args:
        summary: Raw summary from LLM

    Returns:
        Validated summary with stats
    """
    entities = summary.get("entities", [])
    relations = summary.get("key_relations", [])

    # Build entity ID set
    entity_ids = {e.get("id") for e in entities if e.get("id")}

    # Validate relations
    valid_relations = []
    invalid_relations = []
    for rel in relations:
        source = rel.get("source")
        target = rel.get("target")
        if source in entity_ids and target in entity_ids:
            valid_relations.append(rel)
        else:
            invalid_relations.append(rel)

    # Count by type
    type_counts = {}
    for e in entities:
        t = e.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    # Build alias map (for quick lookup)
    alias_map = {}
    for e in entities:
        entity_id = e.get("id")
        if entity_id:
            for alias in e.get("aliases", []):
                if alias:
                    alias_map[alias.lower()] = entity_id
            # Also map the name
            name = e.get("name")
            if name:
                alias_map[name.lower()] = entity_id

    stats = {
        "entity_count": len(entities),
        "relation_count": len(valid_relations),
        "invalid_relations": len(invalid_relations),
        "type_distribution": type_counts,
        "alias_count": len(alias_map),
    }

    return {
        "entities": entities,
        "key_relations": valid_relations,
        "alias_map": alias_map,
        "stats": stats,
    }


def format_summary_for_context(summary: Dict[str, Any], max_tokens: int = 20000) -> str:
    """
    Format summary as compact text for batch request context.

    Args:
        summary: Validated summary
        max_tokens: Maximum tokens for output

    Returns:
        Compact text representation
    """
    lines = ["已知实体列表（必须使用以下 ID）："]

    for entity in summary.get("entities", []):
        entity_id = entity.get("id", "")
        name = entity.get("name", "")
        entity_type = entity.get("type", "")
        aliases = entity.get("aliases", [])

        alias_str = f" ({', '.join(aliases[:3])})" if aliases else ""
        lines.append(f"- {entity_id}: {name}{alias_str} [{entity_type}]")

    lines.append("")
    lines.append("关键关系：")
    for rel in summary.get("key_relations", [])[:50]:  # Limit relations
        lines.append(f"- {rel.get('source')} --[{rel.get('relation')}]--> {rel.get('target')}")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate global entity summary from worldbook"
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="Input worldbook markdown file"
    )
    parser.add_argument(
        "--output", "-o",
        required=True,
        help="Output summary JSON file"
    )
    parser.add_argument(
        "--model", "-m",
        default="gemini-2.0-flash",
        help="Gemini model to use (default: gemini-2.0-flash)"
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        return

    print(f"Loading worldbook from {input_path}...")
    worldbook_content = input_path.read_text(encoding="utf-8")
    print(f"  Content size: {len(worldbook_content):,} characters")

    print(f"Generating global summary with {args.model}...")
    raw_summary = generate_global_summary(
        worldbook_content=worldbook_content,
        model=args.model,
    )

    print("Validating summary...")
    summary = validate_summary(raw_summary)

    # Save output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # Also save compact context version
    context_path = output_path.parent / "summary_context.txt"
    context_text = format_summary_for_context(summary)
    context_path.write_text(context_text, encoding="utf-8")

    print(f"\nGlobal summary complete:")
    print(f"  Entities: {summary['stats']['entity_count']}")
    print(f"  Relations: {summary['stats']['relation_count']}")
    if summary['stats']['invalid_relations'] > 0:
        print(f"  Invalid relations (removed): {summary['stats']['invalid_relations']}")
    print(f"  Type distribution: {summary['stats']['type_distribution']}")
    print(f"\nOutputs:")
    print(f"  - {output_path}")
    print(f"  - {context_path}")


if __name__ == "__main__":
    main()
