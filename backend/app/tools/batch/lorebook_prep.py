"""
Phase 1: Lorebook JSON parsing.

Parses SillyTavern/TavernAI Lorebook (World Info) format into
processable chunks for batch extraction.

Run:
    cd backend
    python -m app.tools.batch.lorebook_prep \
        --input data/worldbook.json \
        --output-dir data/goblin_slayer/
"""
import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def parse_lorebook(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Parse SillyTavern Lorebook JSON format.

    Args:
        data: Raw Lorebook JSON data

    Returns:
        List of parsed entries with keys, comment, and content
    """
    entries = []

    # Handle both formats: entries as dict or list
    raw_entries = data.get("entries", {})
    if isinstance(raw_entries, dict):
        # Format: {"0": {...}, "1": {...}}
        items = sorted(raw_entries.items(), key=lambda x: int(x[0]) if x[0].isdigit() else x[0])
        raw_entries = [entry for _, entry in items]

    for idx, entry in enumerate(raw_entries):
        # Extract relevant fields
        keys = entry.get("key", [])
        if isinstance(keys, str):
            keys = [keys]

        secondary_keys = entry.get("keysecondary", [])
        if isinstance(secondary_keys, str):
            secondary_keys = [secondary_keys]

        all_keys = [k for k in keys + secondary_keys if k]

        comment = entry.get("comment", "")
        content = entry.get("content", "")

        # Skip empty entries
        if not content.strip():
            continue

        parsed = {
            "chunk_id": f"entry_{idx}",
            "entry_keys": all_keys,
            "entry_comment": comment,
            "content": content.strip(),
            # Metadata for debugging
            "original_index": str(idx),
            "constant": entry.get("constant", False),
            "selective": entry.get("selective", False),
        }
        entries.append(parsed)

    return entries


def format_worldbook_markdown(entries: List[Dict[str, Any]]) -> str:
    """
    Format entries as markdown for global summary generation.

    Args:
        entries: Parsed entries from parse_lorebook

    Returns:
        Markdown formatted text of all entries
    """
    sections = []

    for entry in entries:
        keys = entry.get("entry_keys", [])
        comment = entry.get("entry_comment", "")
        content = entry.get("content", "")

        # Build section header
        header_parts = []
        if comment:
            header_parts.append(comment)
        if keys:
            header_parts.append(f"关键词: {', '.join(keys)}")

        header = " | ".join(header_parts) if header_parts else f"条目 {entry['chunk_id']}"

        section = f"""## {header}

{content}

---
"""
        sections.append(section)

    return "\n".join(sections)


def estimate_tokens(text: str) -> int:
    """
    Rough token estimation (Chinese text ~1.5 chars/token).

    Args:
        text: Input text

    Returns:
        Estimated token count
    """
    # Rough estimation: Chinese ~1.5 chars/token, English ~4 chars/token
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.5 + other_chars / 4)


def split_large_entry(entry: Dict[str, Any], max_chars: int = 4000) -> List[Dict[str, Any]]:
    """
    Split entries with content exceeding max_chars.

    Args:
        entry: Single parsed entry
        max_chars: Maximum characters per chunk

    Returns:
        List of entries (possibly split)
    """
    content = entry.get("content", "")
    if len(content) <= max_chars:
        return [entry]

    # Split by paragraphs first
    paragraphs = content.split("\n\n")
    chunks = []
    current_chunk = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para)
        if current_len + para_len > max_chars and current_chunk:
            # Save current chunk
            chunks.append("\n\n".join(current_chunk))
            current_chunk = []
            current_len = 0

        if para_len > max_chars:
            # Split large paragraph by lines
            if current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = []
                current_len = 0

            lines = para.split("\n")
            line_chunk = []
            line_len = 0
            for line in lines:
                if line_len + len(line) > max_chars and line_chunk:
                    chunks.append("\n".join(line_chunk))
                    line_chunk = []
                    line_len = 0
                line_chunk.append(line)
                line_len += len(line) + 1
            if line_chunk:
                chunks.append("\n".join(line_chunk))
        else:
            current_chunk.append(para)
            current_len += para_len + 2

    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    # Create split entries
    result = []
    for i, chunk_content in enumerate(chunks):
        split_entry = entry.copy()
        split_entry["chunk_id"] = f"{entry['chunk_id']}_part{i+1}"
        split_entry["content"] = chunk_content
        split_entry["is_split"] = True
        split_entry["split_index"] = i
        split_entry["total_splits"] = len(chunks)
        result.append(split_entry)

    return result


def process_lorebook(
    input_path: Path,
    output_dir: Path,
    max_entry_chars: int = 4000,
) -> Dict[str, Any]:
    """
    Main processing function.

    Args:
        input_path: Path to Lorebook JSON file
        output_dir: Output directory
        max_entry_chars: Maximum characters per entry

    Returns:
        Processing statistics
    """
    # Load and parse
    data = json.loads(input_path.read_text(encoding="utf-8"))
    entries = parse_lorebook(data)

    # Split large entries
    processed_entries = []
    split_count = 0
    for entry in entries:
        split_entries = split_large_entry(entry, max_entry_chars)
        if len(split_entries) > 1:
            split_count += 1
        processed_entries.extend(split_entries)

    # Generate outputs
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Full markdown text for global summary
    worldbook_md = format_worldbook_markdown(processed_entries)
    md_path = output_dir / "worldbook_full.md"
    md_path.write_text(worldbook_md, encoding="utf-8")

    # 2. JSONL entries for batch processing
    jsonl_path = output_dir / "entries.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for entry in processed_entries:
            f.write(json.dumps(entry, ensure_ascii=False))
            f.write("\n")

    # Calculate statistics
    total_chars = sum(len(e.get("content", "")) for e in processed_entries)
    estimated_tokens = estimate_tokens(worldbook_md)

    stats = {
        "input_file": str(input_path),
        "original_entries": len(entries),
        "processed_entries": len(processed_entries),
        "split_entries": split_count,
        "total_characters": total_chars,
        "estimated_tokens": estimated_tokens,
        "within_1m_context": estimated_tokens < 1_000_000,
        "outputs": {
            "worldbook_full": str(md_path),
            "entries_jsonl": str(jsonl_path),
        }
    }

    # Save stats
    stats_path = output_dir / "prep_stats.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse SillyTavern Lorebook JSON for batch processing"
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="Input Lorebook JSON file"
    )
    parser.add_argument(
        "--output-dir", "-o",
        required=True,
        help="Output directory for processed files"
    )
    parser.add_argument(
        "--max-entry-chars",
        type=int,
        default=4000,
        help="Maximum characters per entry (default: 4000)"
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)

    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        return

    stats = process_lorebook(
        input_path=input_path,
        output_dir=output_dir,
        max_entry_chars=args.max_entry_chars,
    )

    print(f"Lorebook parsing complete:")
    print(f"  Original entries: {stats['original_entries']}")
    print(f"  Processed entries: {stats['processed_entries']}")
    print(f"  Split entries: {stats['split_entries']}")
    print(f"  Total characters: {stats['total_characters']:,}")
    print(f"  Estimated tokens: {stats['estimated_tokens']:,}")
    print(f"  Within 1M context: {'Yes' if stats['within_1m_context'] else 'No (may need compression)'}")
    print(f"\nOutputs:")
    print(f"  - {stats['outputs']['worldbook_full']}")
    print(f"  - {stats['outputs']['entries_jsonl']}")


if __name__ == "__main__":
    main()
