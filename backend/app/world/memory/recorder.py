"""memory/recorder.py — 记忆写入 + 权限检查（纯 L3，无 I/O）。

从 session_runtime.py MemoryOps zone 提取。
"""
import uuid
from typing import Any

from app.world.graph.models import WorldNode


ROLE_WRITE_PERMISSIONS = {
    "gm": {"*"},
    "npc": {"impression"},
    "teammate": {"impression", "memory_event"},
}


def check_memory_write_permission(role: str, memory_type: str) -> None:
    """角色级写入权限校验（无 fallback）。"""
    normalized_role = (role or "").strip().lower()
    normalized_type = (memory_type or "").strip().lower()

    if normalized_role == "gm":
        return
    allowed = ROLE_WRITE_PERMISSIONS.get(normalized_role, set())
    if normalized_type in allowed:
        return
    raise PermissionError(
        f"role '{normalized_role}' cannot write memory_type '{normalized_type}'"
    )


def record_memory(
    world_graph,
    owner_id: str,
    memory_type: str,
    name: str,
    summary: str,
    importance: float,
    role: str,
    **props: Any,
) -> str:
    """向 WorldGraph 写入一条记忆节点 + has_memory 边。"""
    check_memory_write_permission(role, memory_type)

    owner = (owner_id or "").strip()
    if not owner:
        raise ValueError("owner_id is required")
    if not world_graph.has_node(owner):
        raise ValueError(f"owner node not found: {owner}")

    normalized_type = memory_type.strip().lower()
    node_id = props.pop("node_id", "") or f"{normalized_type}_{uuid.uuid4().hex[:12]}"

    resolved_name = (name or "").strip() or (summary or "").strip()[:80] or node_id
    resolved_importance = max(0.0, min(1.0, float(importance)))

    properties = dict(props)
    properties["owner"] = owner
    properties["summary"] = summary
    if normalized_type == "impression" and "content" not in properties:
        properties["content"] = summary

    node = WorldNode(
        id=node_id,
        type=normalized_type,
        name=resolved_name,
        importance=resolved_importance,
        properties=properties,
        state={},
        behaviors=[],
    )
    world_graph.add_node(node)
    world_graph.add_edge(
        owner, node_id, "has_memory", key=f"edge_{owner}_has_{node_id}"
    )
    return node_id
