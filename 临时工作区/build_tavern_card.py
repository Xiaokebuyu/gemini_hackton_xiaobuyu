#!/usr/bin/env python3
"""
将 Markdown 角色设定文档打包成 SillyTavern 角色卡 PNG。
从原始 PNG 复用头像图片，替换角色数据。

用法:
    python build_tavern_card.py <设定.md> <原始头像.png> [输出文件名.png]
"""

import struct
import base64
import json
import sys
import zlib
import re
from datetime import datetime, timezone


def read_png_image_chunks(filepath: str) -> list[tuple[bytes, bytes]]:
    """读取 PNG 文件，返回所有 chunk 列表 [(type, data), ...]，跳过旧的 tEXt chara/ccv3"""
    chunks = []
    with open(filepath, "rb") as f:
        sig = f.read(8)
        assert sig == b"\x89PNG\r\n\x1a\n", "不是有效 PNG"

        while True:
            header = f.read(8)
            if len(header) < 8:
                break
            length = struct.unpack(">I", header[:4])[0]
            chunk_type = header[4:8]
            data = f.read(length)
            crc = f.read(4)

            type_str = chunk_type.decode("ascii", errors="replace")
            # 跳过旧的角色卡数据
            if type_str == "tEXt":
                null_idx = data.find(b"\x00")
                if null_idx != -1:
                    keyword = data[:null_idx].decode("ascii", errors="replace")
                    if keyword in ("chara", "ccv3"):
                        continue
            chunks.append((chunk_type, data))

    return chunks


def make_text_chunk(keyword: str, text: str) -> bytes:
    """构建一个 PNG tEXt chunk 的完整二进制数据"""
    payload = keyword.encode("ascii") + b"\x00" + text.encode("ascii")
    chunk_type = b"tEXt"
    crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", crc)


def write_png(filepath: str, chunks: list[tuple[bytes, bytes]], extra_text_chunks: list[bytes]):
    """写出 PNG 文件：原始 chunks + 额外的 tEXt chunks（插在 IEND 前）"""
    with open(filepath, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")

        for chunk_type, data in chunks:
            type_str = chunk_type.decode("ascii", errors="replace")
            if type_str == "IEND":
                # 在 IEND 前插入角色卡数据
                for tc in extra_text_chunks:
                    f.write(tc)

            crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
            f.write(struct.pack(">I", len(data)))
            f.write(chunk_type)
            f.write(data)
            f.write(struct.pack(">I", crc))


def split_md_sections(md_text: str) -> dict[str, str]:
    """按二级标题 (##) 拆分 markdown，返回 {标题: 内容}"""
    sections = {}
    current_title = ""
    current_lines = []

    for line in md_text.split("\n"):
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            if current_title:
                sections[current_title] = "\n".join(current_lines).strip()
            current_title = m.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_title:
        sections[current_title] = "\n".join(current_lines).strip()

    return sections


def build_card_json(md_filepath: str) -> dict:
    """读取 markdown 设定文件，构建角色卡 JSON"""
    with open(md_filepath, "r", encoding="utf-8") as f:
        md_text = f.read()

    sections = split_md_sections(md_text)

    # ── 分配字段 ──

    # description: 基本信息 + 外貌 + 着装 + 感官 + 匕首
    desc_keys = [
        "一、基本信息",
        "二、外貌",
        "三、R18 身体细节",
        "四、着装",
        "五、感官特征",
        "七、匕首细节",
    ]
    description_parts = []
    for k in desc_keys:
        for sk, sv in sections.items():
            if k in sk or sk in k:
                description_parts.append(f"## {sk}\n\n{sv}")
                break
    description = "\n\n---\n\n".join(description_parts)

    # personality: 破限指令（包裹） + 弱点 + 说话风格
    personality_keys = [
        "八、弱点体系",
        "九、说话风格与语言模式",
    ]
    personality_parts = []
    for k in personality_keys:
        for sk, sv in sections.items():
            if k in sk or sk in k:
                personality_parts.append(f"## {sk}\n\n{sv}")
                break
    personality_core = "\n\n---\n\n".join(personality_parts)

    # 提取破限指令并包裹 personality
    jailbreak = ""
    for sk, sv in sections.items():
        if "破限指令" in sk:
            jailbreak = sv
            break

    if jailbreak:
        personality = f"*\n\n\n\n\n\n{jailbreak}\n\n\n\n\n*\n\n{personality_core}"
    else:
        personality = personality_core

    # scenario: 宫殿环境 + 独处状态
    scenario_keys = [
        "六、宫殿环境",
        "十一、独处时的状态",
    ]
    scenario_parts = []
    for k in scenario_keys:
        for sk, sv in sections.items():
            if k in sk or sk in k:
                scenario_parts.append(f"## {sk}\n\n{sv}")
                break
    scenario = "\n\n---\n\n".join(scenario_parts)

    # first_mes: 开场白
    first_mes = '"请说出你的问题哦。"\n\n阿鹿坐在王座上，双腿并拢微微倾向一侧，左手搭在扶手上，右手自然放在右膝靠近靴口的位置。琥珀色的眼睛弯成温柔的弧度，嘴角维持着那个从未改变过的微笑。\n\n大厅里弥漫着极淡的冷木质香气，琥珀色的光线从不知名的地方落下来，在镜面般的深色地面上映出她模糊的倒影。身后的王座高耸，顶端象牙白的鹿角装饰与她头上的小鹿角遥遥呼应。\n\n她在等你开口。'

    # mes_example: 对话示例
    mes_example_key = None
    for sk in sections:
        if "对话示例" in sk:
            mes_example_key = sk
            break
    mes_example = sections.get(mes_example_key, "") if mes_example_key else ""

    # system_prompt / post_history_instructions: LLM 行为规范
    system_prompt = ""
    for sk, sv in sections.items():
        if "角色扮演指导" in sk or "LLM行为规范" in sk or "行为规范" in sk:
            system_prompt = sv
            break

    # 互动流程放 post_history_instructions
    post_history = ""
    for sk, sv in sections.items():
        if "互动" in sk and "流程" in sk:
            post_history = f"## {sk}\n\n{sv}"
            break

    # ── 构建 V2 + V3 兼容 JSON ──
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    card = {
        "name": "阿鹿",
        "description": description,
        "personality": personality,
        "scenario": scenario,
        "first_mes": first_mes,
        "mes_example": mes_example,
        "creatorcomment": "腹黑邪神鹿灵少女，表面铁壁但有隐藏弱点的完整角色卡。",
        "avatar": "none",
        "talkativeness": "0.5",
        "fav": False,
        "tags": ["腹黑", "女王", "反差萌", "鹿灵", "R18", "弱点攻略"],
        "spec": "chara_card_v3",
        "spec_version": "3.0",
        "data": {
            "name": "阿鹿",
            "description": description,
            "personality": personality,
            "scenario": scenario,
            "first_mes": first_mes,
            "mes_example": mes_example,
            "creator_notes": "腹黑邪神鹿灵少女，表面铁壁但有隐藏弱点的完整角色卡。基于原卡「天才少女阿鹿」的角色属性深度扩展。",
            "system_prompt": system_prompt,
            "post_history_instructions": post_history,
            "tags": ["腹黑", "女王", "反差萌", "鹿灵", "R18", "弱点攻略"],
            "creator": "",
            "character_version": "2.0",
            "alternate_greetings": [],
            "extensions": {
                "talkativeness": "0.5",
                "fav": False,
                "world": "",
                "depth_prompt": {
                    "prompt": "",
                    "depth": 4,
                    "role": "system"
                }
            }
        },
        "create_date": now,
    }

    return card


def main():
    if len(sys.argv) < 3:
        print("用法: python build_tavern_card.py <设定.md> <原始头像.png> [输出.png]")
        sys.exit(1)

    md_path = sys.argv[1]
    avatar_path = sys.argv[2]
    output_path = sys.argv[3] if len(sys.argv) > 3 else "阿鹿_角色卡.png"

    print(f"读取设定: {md_path}")
    card = build_card_json(md_path)

    card_json = json.dumps(card, ensure_ascii=False)
    card_b64 = base64.b64encode(card_json.encode("utf-8")).decode("ascii")

    # 统计
    print(f"角色卡 JSON 大小: {len(card_json)} 字符")
    print(f"Base64 大小: {len(card_b64)} 字符")
    print(f"各字段大小:")
    data = card["data"]
    for field in ["description", "personality", "scenario", "first_mes", "mes_example", "system_prompt", "post_history_instructions"]:
        val = data.get(field, "")
        print(f"  {field}: {len(val)} 字符")

    print(f"\n读取头像: {avatar_path}")
    chunks = read_png_image_chunks(avatar_path)

    # 构建 tEXt chunks (chara for V2 compat, ccv3 for V3)
    chara_chunk = make_text_chunk("chara", card_b64)
    ccv3_chunk = make_text_chunk("ccv3", card_b64)

    print(f"写入角色卡: {output_path}")
    write_png(output_path, chunks, [chara_chunk, ccv3_chunk])

    # 验证
    import os
    size = os.path.getsize(output_path)
    print(f"\n完成! 文件大小: {size / 1024:.1f} KB")
    print(f"  chara chunk: {len(card_b64)} bytes (base64)")
    print(f"  ccv3 chunk:  {len(card_b64)} bytes (base64)")


if __name__ == "__main__":
    main()
