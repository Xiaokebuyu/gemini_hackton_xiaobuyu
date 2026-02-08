"""
地图/箱庭提取器

从世界书中提取地点信息，生成结构化的地图数据。
"""
import json
import re
from pathlib import Path
from typing import Dict, Any, Optional, List

from google import genai
from google.genai import types

from app.config import settings
from .models import MapInfo, MapConnection, PasserbyTemplate, MapsData, SubLocationInfo


class MapExtractor:
    """地图提取器"""

    def __init__(self, model: str = None, api_key: str = None):
        """
        初始化地图提取器

        Args:
            model: Gemini 模型名称（默认使用配置中的 flash 模型）
            api_key: API 密钥（默认使用配置）
        """
        self.client = genai.Client(api_key=api_key or settings.gemini_api_key)
        self.model = model or "gemini-3-pro-preview"  # 使用支持 1M context 的模型
        self.prompt_template = self._load_prompt_template()

    def _load_prompt_template(self) -> str:
        """加载 prompt 模板"""
        template_path = Path(__file__).parent / "prompts" / "map_extraction.md"
        if template_path.exists():
            return template_path.read_text(encoding="utf-8")

        # Fallback inline template
        return """请从以下世界书内容中提取所有地点/地图信息。

输出 JSON 格式：
{
  "maps": [...],
  "passerby_templates": {...}
}

世界书内容：
{worldbook_content}
"""

    def _fix_json_string(self, text: str) -> str:
        """修复常见的 JSON 问题"""
        # 移除尾部逗号
        text = re.sub(r',(\s*[}\]])', r'\1', text)
        return text

    def _parse_json(self, text: str) -> Optional[Dict[str, Any]]:
        """解析 JSON 响应"""
        text = text.strip()

        def try_parse(s: str) -> Optional[Dict]:
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                try:
                    return json.loads(self._fix_json_string(s))
                except json.JSONDecodeError:
                    return None

        # 直接尝试解析
        result = try_parse(text)
        if result:
            return result

        # 尝试从代码块中提取
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

        # 使用正则查找 JSON 对象
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            result = try_parse(match.group(0))
            if result:
                return result

        return None

    async def extract(self, worldbook_content: str) -> MapsData:
        """
        从世界书内容中提取地图数据

        Args:
            worldbook_content: 完整的世界书 Markdown 内容

        Returns:
            MapsData: 结构化的地图数据
        """
        # 构建 prompt
        prompt = self.prompt_template.replace("{worldbook_content}", worldbook_content)

        # 配置 JSON 输出
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
        )

        # 调用 LLM
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=config,
        )

        # 提取响应文本
        text = ""
        if hasattr(response, 'candidates') and response.candidates:
            for part in response.candidates[0].content.parts:
                if hasattr(part, 'text') and part.text:
                    if not (hasattr(part, 'thought') and part.thought):
                        text += part.text

        # 解析 JSON
        raw_data = self._parse_json(text)
        if not raw_data:
            raise ValueError(f"Failed to parse map extraction response: {text[:500]}...")

        # 转换为数据模型
        return self._convert_to_model(raw_data)

    def _convert_to_model(self, raw_data: Dict[str, Any]) -> MapsData:
        """将原始数据转换为数据模型"""
        maps = []
        for map_data in raw_data.get("maps", []):
            # 转换连接
            connections = []
            for conn in map_data.get("connections", []):
                connections.append(MapConnection(
                    target_map_id=conn.get("target_map_id", ""),
                    connection_type=conn.get("connection_type", "walk"),
                    travel_time=conn.get("travel_time"),
                    requirements=conn.get("requirements"),
                ))

            # Task #19: 转换子地点
            sub_locations = []
            for sl_data in map_data.get("sub_locations", []):
                sub_locations.append(SubLocationInfo(
                    id=sl_data.get("id", ""),
                    name=sl_data.get("name", ""),
                    description=sl_data.get("description", ""),
                    interaction_type=sl_data.get("interaction_type", "visit"),
                    resident_npcs=sl_data.get("resident_npcs", []),
                    available_actions=sl_data.get("available_actions", []),
                    passerby_spawn_rate=sl_data.get("passerby_spawn_rate", 0.3),
                    travel_time_minutes=sl_data.get("travel_time_minutes", 0),
                ))

            maps.append(MapInfo(
                id=map_data.get("id", ""),
                name=map_data.get("name", ""),
                description=map_data.get("description", ""),
                atmosphere=map_data.get("atmosphere"),
                danger_level=map_data.get("danger_level", "low"),
                region=map_data.get("region"),
                connections=connections,
                available_actions=map_data.get("available_actions", []),
                key_features=map_data.get("key_features", []),
                sub_locations=sub_locations,  # Task #19: 保留子地点数据
            ))

        # 转换路人模板
        passerby_templates = {}
        for map_id, templates in raw_data.get("passerby_templates", {}).items():
            passerby_templates[map_id] = [
                PasserbyTemplate(
                    template_id=t.get("template_id", ""),
                    name_pattern=t.get("name_pattern", ""),
                    personality_template=t.get("personality_template", ""),
                    speech_pattern=t.get("speech_pattern"),
                    appearance_hints=t.get("appearance_hints"),
                )
                for t in templates
            ]

        return MapsData(maps=maps, passerby_templates=passerby_templates)

    def validate(self, maps_data: MapsData) -> List[str]:
        """
        验证地图数据

        Args:
            maps_data: 地图数据

        Returns:
            验证错误列表（空列表表示验证通过）
        """
        errors = []
        map_ids = {m.id for m in maps_data.maps}

        # 检查 ID 唯一性
        if len(map_ids) != len(maps_data.maps):
            errors.append("存在重复的地图 ID")

        # 检查连接有效性
        for map_info in maps_data.maps:
            for conn in map_info.connections:
                if conn.target_map_id not in map_ids:
                    errors.append(
                        f"地图 '{map_info.id}' 连接到不存在的地图 '{conn.target_map_id}'"
                    )

        # 检查必填字段
        for map_info in maps_data.maps:
            if not map_info.id:
                errors.append("存在空的地图 ID")
            if not map_info.name:
                errors.append(f"地图 '{map_info.id}' 缺少名称")
            if not map_info.description:
                errors.append(f"地图 '{map_info.id}' 缺少描述")

        return errors

    def get_map_ids(self, maps_data: MapsData) -> List[str]:
        """获取所有地图 ID"""
        return [m.id for m in maps_data.maps]

    def format_maps_for_context(self, maps_data: MapsData) -> str:
        """
        将地图数据格式化为上下文字符串（用于 NPC 分类）

        Args:
            maps_data: 地图数据

        Returns:
            格式化的地图列表字符串
        """
        lines = []
        for m in maps_data.maps:
            lines.append(f"- {m.id}: {m.name} ({m.region or '未知区域'})")
        return "\n".join(lines)
