"""
临时脚本：从 lorabook 原始 JSON 提取第二卷第一章所需条目。

输出：data/goblin_slayer/v2/ch2_1_entries.jsonl
运行：cd backend && python -m app.extract_ch2_1
"""
import json
from pathlib import Path

# 原始 lorabook JSON
SOURCE = Path(__file__).resolve().parent.parent / "__哥布林杀手 9.30家产优化.json"
OUTPUT = Path(__file__).resolve().parent.parent / "data/goblin_slayer/v2/ch2_1_entries.jsonl"

# Ch2-1 所需条目 ID（按类型标注，方便后续 pipeline 分拣）
CH2_1_ENTRY_MAP: dict[int, str] = {
    # 角色
    0:   "character",   # 女神官
    57:  "character",   # 哥布林杀手
    80:  "character",   # 妖精弓手
    82:  "character",   # 矿人道士
    83:  "character",   # 蜥蜴僧侣
    55:  "character",   # 柜台小姐
    249: "character",   # 牧牛妹
    # 地点
    50:  "location",    # 边境小镇
    51:  "location",    # 牧场
    293: "location",    # 水之都
    # 怪物
    110: "monster",     # 怪物图鉴 - 哥布林类
    98:  "monster",     # 怪物生态 - 哥布林类
    12:  "monster",     # 种族详情 - 哥布林
    # 世界观背景
    20:  "lore",        # 世界元数据 - 四方世界
    6:   "lore",        # 世界生态系统 - 魔物层级
    # 章节叙事
    155: "chapter",     # 第二卷:第一章:冒险与日常
}


def main() -> None:
    with open(SOURCE, encoding="utf-8") as f:
        data = json.load(f)

    raw_entries: list[dict] = data["originalData"]["entries"]
    by_id = {e["id"]: e for e in raw_entries}

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    extracted = []
    missing = []
    for entry_id, entry_type in CH2_1_ENTRY_MAP.items():
        if entry_id not in by_id:
            missing.append(entry_id)
            continue
        e = by_id[entry_id]
        record = {
            "chunk_id": f"ch2_1_{entry_id}",
            "entry_type": entry_type,
            "entry_id": entry_id,
            "entry_name": e.get("comment", "").split("-")[-1].strip(),
            "entry_keys": e.get("keys", []),
            "entry_comment": e.get("comment", ""),
            "enabled": e.get("enabled", True),
            "content": e.get("content", "").strip(),
        }
        extracted.append(record)

    with open(OUTPUT, "w", encoding="utf-8") as f:
        for record in extracted:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"提取完成：{len(extracted)} 条 → {OUTPUT}")
    if missing:
        print(f"⚠ 未找到的 ID：{missing}")

    # 打印摘要
    from collections import Counter
    type_cnt = Counter(r["entry_type"] for r in extracted)
    for t, cnt in sorted(type_cnt.items()):
        entries_of_type = [r for r in extracted if r["entry_type"] == t]
        names = [r["entry_name"] for r in entries_of_type]
        print(f"  {t:12s} x{cnt}: {', '.join(names)}")


if __name__ == "__main__":
    main()
