"""
V2 数据管线 — 从 lorabook 散文提取结构化游戏数据。

运行方式：
    cd backend
    PYTHONPATH=. python3 -m app.v2_data_pipeline                 # Batch API（默认）
    PYTHONPATH=. python3 -m app.v2_data_pipeline --mode immediate # 逐条直接调用

环境变量：
    GEMINI_API_KEY 或 GOOGLE_API_KEY
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

# ─── 常量 ───────────────────────────────────────────────────────────────────

ENTRIES_PATH = Path("data/goblin_slayer/v2/ch2_1_entries.jsonl")
OUTPUT_DIR   = Path("data/goblin_slayer/v2")
BATCH_TEMP   = OUTPUT_DIR / "batch_temp"
MODEL        = "gemini-3-flash-preview"

# ─── BatchRunner ─────────────────────────────────────────────────────────────


class BatchRunner:
    """Gemini Batch API 执行器（按设计文档 §5.1 实现）。"""

    def __init__(self, model: str, api_key: str) -> None:
        from google import genai
        self.model = model
        self.client = genai.Client(api_key=api_key)

    def run_batch(
        self,
        requests: list[tuple[str, str]],
        temp_dir: Path,
        display_name: str,
        temperature: float = 0.7,
        response_mime_type: str = "application/json",
        max_output_tokens: int = 65536,
    ) -> dict[str, str]:
        """提交 batch job 并等待完成，返回 {key: raw_json_text}。"""
        from google.genai import types

        temp_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")

        # 1. 写 JSONL 请求文件
        req_path = temp_dir / f"batch_{display_name}_{ts}.jsonl"
        with req_path.open("w", encoding="utf-8") as f:
            for key, prompt in requests:
                line = {
                    "key": key,
                    "request": {
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generation_config": {
                            "response_mime_type": response_mime_type,
                            "temperature": temperature,
                            "max_output_tokens": max_output_tokens,
                        },
                    },
                }
                f.write(json.dumps(line, ensure_ascii=False) + "\n")

        # 2. 上传
        uploaded = self.client.files.upload(
            file=str(req_path),
            config=types.UploadFileConfig(
                display_name=f"{display_name}-{ts}",
                mime_type="jsonl",
            ),
        )

        # 3. 创建 batch job
        job = self.client.batches.create(
            model=self.model,
            src=uploaded.name,
            config=types.CreateBatchJobConfig(display_name=display_name),
        )
        print(f"[{display_name}] Batch job created: {job.name}")

        # 4. 轮询
        while True:
            job = self.client.batches.get(name=job.name)
            state = job.state.name if hasattr(job.state, "name") else str(job.state)
            stats = getattr(job, "batch_stats", None)
            progress = ""
            if stats:
                s = getattr(stats, "succeeded_request_count", 0) or 0
                t = getattr(stats, "total_request_count", 0) or 0
                if t:
                    progress = f" ({s}/{t})"
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] {state}{progress}")
            if state in {"JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED", "JOB_STATE_CANCELLED"}:
                break
            time.sleep(30)

        if state != "JOB_STATE_SUCCEEDED":
            raise RuntimeError(f"Batch job {display_name} failed: {state}")

        # 5. 下载结果
        res_path = temp_dir / f"batch_{display_name}_{ts}_results.jsonl"
        content = self.client.files.download(file=job.dest.file_name)
        if isinstance(content, bytes):
            res_path.write_bytes(content)
        elif hasattr(content, "read"):
            res_path.write_bytes(content.read())
        else:
            res_path.write_text(str(content), encoding="utf-8")

        # 6. 解析
        results: dict[str, str] = {}
        for line in res_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            key = data.get("key", "")
            text = ""
            for cand in data.get("response", {}).get("candidates", []):
                for part in cand.get("content", {}).get("parts", []):
                    if "text" in part:
                        text += part["text"]
            if key and text:
                results[key] = text

        print(f"  [{display_name}] Parsed {len(results)}/{len(requests)} results")
        return results

    def run_immediate(
        self,
        requests: list[tuple[str, str]],
        display_name: str,
        temperature: float = 0.7,
        response_mime_type: str = "application/json",
        max_output_tokens: int = 65536,
    ) -> dict[str, str]:
        """逐条直接调用 generate_content，返回 {key: raw_json_text}。"""
        from google.genai import types

        results: dict[str, str] = {}
        for i, (key, prompt) in enumerate(requests, 1):
            print(f"  [{display_name}] ({i}/{len(requests)}) generating: {key}")
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type=response_mime_type,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                ),
            )
            text = response.text or ""
            if text:
                results[key] = text
                print(f"    ✓ {key}: {len(text)} chars")
            else:
                print(f"    ✗ {key}: empty response")
        print(f"  [{display_name}] Done {len(results)}/{len(requests)}")
        return results


# ─── 数据加载 ─────────────────────────────────────────────────────────────────


def load_entries() -> dict[str, list[dict]]:
    """读取 ch2_1_entries.jsonl，按 entry_type 分组返回。"""
    groups: dict[str, list[dict]] = defaultdict(list)
    with ENTRIES_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            groups[entry["entry_type"]].append(entry)
    return dict(groups)


def _fmt_entries(entries: list[dict]) -> str:
    """将条目列表格式化为 prompt 用的文本块。"""
    parts = []
    for e in entries:
        parts.append(f"=== {e['entry_comment']} ===\n{e['content']}")
    return "\n\n".join(parts)


# ─── 通用系统指令 ─────────────────────────────────────────────────────────────


def build_system_prompt() -> str:
    return """\
你是一款类博德之门3的CRPG游戏的数据设计师。
你的任务是根据提供的世界观资料（哥布林杀手 Goblin Slayer），生成严格符合指定 JSON Schema 的结构化游戏数据。

核心规则：
1. 所有 ID 使用英文 snake_case（如 "priestess", "healing_potion", "frontier_town"）
2. 数值参考 D&D 5e 机制（属性 3-20，AC 10-20，HP 按等级估算）
3. 对于资料中未明确提及的数值字段，根据角色描述合理推断
4. 输出纯 JSON，不包含 markdown 代码块标记或解释文本
5. 所有文本字段使用中文（name, description, personality 等）
6. 所有 ID、类型枚举使用英文\
"""


# ─── Phase 1 prompt builders ─────────────────────────────────────────────────


def build_lore_request(groups: dict[str, list[dict]]) -> tuple[str, str]:
    sys = build_system_prompt()
    lore_text = _fmt_entries(groups.get("lore", []) + groups.get("chapter", []))
    prompt = f"""{sys}

## 任务
从以下世界观资料中提取结构化的游戏知识条目（LoreEntry）和世界规则（WorldRule）。

## 输出 JSON Schema
{{
  "entries": {{
    "<lore_id>": {{
      "id": "string (英文 snake_case)",
      "title": "string (中文标题)",
      "content": "string (中文正文，保留原文精华，100-400字)",
      "tags": ["string"],
      "scope": "global | area | faction",
      "scope_id": "string | null"
    }}
  }},
  "rules": {{
    "<rule_id>": {{
      "id": "string",
      "title": "string",
      "description": "string",
      "tags": ["string"],
      "scope": "global",
      "scope_id": null,
      "priority": 5
    }}
  }}
}}

## 世界观与章节资料
{lore_text}
"""
    return ("lore", prompt)


def build_classes_request(groups: dict[str, list[dict]]) -> tuple[str, str]:
    sys = build_system_prompt()
    # 从角色条目摘取职业/种族描述作为参考
    char_snippets = []
    for e in groups.get("character", []):
        content = e["content"][:600]
        char_snippets.append(f"[{e['entry_comment']}] {content}")
    char_ref = "\n\n".join(char_snippets[:5])

    prompt = f"""{sys}

## 任务
根据哥布林杀手世界的职业体系，生成以下职业、种族、背景的结构化数据。

## 需要的职业（6个）
战士(fighter)、神官(priest)、游侠(ranger)、魔法师(wizard)、武道家(martial_artist)、斥候(scout)

## 需要的种族（5个）
人类(human)、精灵/森人(elf)、矮人/矿人(dwarf)、蜥蜴人(lizardman)、圃人/半身人(halfling)

## 需要的背景（至少6个，优先包含以下）
士兵(soldier)、冒险者(adventurer)、学者(scholar)、罪犯(criminal)、流浪者(wanderer)、民间英雄(folk_hero)

可选补充：
贵族(noble)、工匠(artisan)、侍僧(acolyte)

## 输出 JSON Schema
{{
  "classes": {{
    "<class_id>": {{
      "id": "string",
      "name": "string (中文)",
      "description": "string",
      "hit_die": "d10 | d8 | d6 | d12",
      "base_hp": 10,
      "hp_per_level": 6,
      "armor_proficiency": ["light", "medium", "heavy", "shield"],
      "weapon_proficiency": ["simple", "martial"],
      "save_proficiency": ["str", "dex", "con", "int", "wis", "cha"],
      "skill_choices": {{"choose": 2, "from": ["athletics", "perception"]}},
      "spellcasting_ability": "wis (施法属性，非施法职业填空字符串)",
      "spellcasting": {{
        "stat": "wis",
        "cantrips_known": {{"1": 3, "4": 4}},
        "spell_slots": {{"1": {{"1": 2}}, "3": {{"1": 3, "2": 1}}}},
        "spells_known": {{"1": 4, "3": 6}}
      }},
      "level_features": {{
        "1": [{{"id": "string", "name": "string", "description": "string"}}]
      }},
      "subclass_options": ["string"],
      "tags": ["string"]
    }}
  }},
  "races": {{
    "<race_id>": {{
      "id": "string",
      "name": "string (中文)",
      "description": "string",
      "speed": 30,
      "languages": ["common"],
      "size": "medium",
      "stat_bonuses": {{"str": 2, "con": 1}},
      "racial_traits": [{{"id": "string", "name": "string", "description": "string"}}],
      "tags": ["string"]
    }}
  }},
  "backgrounds": {{
    "<background_id>": {{
      "id": "string",
      "name": "string (中文)",
      "description": "string",
      "feature": "string（背景特性 ID；如没有可填空字符串）",
      "gold_bonus": 10,
      "starting_gold": 15,
      "skill_proficiency": ["athletics", "survival"],
      "tool_proficiency": ["thieves_tools"],
      "equipment": ["rope_hempen_50ft", "torch"]
    }}
  }}
}}

职业字段说明：
- base_hp：1级基础HP（战士10，神官8，法师6，对应hit_die最大值）
- hp_per_level：升级增加HP（hit_die均值+1，如d10→6，d8→5，d6→4）
- spellcasting_ability：施法属性（如神官"wis"，法师"int"），非施法职业填 ""
- spellcasting：施法配置对象，非施法职业填 null。施法职业需填 stat/cantrips_known/spell_slots/spells_known
- stat_bonuses（种族）：种族属性加值，如人类 {{"str":1,"dex":1,"con":1,"int":1,"wis":1,"cha":1}}，矮人 {{"con":2,"wis":1}}
- backgrounds 必须输出，不可省略，不可为空对象
- 背景字段名使用 skill_proficiency（单数）和 tool_proficiency（单数），与当前内容层 schema 保持一致
- gold_bonus 与 starting_gold 至少填一个；如果两者都能判断，优先同时填
- feature 建议填简洁稳定的 snake_case ID；如确实无合适特性，可填空字符串

## 角色资料参考（职业/种族/背景信息）
{char_ref}
"""
    return ("classes", prompt)


def build_factions_request(groups: dict[str, list[dict]]) -> tuple[str, str]:
    sys = build_system_prompt()
    char_snippets = []
    for e in groups.get("character", []):
        char_snippets.append(f"[{e['entry_comment']}]\n{e['content'][:400]}")
    char_ref = "\n\n".join(char_snippets)

    prompt = f"""{sys}

## 任务
根据哥布林杀手世界的势力格局，生成以下4个阵营的结构化数据。

## 需要的阵营
冒险者公会(adventurer_guild)、地母神教会(earth_mother_church)、边境商会(frontier_merchants)、王国(kingdom)

## 输出 JSON Schema
{{
  "<faction_id>": {{
    "id": "string",
    "name": "string (中文)",
    "description": "string",
    "alignment": "lawful_good | neutral_good | lawful_neutral | neutral | chaotic_good",
    "faction_relations": {{
      "<other_faction_id>": {{"stance": "allied | friendly | neutral | hostile", "strength": 0.5}}
    }},
    "behavioral_rules": ["string"],
    "initial_standing": 0,
    "leader_id": null,
    "member_ids": [],
    "influence_areas": ["frontier_town"],
    "tags": ["string"]
  }}
}}

## 角色与世界观参考
{char_ref}
"""
    return ("factions", prompt)


# ─── Phase 2 prompt builders ─────────────────────────────────────────────────


def build_skills_request(groups: dict[str, list[dict]], class_ids: list[str]) -> tuple[str, str]:
    sys = build_system_prompt()
    char_ref = _fmt_entries(groups.get("character", [])[:4])
    prompt = f"""{sys}

## 前序阶段已生成的 ID（引用必须来自这些列表）
职业 IDs: {json.dumps(class_ids, ensure_ascii=False)}

## 任务
为哥布林杀手世界（低魔暗黑风）设计一套完整技能体系。包含：
- 每个职业的核心技能（每职业3-4个）
- 通用探索/社交技能（3-4个）
- 重要状态效果（毒素、护盾、祝福等，4-6个）

## 输出 JSON Schema
{{
  "skills": {{
    "<skill_id>": {{
      "id": "string",
      "name": "string (中文)",
      "description": "string",
      "category": "spell | ability | passive",
      "spell_level": null,
      "school": null,
      "effect": {{
        "type": "damage | heal | buff | debuff | utility",
        "target": "self | single | area",
        "range": 1,
        "dice": "2d6 | null",
        "damage_type": "string | null",
        "save": "str | dex | con | wis | null",
        "applies_status": "string | null",
        "status_duration": 3,
        "concentration": false
      }},
      "cost": {{
        "action_type": "action | bonus_action | reaction | free",
        "spell_slot": null,
        "resource": null,
        "resource_amount": 0,
        "cooldown": 0
      }},
      "requirements": {{"class": null, "level": 1}},
      "usable_in": ["combat"],
      "tags": ["string"]
    }}
  }},
  "status_effects": {{
    "<effect_id>": {{
      "id": "string",
      "name": "string (中文)",
      "description": "string",
      "category": "buff | debuff",
      "stackable": false,
      "prevents_action": false,
      "tick_damage": null,
      "duration": 3,
      "tags": ["string"]
    }}
  }}
}}

## 角色技能参考
{char_ref}
"""
    return ("skills", prompt)


def build_items_request(groups: dict[str, list[dict]], status_effect_ids: list[str]) -> tuple[str, str]:
    sys = build_system_prompt()
    monster_ref = _fmt_entries(groups.get("monster", []))
    char_snippets = "\n".join(
        f"[{e['entry_comment']}] {e['content'][:300]}"
        for e in groups.get("character", [])[:3]
    )
    prompt = f"""{sys}

## 前序阶段已生成的状态效果 ID（consumable_data.effect 中 applies_status 需引用）
状态效果 IDs: {json.dumps(status_effect_ids, ensure_ascii=False)}

## 任务
为哥布林杀手世界（低魔暗黑风，"准备>战斗力"）设计物品体系。包含：
- 近战武器（4-6种）：短剑/长剑/斧/锤/长柄武器/匕首
- 防具（4-5种）：轻甲/中甲/重甲/盾牌
- 消耗品（5-7种）：治疗药水/解毒剂/火把/硫磺熏烟球/圣水
- 杂物道具（3-4种）：绳索/铁钉/十呎竿/火折子
- 饰品（1-2种）

## 输出 JSON Schema（数组）
[
  {{
    "id": "string",
    "name": "string (中文)",
    "description": "string",
    "type": "weapon | armor | consumable | misc | accessory",
    "rarity": "common | uncommon | rare",
    "base_price": 10,
    "weight": 2.0,
    "slot": "main_hand | off_hand | body | none",
    "tags": ["string"],
    "damage_dice": "1d8 (仅武器)",
    "damage_type": "slashing | piercing | bludgeoning (仅武器)",
    "properties": ["light", "finesse"],
    "range": 1,
    "weapon_proficiency": "simple | martial (仅武器)",
    "ac_bonus": 4,
    "subtype": "light | medium | heavy | shield (仅护甲)",
    "consumable_data": {{
      "trigger": "on_use",
      "charges": 1,
      "effect": {{
        "type": "heal | damage | buff | utility",
        "params": {{"dice": "2d4+2"}},
        "target": "self | single"
      }}
    }}
  }}
]

字段填写规则：
- weight：浮点数（磅），如短剑 2.0、长剑 3.0、重甲 65.0、药水 0.5、杂物 1.0
- 武器(type=weapon)：填 damage_dice/damage_type/properties/range/weapon_proficiency，省略 ac_bonus/subtype/consumable_data
- 护甲(type=armor)：填 ac_bonus（AC加值，不是绝对AC，如皮甲ac_bonus=1，链甲ac_bonus=6，盾牌ac_bonus=2）和 subtype，省略武器字段和consumable_data
- 消耗品(type=consumable)：填 consumable_data，省略武器和护甲字段
- 杂物/饰品：只填基础字段，省略武器/护甲/消耗品字段

## 参考资料
{monster_ref}

{char_snippets}
"""
    return ("items", prompt)


# ─── Phase 3 prompt builders ─────────────────────────────────────────────────


def build_characters_request(
    char_entries: list[dict],
    batch_name: str,
    faction_ids: list[str],
    class_ids: list[str],
    skill_ids: list[str],
    item_ids: list[str],
) -> tuple[str, str]:
    sys = build_system_prompt()
    char_text = _fmt_entries(char_entries)
    prompt = f"""{sys}

## 前序阶段已生成的 ID（所有引用必须来自这些列表）
阵营 IDs: {json.dumps(faction_ids, ensure_ascii=False)}
职业 IDs: {json.dumps(class_ids, ensure_ascii=False)}
技能 IDs: {json.dumps(skill_ids, ensure_ascii=False)}
物品 IDs: {json.dumps(item_ids, ensure_ascii=False)}

可用区域 IDs: ["frontier_town", "cow_girl_farm", "water_capital", "ancient_ruins"]
可用子地点 IDs: ["guild_hall", "guild_counter", "temple", "tavern", "farm_house", "farm_field"]

## 任务
根据以下角色的 lorabook 散文描述，生成完整的结构化角色数据。

## 输出 JSON Schema（对象数组）
[
  {{
    "id": "string",
    "name": "string (中文)",
    "area_id": "string (从可用区域选择)",
    "location_id": "string (子地点 ID)",
    "faction_id": "string (从阵营 IDs 选择)",
    "class_id": "string (从职业 IDs 选择)",
    "tags": ["string"],
    "tier": "main | secondary | background",
    "personality": "string (中文，2-3句)",
    "dialogue_style": "string (中文，说话风格)",
    "speech_pattern": "string (中文，口头禅/语气特征)",
    "appearance": "string (中文，外貌简述，2-3句，不含敏感内容)",
    "backstory": "string (中文，背景故事，3-4句)",
    "schedule": {{
      "morning": "string (子地点 ID)",
      "afternoon": "string",
      "evening": "string",
      "night": "string"
    }},
    "stats": {{"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10}},
    "base_hp": 20,
    "base_ac": 12,
    "level": 3,
    "proficiency_bonus": 2,
    "combat_capable": true,
    "attacks": [
      {{"name": "string", "hit_bonus": 3, "damage_dice": "1d6", "damage_type": "slashing", "range": 1, "tags": []}}
    ],
    "skills": ["string (从技能 IDs 选择，最多4个)"],
    "secrets": ["string (角色秘密，1-2条中文)"],
    "base_disposition": {{"approval": 0, "trust": 0, "fear": 0, "romance": 0}},
    "inventory": [{{"item_id": "string (从物品 IDs 选择)", "count": 1}}],
    "shop": null
  }}
]

## 角色资料
{char_text}
"""
    return (batch_name, prompt)


def build_monsters_request(
    groups: dict[str, list[dict]],
    item_ids: list[str],
) -> tuple[str, str]:
    sys = build_system_prompt()
    monster_text = _fmt_entries(groups.get("monster", []))
    prompt = f"""{sys}

## 前序阶段已生成的物品 ID（用于 loot_table）
物品 IDs: {json.dumps(item_ids, ensure_ascii=False)}

## 任务
根据以下怪物资料，生成4种哥布林类怪物的结构化数据。

## 需要的怪物
- 普通哥布林 (goblin): CR 0.25，弱小贪婪，群体战术
- 哥布林萨满 (goblin_shaman): CR 1.0，会用原始魔法
- 大哥布林/半兽人 (hobgoblin): CR 2.0，比普通哥布林更强壮
- 哥布林骑手 (goblin_rider): CR 3.0，骑乘狼

## 输出 JSON Schema（数组）
[
  {{
    "id": "string",
    "name": "string (中文)",
    "description": "string (中文，2-3句)",
    "creature_type": "humanoid",
    "hp": 7,
    "ac": 13,
    "cr": 0.25,
    "xp_reward": 50,
    "speed": 30,
    "abilities": {{"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8}},
    "attacks": [
      {{"name": "string", "hit_bonus": 4, "damage_dice": "1d6+2", "damage_type": "slashing", "range": 1, "tags": []}}
    ],
    "loot_table": [
      {{"item_id": "string (从物品 IDs 选择)", "chance": 0.3, "count": "1"}}
    ],
    "gold_drop": "1d4",
    "ai_personality": "aggressive | defensive | cowardly",
    "flee_threshold": 0.3,
    "flee_chance": 0.6,
    "tactics_notes": "string (中文，战术特征)",
    "resistances": [],
    "immunities": [],
    "vulnerabilities": [],
    "tags": ["goblin", "humanoid"]
  }}
]

## 怪物资料
{monster_text}
"""
    return ("monsters", prompt)


# ─── Phase 4 prompt builders ─────────────────────────────────────────────────


def build_maps_request(
    groups: dict[str, list[dict]],
    char_ids: list[str],
    monster_ids: list[str],
) -> tuple[str, str]:
    sys = build_system_prompt()
    location_text = _fmt_entries(groups.get("location", []))
    prompt = f"""{sys}

## 前序阶段已生成的 ID
角色 IDs: {json.dumps(char_ids, ensure_ascii=False)}
怪物 IDs: {json.dumps(monster_ids, ensure_ascii=False)}

## 任务
根据以下地点资料，生成4个区域的结构化地图数据。包含子地点、NPC 分配、怪物配置。

## 需要的区域
1. frontier_town（边境小镇，起始区域，is_starting_area=true）
2. cow_girl_farm（牧场）
3. water_capital（水之都）
4. ancient_ruins（古代遗迹，本章探索目标，LLM 自由设计）

## 输出 JSON Schema（数组）
[
  {{
    "id": "string",
    "name": "string (中文)",
    "description": "string (中文，氛围描述，2-3句)",
    "region": "string",
    "danger_level": "low | medium | high | extreme",
    "terrain_type": "urban | forest | cave | underground | plains",
    "tags": ["string"],
    "is_starting_area": false,
    "connections": [
      {{
        "target": "string (其他 area_id)",
        "type": "walk | travel",
        "travel_time": "30分钟",
        "description": "string",
        "blocked": false
      }}
    ],
    "sub_locations": {{
      "<sub_id>": {{
        "id": "string",
        "name": "string (中文)",
        "description": "string",
        "type": "shop | tavern | temple | guild | residential | dungeon | outdoor",
        "resident_npcs": ["string (从角色 IDs 选择)"],
        "interactables": [
          {{
            "id": "string",
            "name": "string (中文)",
            "description": "string",
            "type": "container | door | readable | generic",
            "tags": []
          }}
        ],
        "tags": []
      }}
    }},
    "hostile_pool": [
      {{
        "id": "string (如 goblin_patrol)",
        "name": "string (中文，如 哥布林巡逻队)",
        "description": "string (中文)",
        "hostile_config": {{
          "hostile_groups": [
            {{"monster_ids": ["goblin"], "count": "2d4", "role": "patrol | ambush | guard"}}
          ],
          "stealth_dc": 12,
          "blocking": true
        }}
      }}
    ],
    "encounter_table": [
      {{"monster_ids": ["goblin", "goblin"], "weight": 1.0, "description": "string (中文)"}}
    ],
    "discoveries": [
      {{
        "id": "string",
        "name": "string (中文)",
        "check_type": "perception | investigation",
        "dc": 15,
        "reward": {{"gold": "2d10", "items": ["healing_potion"]}},
        "tags": []
      }}
    ]
  }}
]

字段说明：
- hostile_pool：敌对区域配置（HostileTemplate 格式），需要 id + hostile_config 嵌套。安全区域（frontier_town/cow_girl_farm）设为 null
- encounter_table：遭遇条目，monster_ids 是列表（用列表长度表示数量，如3个哥布林 = ["goblin","goblin","goblin"]）
- discoveries：隐藏发现点，安全区域设为 []，危险区域设 1-3 个
- ancient_ruins 应有丰富的 hostile_pool（哥布林巢穴）和 discoveries（隐藏宝藏/秘密通道）

## 地点资料
{location_text}
"""
    return ("maps", prompt)


def build_quests_request(
    groups: dict[str, list[dict]],
    char_ids: list[str],
    map_ids: list[str],
) -> tuple[str, str]:
    sys = build_system_prompt()
    chapter_text = _fmt_entries(groups.get("chapter", []))
    prompt = f"""{sys}

## 前序阶段已生成的 ID
角色 IDs: {json.dumps(char_ids, ensure_ascii=False)}
区域 IDs: {json.dumps(map_ids, ensure_ascii=False)}

## 任务
根据以下第二卷第一章的叙事脚本，生成章节元数据和3个任务里程碑。
每个章节事件对应一个 milestone。

## 输出 JSON Schema
{{
  "chapters": [
    {{"id": "ch2", "title": "第二卷：水之都的阴影", "description": "string"}}
  ],
  "milestones": {{
    "<milestone_id>": {{
      "id": "string",
      "title": "string (中文)",
      "description": "string (中文，事件描述)",
      "chapter_id": "ch2",
      "tags": ["string"],
      "sequence": 1,
      "prerequisites": [],
      "next_milestones": ["string"],
      "involved_npcs": ["string (从角色 IDs 选择)"],
      "involved_locations": ["string (区域或子地点 ID)"],
      "narrative_context": "string (中文，叙事背景)",
      "key_elements": ["string"],
      "completion_value": 10
    }}
  }}
}}

## 第二卷第一章叙事脚本
{chapter_text}
"""
    return ("quests", prompt)


# ─── 解析与保存辅助 ──────────────────────────────────────────────────────────


def parse_json_safe(text: str) -> Any:
    """解析 LLM 返回的 JSON 文本，去除可能的 markdown 代码块标记。"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # 去掉首尾的 ``` 行
        start = 1 if lines[0].startswith("```") else 0
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[start:end]).strip()
    return json.loads(text)


def parse_and_save(raw_text: str, output_file: Path) -> Any:
    """解析 JSON 文本，写入文件，返回 parsed 数据。"""
    try:
        data = parse_json_safe(raw_text)
    except json.JSONDecodeError as e:
        print(f"  ⚠ JSON parse error for {output_file.name}: {e}")
        print(f"    Raw text (first 200 chars): {raw_text[:200]}")
        data = {}
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  ✓ Saved {output_file.name}")
    return data


def extract_ids(data: Any) -> list[str]:
    """从 LLM 输出中提取 ID 列表（支持 dict 和 list 两种格式）。"""
    if isinstance(data, dict):
        return list(data.keys())
    if isinstance(data, list):
        result = []
        for item in data:
            if isinstance(item, dict) and "id" in item:
                result.append(str(item["id"]))
        return result
    return []


def list_to_dict_by_id(data: Any) -> dict[str, Any]:
    """将 list[{id: ...}] 转为 dict[id, {...}]，以兼容 registry.load()。"""
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        return {str(item["id"]): item for item in data if isinstance(item, dict) and "id" in item}
    return {}


# ─── Phase 5: Tags ──────────────────────────────────────────────────────────


_TAG_KEYWORDS: dict[str, list[str]] = {
    "story_role": ["protagonist", "ally", "quest_giver", "merchant", "enemy", "npc", "companion", "boss", "guard"],
    "race": ["human", "elf", "dwarf", "halfling", "lizardman", "goblin", "orc", "undead", "dragon"],
    "combat_style": ["melee", "ranged", "magic", "healer", "tank", "stealth", "support", "berserker"],
    "creature_type": ["humanoid", "beast", "undead", "fiend", "construct", "aberration", "monstrosity"],
    "location_type": ["town", "dungeon", "wilderness", "ruins", "farm", "temple", "tavern", "guild", "urban", "cave"],
    "affiliation": ["guild", "church", "kingdom", "merchant", "independent", "faction"],
}


def _collect_tags_recursive(data: Any, collected: set[str]) -> None:
    if isinstance(data, dict):
        for k, v in data.items():
            if k == "tags" and isinstance(v, list):
                for t in v:
                    if isinstance(t, str) and t.strip():
                        collected.add(t.strip().lower())
            else:
                _collect_tags_recursive(v, collected)
    elif isinstance(data, list):
        for item in data:
            _collect_tags_recursive(item, collected)


def generate_tags(all_outputs: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Phase 5: 扫描所有输出的 tags 字段，按维度分类。

    输出格式对齐 TagRegistry.load() 期望：
    {dim_key: {"id": dim_key, "description": "", "tags": [strings]}}
    """
    collected: set[str] = set()
    for data in all_outputs.values():
        _collect_tags_recursive(data, collected)

    dimensions: dict[str, dict[str, Any]] = {}
    for k in _TAG_KEYWORDS:
        dimensions[k] = {"id": k, "description": "", "tags": []}
    dimensions["general"] = {"id": "general", "description": "", "tags": []}

    for tag in sorted(collected):
        categorized = False
        for dim, keywords in _TAG_KEYWORDS.items():
            if any(kw in tag for kw in keywords):
                dimensions[dim]["tags"].append(tag)
                categorized = True
                break
        if not categorized:
            dimensions["general"]["tags"].append(tag)

    return dimensions


# ─── main ───────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="V2 数据管线")
    parser.add_argument(
        "--mode", choices=["batch", "immediate"], default="batch",
        help="batch=Batch API（默认），immediate=逐条直接调用",
    )
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("环境变量 GEMINI_API_KEY 或 GOOGLE_API_KEY 未设置")

    if not ENTRIES_PATH.exists():
        raise FileNotFoundError(
            f"入口文件不存在：{ENTRIES_PATH}\n"
            "请先运行：python3 -m app.extract_ch2_1"
        )

    runner = BatchRunner(model=MODEL, api_key=api_key)
    groups = load_entries()

    def run(requests: list[tuple[str, str]], display_name: str) -> dict[str, str]:
        if args.mode == "immediate":
            return runner.run_immediate(requests, display_name)
        return runner.run_batch(requests, BATCH_TEMP, display_name)

    print(f"模式：{args.mode}")
    print(f"已加载条目：{ {t: len(v) for t, v in groups.items()} }")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── Phase 1: Lore + Classes + Factions ──────────────────────────────────
    print("\n=== Phase 1: Lore / Classes / Factions ===")
    p1_requests = [
        build_lore_request(groups),
        build_classes_request(groups),
        build_factions_request(groups),
    ]
    p1_results = run(p1_requests, "v2-phase1")

    lore_data   = parse_and_save(p1_results.get("lore", "{}"),    OUTPUT_DIR / "lore.json")
    classes_raw = parse_and_save(p1_results.get("classes", "{}"), OUTPUT_DIR / "classes.json")
    factions_raw = parse_and_save(p1_results.get("factions", "{}"), OUTPUT_DIR / "factions.json")

    # 提取 IDs
    classes_data = classes_raw.get("classes", {}) if isinstance(classes_raw, dict) else {}
    backgrounds_data = classes_raw.get("backgrounds", {}) if isinstance(classes_raw, dict) else {}
    class_ids    = list(classes_data.keys())
    background_ids = list(backgrounds_data.keys())
    faction_ids  = list(factions_raw.keys()) if isinstance(factions_raw, dict) else []
    print(f"  class_ids: {class_ids}")
    print(f"  background_ids: {background_ids}")
    print(f"  faction_ids: {faction_ids}")

    # ── Phase 2: Skills + Items ──────────────────────────────────────────────
    print("\n=== Phase 2: Skills / Items ===")
    p2_requests = [
        build_skills_request(groups, class_ids),
        build_items_request(groups, []),  # 第一次运行无 status_effect_ids，后续可补
    ]
    p2_results = run(p2_requests, "v2-phase2")

    skills_parsed = parse_json_safe(p2_results.get("skills", "{}"))
    # 展平：把 {"skills": {...}, "status_effects": {...}} 变成
    # {skill_id: {...}, ..., "status_effects": {...}}
    if isinstance(skills_parsed, dict) and "skills" in skills_parsed:
        inner = skills_parsed.pop("skills")
        if isinstance(inner, dict):
            skills_parsed = {**inner, **skills_parsed}
    (OUTPUT_DIR / "skills.json").write_text(
        json.dumps(skills_parsed, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    skills_raw = skills_parsed
    print(f"  ✓ Saved skills.json")
    items_raw  = parse_and_save(p2_results.get("items", "[]"),  OUTPUT_DIR / "items.json")

    # 转为 dict 格式并保存（items 可能是 list）
    items_dict = list_to_dict_by_id(items_raw)
    if isinstance(items_raw, list):
        (OUTPUT_DIR / "items.json").write_text(
            json.dumps(items_dict, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    skill_ids = []
    if isinstance(skills_raw, dict):
        skill_ids = [k for k in skills_raw if k != "status_effects"]
    item_ids = list(items_dict.keys())

    status_effect_ids: list[str] = []
    if isinstance(skills_raw, dict) and "status_effects" in skills_raw:
        status_effect_ids = list(skills_raw["status_effects"].keys())

    print(f"  skill_ids ({len(skill_ids)}): {skill_ids[:5]}...")
    print(f"  item_ids ({len(item_ids)}): {item_ids[:5]}...")
    print(f"  status_effect_ids: {status_effect_ids}")

    # ── Phase 3: Characters + Monsters ──────────────────────────────────────
    print("\n=== Phase 3: Characters / Monsters ===")
    char_entries = groups.get("character", [])
    mid = len(char_entries) // 2
    batch1 = char_entries[:mid]   # 前半：女神官/哥布林杀手/妖精弓手/矿人道士
    batch2 = char_entries[mid:]   # 后半：蜥蜴僧侣/柜台小姐/牧牛妹

    p3_requests = [
        build_characters_request(batch1, "characters_batch1", faction_ids, class_ids, skill_ids, item_ids),
        build_characters_request(batch2, "characters_batch2", faction_ids, class_ids, skill_ids, item_ids),
        build_monsters_request(groups, item_ids),
    ]
    p3_results = run(p3_requests, "v2-phase3")

    chars_b1 = parse_json_safe(p3_results.get("characters_batch1", "[]"))
    chars_b2 = parse_json_safe(p3_results.get("characters_batch2", "[]"))

    # 合并并转为 dict
    all_chars: list[dict] = []
    if isinstance(chars_b1, list):
        all_chars.extend(chars_b1)
    if isinstance(chars_b2, list):
        all_chars.extend(chars_b2)
    chars_dict = list_to_dict_by_id(all_chars)
    (OUTPUT_DIR / "characters.json").write_text(
        json.dumps(chars_dict, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  ✓ Saved characters.json ({len(chars_dict)} chars)")

    monsters_raw = parse_and_save(p3_results.get("monsters", "[]"), OUTPUT_DIR / "monsters.json")
    monsters_dict = list_to_dict_by_id(monsters_raw)
    if isinstance(monsters_raw, list):
        (OUTPUT_DIR / "monsters.json").write_text(
            json.dumps(monsters_dict, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    char_ids    = list(chars_dict.keys())
    monster_ids = list(monsters_dict.keys())
    print(f"  char_ids: {char_ids}")
    print(f"  monster_ids: {monster_ids}")

    # ── Phase 4: Maps + Quests ───────────────────────────────────────────────
    print("\n=== Phase 4: Maps / Quests ===")
    p4_requests = [
        build_maps_request(groups, char_ids, monster_ids),
        build_quests_request(groups, char_ids, ["frontier_town", "cow_girl_farm", "water_capital", "ancient_ruins"]),
    ]
    p4_results = run(p4_requests, "v2-phase4")

    maps_raw   = parse_and_save(p4_results.get("maps", "[]"),   OUTPUT_DIR / "maps.json")
    quests_raw = parse_and_save(p4_results.get("quests", "{}"), OUTPUT_DIR / "quests.json")

    # maps 可能是 list，转 dict
    maps_dict = list_to_dict_by_id(maps_raw)
    if isinstance(maps_raw, list):
        (OUTPUT_DIR / "maps.json").write_text(
            json.dumps(maps_dict, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ── Phase 5: Tags ────────────────────────────────────────────────────────
    print("\n=== Phase 5: Tags (deterministic) ===")
    all_outputs = {
        "lore": lore_data,
        "classes": classes_raw,
        "factions": factions_raw,
        "skills": skills_raw,
        "items": items_dict,
        "characters": chars_dict,
        "monsters": monsters_dict,
        "maps": maps_dict,
        "quests": quests_raw,
    }
    tags = generate_tags(all_outputs)
    (OUTPUT_DIR / "tags.json").write_text(
        json.dumps(tags, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    total_tags = sum(len(v["tags"]) for v in tags.values())
    print(f"  ✓ Saved tags.json ({total_tags} tags across {len(tags)} dimensions)")

    # ── 完成 ─────────────────────────────────────────────────────────────────
    print("\n✅ V2 data pipeline complete!")
    print(f"   输出目录：{OUTPUT_DIR.resolve()}")
    files = list(OUTPUT_DIR.glob("*.json"))
    print(f"   生成文件：{[f.name for f in sorted(files)]}")


if __name__ == "__main__":
    main()
