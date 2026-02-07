"""
NPC 分类器

从世界书中提取角色信息并按重要性分类。
"""
import json
import re
from pathlib import Path
from typing import Dict, Any, Optional, List

from google import genai
from google.genai import types

from app.config import settings
from .models import CharacterInfo, NPCTier, CharactersData, MapsData


class NPCClassifier:
    """NPC 分类器"""

    def __init__(self, model: str = None, api_key: str = None):
        """
        初始化 NPC 分类器

        Args:
            model: Gemini 模型名称
            api_key: API 密钥
        """
        self.client = genai.Client(api_key=api_key or settings.gemini_api_key)
        self.model = model or "gemini-2.0-flash"
        self.prompt_template = self._load_prompt_template()

    def _load_prompt_template(self) -> str:
        """加载 prompt 模板"""
        template_path = Path(__file__).parent / "prompts" / "npc_classification.md"
        if template_path.exists():
            return template_path.read_text(encoding="utf-8")

        return """请从以下世界书内容中提取所有角色并分类。

已知地图列表：
{known_maps}

输出 JSON 格式：
{
  "characters": [...],
  "map_assignments": {...}
}

世界书内容：
{worldbook_content}
"""

    def _fix_json_string(self, text: str) -> str:
        """修复常见的 JSON 问题"""
        text = re.sub(r',(\s*[}\]])', r'\1', text)
        return text

    def _parse_json(self, text: str) -> Optional[Dict[str, Any]]:
        """解析 JSON 响应，支持截断恢复"""
        text = text.strip()

        def try_parse(s: str) -> Optional[Dict]:
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                try:
                    return json.loads(self._fix_json_string(s))
                except json.JSONDecodeError:
                    return None

        result = try_parse(text)
        if result:
            return result

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

        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            result = try_parse(match.group(0))
            if result:
                return result

        # 截断恢复：输出超长时 JSON 可能不完整，尝试在最后一个完整对象处截断
        result = self._try_recover_truncated(text)
        if result:
            return result

        return None

    def _try_recover_truncated(self, text: str) -> Optional[Dict[str, Any]]:
        """尝试从截断的 JSON 中恢复 characters 数组"""
        # 找到 "characters" 数组的起始位置
        idx = text.find('"characters"')
        if idx == -1:
            return None

        # 找到数组开始的 [
        arr_start = text.find('[', idx)
        if arr_start == -1:
            return None

        # 从后往前找最后一个完整的 }（角色对象结尾）
        last_close = text.rfind('}')
        if last_close <= arr_start:
            return None

        # 尝试逐步回退找到可解析的完整数组
        search_from = last_close
        for _ in range(50):  # 最多回退 50 次
            candidate = text[:search_from + 1] + ']}'
            # 确保从顶层 { 开始
            top_start = text.find('{')
            if top_start == -1:
                break
            candidate = text[top_start:search_from + 1] + ']}'
            try:
                data = json.loads(candidate)
                if isinstance(data.get("characters"), list) and len(data["characters"]) > 0:
                    print(f"  [truncation recovery] Recovered {len(data['characters'])} characters from truncated JSON")
                    return data
            except json.JSONDecodeError:
                pass
            # 继续往前找上一个 }
            search_from = text.rfind('}', 0, search_from)
            if search_from <= arr_start:
                break

        return None

    async def classify(
        self,
        worldbook_content: str,
        maps_data: Optional[MapsData] = None
    ) -> CharactersData:
        """
        从世界书中提取并分类角色

        Args:
            worldbook_content: 完整的世界书内容
            maps_data: 已提取的地图数据（用于关联 NPC 到地图）

        Returns:
            CharactersData: 结构化的角色数据
        """
        # 准备已知地图列表
        known_maps = "无（将自动创建）"
        if maps_data and maps_data.maps:
            known_maps = "\n".join(
                f"- {m.id}: {m.name}"
                for m in maps_data.maps
            )

        # 构建 prompt
        prompt = self.prompt_template.replace(
            "{worldbook_content}", worldbook_content
        ).replace(
            "{known_maps}", known_maps
        )

        # 配置 JSON 输出（角色列表可能很长，需要足够的输出空间）
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
            max_output_tokens=65536,
        )

        # 调用 LLM
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=config,
        )

        # 提取响应文本
        text = ""
        finish_reason = None
        if hasattr(response, 'candidates') and response.candidates:
            candidate = response.candidates[0]
            finish_reason = getattr(candidate, 'finish_reason', None)
            for part in candidate.content.parts:
                if hasattr(part, 'text') and part.text:
                    if not (hasattr(part, 'thought') and part.thought):
                        text += part.text

        if finish_reason and str(finish_reason) != "STOP":
            print(f"  Warning: NPC classification finish_reason={finish_reason}, output may be truncated ({len(text)} chars)")

        # 解析 JSON
        raw_data = self._parse_json(text)
        if not raw_data:
            raise ValueError(f"Failed to parse NPC classification response (finish_reason={finish_reason}, len={len(text)}): {text[:500]}...")

        # 转换为数据模型
        return self._convert_to_model(raw_data)

    def _convert_to_model(self, raw_data: Dict[str, Any]) -> CharactersData:
        """将原始数据转换为数据模型"""
        characters = []
        for char_data in raw_data.get("characters", []):
            # 解析 tier
            tier_str = char_data.get("tier", "secondary")
            try:
                tier = NPCTier(tier_str)
            except ValueError:
                tier = NPCTier.SECONDARY

            characters.append(CharacterInfo(
                id=char_data.get("id", ""),
                name=char_data.get("name", ""),
                tier=tier,
                default_map=char_data.get("default_map"),
                default_sub_location=char_data.get("default_sub_location"),
                aliases=char_data.get("aliases", []),
                occupation=char_data.get("occupation"),
                age=char_data.get("age"),
                personality=char_data.get("personality"),
                speech_pattern=char_data.get("speech_pattern"),
                example_dialogue=char_data.get("example_dialogue"),
                appearance=char_data.get("appearance"),
                backstory=char_data.get("backstory"),
                relationships=char_data.get("relationships", {}),
                importance=char_data.get("importance", 0.5),
                tags=char_data.get("tags", []),
            ))

        # 转换地图分配
        map_assignments = raw_data.get("map_assignments", {})

        return CharactersData(
            characters=characters,
            map_assignments=map_assignments,
        )

    def validate(
        self,
        characters_data: CharactersData,
        maps_data: Optional[MapsData] = None
    ) -> List[str]:
        """
        验证角色数据

        Args:
            characters_data: 角色数据
            maps_data: 地图数据（用于验证地图引用）

        Returns:
            验证错误列表
        """
        errors = []
        char_ids = {c.id for c in characters_data.characters}
        map_ids = {m.id for m in maps_data.maps} if maps_data else set()

        # 检查 ID 唯一性
        if len(char_ids) != len(characters_data.characters):
            errors.append("存在重复的角色 ID")

        # 检查必填字段
        for char in characters_data.characters:
            if not char.id:
                errors.append("存在空的角色 ID")
            if not char.name:
                errors.append(f"角色 '{char.id}' 缺少名称")

        # 检查地图引用
        if map_ids:
            for char in characters_data.characters:
                if char.default_map and char.default_map not in map_ids:
                    errors.append(
                        f"角色 '{char.id}' 引用不存在的地图 '{char.default_map}'"
                    )

        # 检查关系引用
        for char in characters_data.characters:
            for rel_id in char.relationships.keys():
                if rel_id not in char_ids:
                    errors.append(
                        f"角色 '{char.id}' 的关系引用不存在的角色 '{rel_id}'"
                    )

        # 检查 map_assignments
        for map_id, assignments in characters_data.map_assignments.items():
            if map_ids and map_id not in map_ids:
                errors.append(f"map_assignments 引用不存在的地图 '{map_id}'")

            for tier in ["main", "secondary"]:
                for char_id in assignments.get(tier, []):
                    if char_id not in char_ids:
                        errors.append(
                            f"map_assignments[{map_id}][{tier}] 引用不存在的角色 '{char_id}'"
                        )

        return errors

    def get_characters_by_tier(
        self,
        characters_data: CharactersData,
        tier: NPCTier
    ) -> List[CharacterInfo]:
        """按层级获取角色"""
        return [c for c in characters_data.characters if c.tier == tier]

    def get_characters_for_map(
        self,
        characters_data: CharactersData,
        map_id: str
    ) -> Dict[str, List[CharacterInfo]]:
        """获取指定地图的角色分组"""
        result = {"main": [], "secondary": [], "passerby": []}

        # 从 map_assignments 获取
        assignments = characters_data.map_assignments.get(map_id, {})
        char_by_id = {c.id: c for c in characters_data.characters}

        for tier in ["main", "secondary"]:
            for char_id in assignments.get(tier, []):
                if char_id in char_by_id:
                    result[tier].append(char_by_id[char_id])

        # 也检查 default_map
        for char in characters_data.characters:
            if char.default_map == map_id:
                tier_key = char.tier.value
                if tier_key in result and char not in result[tier_key]:
                    result[tier_key].append(char)

        return result

    def to_character_profiles(
        self,
        characters_data: CharactersData
    ) -> Dict[str, Dict[str, Any]]:
        """
        转换为 CharacterProfile 格式（用于 Firestore）

        Returns:
            {character_id: profile_dict}
        """
        profiles = {}
        for char in characters_data.characters:
            if char.tier == NPCTier.PASSERBY:
                continue  # 路人不需要独立 profile

            profiles[char.id] = {
                "name": char.name,
                "occupation": char.occupation,
                "age": char.age,
                "personality": char.personality,
                "speech_pattern": char.speech_pattern,
                "example_dialogue": char.example_dialogue,
                "metadata": {
                    "appearance": char.appearance,
                    "backstory": char.backstory,
                    "aliases": char.aliases,
                    "importance": char.importance,
                    "tags": char.tags,
                    "default_map": char.default_map,
                    "default_sub_location": char.default_sub_location,
                    "tier": char.tier.value,
                }
            }

        return profiles
