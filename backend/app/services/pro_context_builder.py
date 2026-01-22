"""
Pro context builder.
"""
from typing import Dict, Optional

from app.models.flash import RecallResponse
from app.models.pro import CharacterProfile, SceneContext


class ProContextBuilder:
    """Build prompt text for Pro."""

    def build_prompt(
        self,
        profile: CharacterProfile,
        state: Dict,
        scene: SceneContext,
        memory: Optional[RecallResponse],
        recent_conversation: Optional[str] = None,
    ) -> str:
        memory_text = self._format_memory(memory)
        state_text = self._format_state(state)

        sections = [
            f"# 你是 {profile.name or '未知角色'}",
            "",
            "## 基本信息",
            f"- 职业: {profile.occupation or ''}",
            f"- 年龄: {profile.age if profile.age is not None else ''}",
            f"- 所在地: {scene.location or ''}",
            "",
            "## 性格特点",
            profile.personality or "",
            "",
            "## 说话风格",
            profile.speech_pattern or "",
            f"例句: \"{profile.example_dialogue or ''}\"",
            "",
            "## 当前状态",
            state_text,
            "",
            "## 你知道的重要事情",
            memory_text,
            "",
            "## 当前场景",
            scene.description or "",
            "",
            "## 在场的人",
            ", ".join(scene.present_characters) if scene.present_characters else "",
        ]

        if recent_conversation:
            sections.extend(["", "## 最近对话", recent_conversation])

        if profile.system_prompt:
            sections.extend(["", "## 系统提示补充", profile.system_prompt])

        return "\n".join(sections).strip()

    def _format_state(self, state: Dict) -> str:
        if not state:
            return "- 情绪: \n- 正在做: \n- 目标: "
        mood = state.get("mood", "")
        activity = state.get("current_activity", "")
        goals = state.get("active_goals", state.get("goals", ""))
        if isinstance(goals, list):
            goals = ", ".join(goals)
        return f"- 情绪: {mood}\n- 正在做: {activity}\n- 目标: {goals}"

    def _format_memory(self, memory: Optional[RecallResponse]) -> str:
        if not memory:
            return ""
        if not memory.subgraph or not memory.subgraph.nodes:
            return ""
        nodes = memory.subgraph.nodes
        nodes_sorted = sorted(
            nodes,
            key=lambda n: n.properties.get("activation", 0.0) if n.properties else 0.0,
            reverse=True,
        )
        lines = []
        for node in nodes_sorted[:10]:
            summary = ""
            if node.properties:
                summary = node.properties.get("summary") or node.properties.get("emotion") or ""
            lines.append(f"- [{node.type}] {node.name} {summary}".strip())
        return "\n".join(lines)
