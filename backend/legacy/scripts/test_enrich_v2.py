"""
验证脚本：测试单章节的 v2 enrichment 效果。

用法：
    python3 scripts/test_enrich_v2.py [chapter_index]
    # chapter_index 默认 0（第一章）

输出：
    tmp/enrich_v2_test_{chapter_id}.json  ← LLM 产出的完整结构化数据
"""
import asyncio
import json
import sys
from pathlib import Path

# 配置
LOREBOOK_PATH = Path("__哥布林杀手 9.30家产优化.json")
STRUCTURED_DIR = Path("data/goblin_slayer/structured_new")
OUTPUT_DIR = Path("tmp")
CHAPTER_INDEX = int(sys.argv[1]) if len(sys.argv) > 1 else 0
MODEL = sys.argv[2] if len(sys.argv) > 2 else "gemini-3-flash-preview"

# 添加项目根到 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.tools.worldbook_graphizer.unified_pipeline import UnifiedWorldExtractor
from app.tools.worldbook_graphizer.tavern_card_parser import TavernCardParser
from app.tools.worldbook_graphizer.models import MapsData, MapInfo, CharactersData, CharacterInfo, NPCTier


async def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    # ── 1. 解析 lorebook，找章节条目 ──
    print(f"Loading lorebook: {LOREBOOK_PATH}")
    parser = TavernCardParser()
    data = parser.parse(LOREBOOK_PATH)

    story_entries = [
        e for e in data.entries
        if e.entry_type and ("章" in e.comment and "卷" in e.comment)
    ]
    story_entries.sort(key=lambda e: e.order)
    print(f"Found {len(story_entries)} story chapter entries")

    if CHAPTER_INDEX >= len(story_entries):
        print(f"Error: chapter_index {CHAPTER_INDEX} out of range (max {len(story_entries)-1})")
        return

    entry = story_entries[CHAPTER_INDEX]
    print(f"\n=== Testing chapter [{CHAPTER_INDEX}]: {entry.comment} ===")
    print(f"Content length: {len(entry.content)} chars")
    print(f"Content preview: {entry.content[:200]}...")

    # ── 2. 构建最小章节 dict（模拟 Phase 1 输出）──
    ch_id = f"ch_test_{CHAPTER_INDEX}"
    chapter = {
        "id": ch_id,
        "mainline_id": "vol_1",
        "name": entry.comment.strip(),
        "type": "story",
        "description": entry.content[:500],
        "_full_content": entry.content,    # 完整内容，不截断
        "_source_entry_uid": entry.uid,
        "available_maps": [],
        "objectives": [],
        "trigger_conditions": {},
        "completion_conditions": {},
        "events": [],
        "transitions": [],
        "pacing": {},
    }

    # ── 3. 加载已有的 maps 和 characters ──
    maps_data = MapsData(maps=[])
    maps_path = STRUCTURED_DIR / "maps.json"
    if maps_path.exists():
        raw = json.loads(maps_path.read_text(encoding="utf-8"))
        maps_list = raw if isinstance(raw, list) else raw.get("maps", [])
        maps_data = MapsData(maps=[
            MapInfo(
                id=m.get("id", ""),
                name=m.get("name", ""),
                description=m.get("description", ""),
                sub_locations=m.get("sub_locations", []),
                connections=m.get("connections", []),
            )
            for m in maps_list if m.get("id")
        ])
        print(f"\nLoaded {len(maps_data.maps)} maps from {maps_path}")
    else:
        print(f"\nWarning: {maps_path} not found, running without maps context")

    chars_data = None
    chars_path = STRUCTURED_DIR / "characters.json"
    if chars_path.exists():
        raw = json.loads(chars_path.read_text(encoding="utf-8"))
        chars_list = raw if isinstance(raw, list) else raw.get("characters", [])
        chars_data = CharactersData(characters=[
            CharacterInfo(
                id=c.get("id", ""),
                name=c.get("name", ""),
                tier=NPCTier(c.get("tier", "secondary")),
            )
            for c in chars_list if c.get("id")
        ])
        print(f"Loaded {len(chars_data.characters)} NPCs from {chars_path}")

    # ── 4. 运行 v2 enrichment（direct 模式，实时返回）──
    print(f"\n=== Running _enrich_chapters_v2 (direct mode) ===")
    print(f"Using model: {MODEL}")
    extractor = UnifiedWorldExtractor(model=MODEL, verbose=True)

    chapters = await extractor._enrich_chapters_v2(
        chapters=[chapter],
        maps_data=maps_data,
        chars_data=chars_data,
        output_dir=OUTPUT_DIR,
        use_direct=True,  # 直接调用，实时返回
    )

    result = chapters[0] if chapters else chapter

    # ── 5. 输出结果 ──
    out_path = OUTPUT_DIR / f"enrich_v2_test_{ch_id}.json"
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n=== Result saved to: {out_path} ===")

    # 摘要
    events = result.get("events", [])
    print(f"\navailable_maps: {result.get('available_maps', [])}")
    print(f"objectives: {result.get('objectives', [])}")
    print(f"events: {len(events)} 个")
    for i, evt in enumerate(events):
        print(f"  [{i+1}] {evt.get('id')} | {evt.get('name')} | activation={evt.get('activation_type')} | importance={evt.get('importance')}")
        if evt.get("completion_conditions"):
            print(f"       completion_conditions: {json.dumps(evt['completion_conditions'], ensure_ascii=False)[:100]}")
        if evt.get("on_complete"):
            print(f"       on_complete: {json.dumps(evt['on_complete'], ensure_ascii=False)[:100]}")
        if evt.get("stages"):
            print(f"       stages: {len(evt['stages'])} 个")
        if evt.get("outcomes"):
            print(f"       outcomes: {list(evt['outcomes'].keys())}")
    print(f"\ntransitions: {len(result.get('transitions', []))} 个")


if __name__ == "__main__":
    asyncio.run(main())
