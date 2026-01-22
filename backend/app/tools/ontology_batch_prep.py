"""
Prepare JSONL batches for ontology extraction.

Run:
    cd backend
    python -m app.tools.ontology_batch_prep --input data.txt --output chunks.jsonl --source world_doc
    python -m app.tools.ontology_batch_prep --input data.txt --output prompts.jsonl --template app/tools/templates/ontology_extract_prompt.txt
"""
import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional


def _normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _split_paragraphs(text: str) -> List[str]:
    paragraphs = []
    for para in text.split("\n\n"):
        para = para.strip()
        if para:
            paragraphs.append(para)
    return paragraphs


def _select_overlap(paragraphs: List[str], overlap_chars: int) -> List[str]:
    if overlap_chars <= 0:
        return []
    selected: List[str] = []
    total = 0
    for para in reversed(paragraphs):
        para_len = len(para) + 2
        if selected and total + para_len > overlap_chars:
            break
        selected.append(para)
        total += para_len
        if total >= overlap_chars:
            break
    return list(reversed(selected))


def _split_large_paragraph(para: str, chunk_size: int) -> List[str]:
    if chunk_size <= 0:
        return [para]
    parts = []
    start = 0
    while start < len(para):
        end = min(start + chunk_size, len(para))
        parts.append(para[start:end])
        start = end
    return parts


def chunk_text(
    text: str,
    chunk_size: int,
    overlap_chars: int,
) -> List[str]:
    paragraphs = _split_paragraphs(text)
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    def flush_current() -> None:
        nonlocal current, current_len
        if not current:
            return
        chunk = "\n\n".join(current).strip()
        if chunk:
            chunks.append(chunk)
        current = []
        current_len = 0

    for para in paragraphs:
        para_len = len(para)
        if para_len > chunk_size > 0:
            flush_current()
            for part in _split_large_paragraph(para, chunk_size):
                if part.strip():
                    chunks.append(part.strip())
            continue

        tentative_len = current_len + para_len + (2 if current else 0)
        if chunk_size > 0 and tentative_len > chunk_size:
            flush_current()
            overlap = _select_overlap(chunks[-1].split("\n\n"), overlap_chars) if chunks else []
            current = overlap[:]
            current_len = sum(len(p) + 2 for p in current)

        current.append(para)
        current_len += para_len + (2 if current_len else 0)

    flush_current()
    return chunks


def build_payloads(
    chunks: List[str],
    source: str,
    template: Optional[str],
) -> List[Dict]:
    payloads = []
    for index, chunk in enumerate(chunks, start=1):
        chunk_id = f"chunk_{index:04d}"
        payload = {
            "chunk_id": chunk_id,
            "source": source,
            "text": chunk,
        }
        if template:
            payload["prompt"] = template.format(
                chunk_id=chunk_id,
                source=source,
                text=chunk,
            )
        payloads.append(payload)
    return payloads


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare ontology batch JSONL")
    parser.add_argument("--input", required=True, help="Input text file")
    parser.add_argument("--output", required=True, help="Output JSONL path")
    parser.add_argument("--source", default="world_doc", help="Source name")
    parser.add_argument("--chunk-size", type=int, default=1200, help="Chunk size in characters")
    parser.add_argument("--overlap", type=int, default=0, help="Overlap size in characters")
    parser.add_argument("--template", default=None, help="Prompt template file path")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    template_text = None
    if args.template:
        template_text = Path(args.template).read_text(encoding="utf-8")

    text = _normalize_text(input_path.read_text(encoding="utf-8"))
    chunks = chunk_text(text, args.chunk_size, args.overlap)
    payloads = build_payloads(chunks, args.source, template_text)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        for payload in payloads:
            file.write(json.dumps(payload, ensure_ascii=False))
            file.write("\n")

    print(f"Wrote {len(payloads)} chunks to {output_path}")


if __name__ == "__main__":
    main()
