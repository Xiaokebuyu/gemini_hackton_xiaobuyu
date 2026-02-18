#!/usr/bin/env python3
"""
SillyTavern (酒馆) 角色卡解析器 → Markdown 导出
支持 Character Card V2 / V3 格式，解析 PNG 内嵌的角色数据和 Lorebook 条目。

用法:
    python parse_tavern_card.py [文件或目录路径]

    不指定路径时，解析当前目录下所有 .png 文件。
    每张卡输出一个 .md 文件到同目录。
"""

import struct
import base64
import json
import sys
import os
from pathlib import Path


def extract_chunks_from_png(filepath: str) -> dict[str, str]:
    """从 PNG 文件中提取 tEXt chunk 数据，返回 {keyword: decoded_text}"""
    chunks = {}
    with open(filepath, "rb") as f:
        sig = f.read(8)
        if sig != b"\x89PNG\r\n\x1a\n":
            raise ValueError(f"不是有效的 PNG 文件: {filepath}")

        while True:
            header = f.read(8)
            if len(header) < 8:
                break
            length = struct.unpack(">I", header[:4])[0]
            chunk_type = header[4:8].decode("ascii", errors="replace")
            data = f.read(length)
            _crc = f.read(4)

            if chunk_type == "tEXt":
                null_idx = data.find(b"\x00")
                if null_idx == -1:
                    continue
                keyword = data[:null_idx].decode("ascii", errors="replace")
                text_data = data[null_idx + 1 :].decode("ascii", errors="replace")
                chunks[keyword] = text_data

    return chunks


def decode_card_json(base64_text: str) -> dict | None:
    try:
        decoded = base64.b64decode(base64_text)
        return json.loads(decoded)
    except Exception as e:
        print(f"  [解码失败] {e}")
        return None


def parse_card(filepath: str) -> dict | None:
    chunks = extract_chunks_from_png(filepath)
    for key in ("ccv3", "chara"):
        if key in chunks:
            card = decode_card_json(chunks[key])
            if card:
                card["_source_chunk"] = key
                return card
    return None


def get_data(card: dict) -> dict:
    return card.get("data", card)


def get_character_book(card: dict) -> dict | None:
    data = get_data(card)
    if "character_book" in data:
        return data["character_book"]
    extensions = data.get("extensions", {})
    if "character_book" in extensions:
        return extensions["character_book"]
    if "character_book" in card:
        return card["character_book"]
    return None


def clean_text(text: str) -> str:
    """统一换行符"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def card_to_markdown(filepath: str, card: dict) -> str:
    """将整张角色卡转为 Markdown 字符串"""
    data = get_data(card)
    spec = card.get("spec", data.get("spec", "unknown"))
    version = card.get("spec_version", data.get("spec_version", "?"))
    source = card.get("_source_chunk", "?")
    lines = []

    name = data.get("name", "未知角色")
    lines.append(f"# {name}")
    lines.append("")
    lines.append(f"> 文件: `{os.path.basename(filepath)}` | 规格: {spec} v{version} | chunk: {source}")

    create_date = card.get("create_date", data.get("create_date", ""))
    if create_date:
        lines.append(f"> 创建日期: {create_date}")
    lines.append("")

    # ── 基本信息 ──
    lines.append("## 基本信息")
    lines.append("")
    lines.append(f"| 字段 | 值 |")
    lines.append(f"|---|---|")
    lines.append(f"| 名称 | {name} |")
    lines.append(f"| 描述 | {data.get('description', '')} |")
    lines.append(f"| 创建者 | {data.get('creator', '') or '(未填写)'} |")
    lines.append(f"| 版本 | {data.get('character_version', '') or '(未填写)'} |")

    tags = data.get("tags", [])
    if tags:
        lines.append(f"| 标签 | {', '.join(str(t) for t in tags)} |")
    lines.append("")

    # ── 文本字段 ──
    text_fields = [
        ("personality", "人格/性格"),
        ("scenario", "场景"),
        ("first_mes", "开场白"),
        ("mes_example", "示例对话"),
        ("system_prompt", "系统提示"),
        ("post_history_instructions", "历史后指令"),
    ]

    for field, label in text_fields:
        value = data.get(field, "")
        if value and value.strip():
            content = clean_text(value).strip()
            lines.append(f"## {label}")
            lines.append("")
            lines.append("```")
            lines.append(content)
            lines.append("```")
            lines.append("")

    # ── 替代开场白 ──
    alt_greetings = data.get("alternate_greetings", [])
    if alt_greetings:
        lines.append(f"## 替代开场白 ({len(alt_greetings)} 条)")
        lines.append("")
        for i, g in enumerate(alt_greetings):
            lines.append(f"### 开场白 {i + 1}")
            lines.append("")
            lines.append("```")
            lines.append(clean_text(g).strip())
            lines.append("```")
            lines.append("")

    # ── 扩展字段 ──
    extensions = data.get("extensions", {})
    depth_prompt = extensions.get("depth_prompt", {})
    other_ext = {k: v for k, v in extensions.items() if k not in ("depth_prompt", "character_book")}

    if depth_prompt and depth_prompt.get("prompt"):
        lines.append("## Depth Prompt")
        lines.append("")
        lines.append(f"- depth: `{depth_prompt.get('depth')}`")
        lines.append(f"- role: `{depth_prompt.get('role')}`")
        lines.append("")
        lines.append("```")
        lines.append(clean_text(depth_prompt["prompt"]).strip())
        lines.append("```")
        lines.append("")

    if other_ext:
        lines.append("## 扩展字段")
        lines.append("")
        for k, v in other_ext.items():
            lines.append(f"- **{k}**: `{v}`")
        lines.append("")

    # ── Lorebook ──
    book = get_character_book(card)
    if book:
        entries = book.get("entries", [])
        lines.append("---")
        lines.append("")
        lines.append("## Lorebook / 世界书")
        lines.append("")

        # 元数据
        meta_items = []
        book_name = book.get("name", "")
        if book_name:
            meta_items.append(f"- 书名: {book_name}")
        book_desc = book.get("description", "")
        if book_desc:
            meta_items.append(f"- 描述: {book_desc}")
        scan_depth = book.get("scan_depth", book.get("extensions", {}).get("scan_depth"))
        if scan_depth is not None:
            meta_items.append(f"- 扫描深度: `{scan_depth}`")
        token_budget = book.get("token_budget", book.get("extensions", {}).get("token_budget"))
        if token_budget is not None:
            meta_items.append(f"- Token 预算: `{token_budget}`")
        recursive = book.get("recursive_scanning")
        if recursive is not None:
            meta_items.append(f"- 递归扫描: {'是' if recursive else '否'}")

        enabled_count = sum(1 for e in entries if e.get("enabled", True))
        constant_count = sum(1 for e in entries if e.get("constant", False))
        meta_items.append(f"- 条目总数: **{len(entries)}** (启用 {enabled_count}, 常驻 {constant_count})")

        for m in meta_items:
            lines.append(m)
        lines.append("")

        # 每条 entry
        position_map = {
            0: "before_char (角色定义前)",
            1: "after_char (角色定义后)",
            2: "before_example (示例对话前)",
            3: "after_example (示例对话后)",
            4: "before_system (系统提示前)",
            5: "after_system (系统提示后)",
            6: "at_depth (按深度插入)",
        }

        for i, entry in enumerate(entries):
            comment = entry.get("comment", entry.get("name", ""))
            heading = f"条目 #{i}"
            if comment:
                heading += f" — {comment}"
            lines.append(f"### {heading}")
            lines.append("")

            # 属性表
            uid = entry.get("id", entry.get("uid", "N/A"))
            enabled = entry.get("enabled", True)
            keys = entry.get("keys", entry.get("key", []))
            if isinstance(keys, list):
                keys_str = ", ".join(f"`{k}`" for k in keys)
            else:
                keys_str = f"`{keys}`"
            secondary = entry.get("secondary_keys", [])
            if isinstance(secondary, list):
                sec_str = ", ".join(f"`{k}`" for k in secondary) if secondary else ""
            else:
                sec_str = f"`{secondary}`" if secondary else ""
            selective = entry.get("selective", False)
            constant = entry.get("constant", False)
            position = entry.get("position", "N/A")
            pos_label = position_map.get(position, str(position))

            ext = entry.get("extensions", {})
            depth = ext.get("depth", entry.get("depth"))
            role = ext.get("role", entry.get("role"))
            priority = entry.get("priority", entry.get("order"))
            insertion_order = entry.get("insertion_order")
            case_sensitive = entry.get("case_sensitive")

            lines.append("| 属性 | 值 |")
            lines.append("|---|---|")
            lines.append(f"| ID | `{uid}` |")
            lines.append(f"| 启用 | {'✅' if enabled else '❌'} |")
            lines.append(f"| 主关键词 | {keys_str} |")
            if sec_str:
                lines.append(f"| 副关键词 | {sec_str} |")
            lines.append(f"| 选择性 | {'是' if selective else '否'} |")
            lines.append(f"| 常驻 | {'是' if constant else '否'} |")
            lines.append(f"| 位置 | {pos_label} |")
            if depth is not None:
                lines.append(f"| 深度 | `{depth}` |")
            if role is not None:
                lines.append(f"| 角色 | `{role}` |")
            if priority is not None:
                lines.append(f"| 优先级 | `{priority}` |")
            if insertion_order is not None:
                lines.append(f"| 插入顺序 | `{insertion_order}` |")
            if case_sensitive is not None:
                lines.append(f"| 大小写敏感 | {'是' if case_sensitive else '否'} |")
            lines.append("")

            # 内容（完整输出）
            content = entry.get("content", "")
            if content:
                lines.append("**内容:**")
                lines.append("")
                lines.append("```")
                lines.append(clean_text(content).strip())
                lines.append("```")
            else:
                lines.append("**内容:** (空)")
            lines.append("")
    else:
        lines.append("---")
        lines.append("")
        lines.append("*此角色卡无 Lorebook 数据*")
        lines.append("")

    return "\n".join(lines)


def main():
    args = sys.argv[1:]
    args = [a for a in args if not a.startswith("--")]

    target = args[0] if args else "."
    target_path = Path(target)

    png_files = []
    if target_path.is_file():
        png_files.append(str(target_path))
    elif target_path.is_dir():
        png_files = sorted(str(p) for p in target_path.glob("*.png"))
    else:
        print(f"路径不存在: {target}")
        sys.exit(1)

    if not png_files:
        print(f"在 {target} 中未找到 PNG 文件")
        sys.exit(1)

    print(f"找到 {len(png_files)} 个 PNG 文件")

    parsed_count = 0
    for filepath in png_files:
        try:
            card = parse_card(filepath)
        except Exception as e:
            print(f"[跳过] {filepath}: {e}")
            continue

        if card is None:
            print(f"[跳过] {filepath}: 未找到角色卡数据")
            continue

        parsed_count += 1

        md = card_to_markdown(filepath, card)

        # 输出文件名：角色名.md
        data = get_data(card)
        safe_name = data.get("name", "unknown")
        for ch in '/\\:*?"<>|':
            safe_name = safe_name.replace(ch, "_")
        out_dir = os.path.dirname(filepath) or "."
        out_path = os.path.join(out_dir, f"{safe_name}.md")

        with open(out_path, "w", encoding="utf-8") as f:
            f.write(md)

        print(f"  ✓ {os.path.basename(filepath)} → {out_path}")

    print(f"\n共解析 {parsed_count}/{len(png_files)} 张角色卡")


if __name__ == "__main__":
    main()
