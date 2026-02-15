"""
Snapshot Persistence -- Step C6

世界活图快照持久化：将 WorldGraph 运行时状态序列化/反序列化。
纯序列化逻辑，不含 Firestore I/O（由调用方负责读写）。

设计文档: 架构与设计/世界底层重构与战斗系统设计专项/世界活图详细设计.md §9

=== 快照生命周期 ===

新建会话:
  GraphBuilder.build() → wg.seal() → WorldGraph（干净状态）

游戏过程:
  BehaviorEngine.tick() / handle_event() → 状态变更、SPAWN、REMOVE、边变更
  ↓ 追踪记录在 WorldGraph 内部集合中

保存:
  capture_snapshot(wg, ...) → WorldSnapshot → snapshot_to_dict() → Dict
  → 调用方写入 Firestore: worlds/{wid}/sessions/{sid}/world_snapshot

恢复会话:
  GraphBuilder.build() → wg.seal()
  → dict_to_snapshot(firestore_data) → WorldSnapshot
  → restore_snapshot(wg, snapshot) → 重现运行时状态
"""
from __future__ import annotations

import copy
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.world.models import WorldNode
from app.world.world_graph import EdgeChange, WorldGraph

logger = logging.getLogger(__name__)


# =============================================================================
# Models
# =============================================================================


class EdgeChangeRecord(BaseModel):
    """序列化的边变更记录。"""
    operation: str          # "add" | "update" | "remove"
    source: str = ""
    target: str = ""
    key: str = ""
    relation: str = ""
    attrs: Dict[str, Any] = Field(default_factory=dict)


class WorldSnapshot(BaseModel):
    """世界状态快照 — 只存可变部分。

    不含 behavior_states — 行为状态已嵌入 node.state
    （behavior_fired / behavior_cooldowns 通过 node_states 自动持久化）。
    """
    world_id: str
    session_id: str
    created_at: datetime = Field(default_factory=datetime.now)
    game_day: int = 1
    game_hour: int = 8

    # 脏节点的 state 快照 {node_id: state_dict}
    node_states: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    # 运行期动态创建的节点（完整 model_dump）
    spawned_nodes: List[Dict[str, Any]] = Field(default_factory=list)

    # 运行期动态移除的节点 ID
    removed_node_ids: List[str] = Field(default_factory=list)

    # 压缩后的边变更记录
    modified_edges: List[EdgeChangeRecord] = Field(default_factory=list)


# =============================================================================
# Capture
# =============================================================================


def _compact_edge_changes(changes: List[EdgeChange]) -> List[EdgeChangeRecord]:
    """压缩边变更日志：同 key 多次操作合并，add+remove 抵消。

    压缩规则:
    - 同一 (source, target, key) 的多次操作，保留最终状态
    - add → remove = 抵消（不记录）
    - add → update = add（合并 attrs）
    - update → update = update（合并 attrs）
    - update → remove = remove
    """
    # {(source, target, key): EdgeChangeRecord}
    merged: Dict[tuple, EdgeChangeRecord] = {}

    for ch in changes:
        edge_key = (ch.source, ch.target, ch.key)

        if edge_key not in merged:
            merged[edge_key] = EdgeChangeRecord(
                operation=ch.operation,
                source=ch.source,
                target=ch.target,
                key=ch.key,
                relation=ch.relation,
                attrs=dict(ch.attrs),
            )
            continue

        existing = merged[edge_key]

        if ch.operation == "remove":
            if existing.operation == "add":
                # add + remove = 净零
                del merged[edge_key]
            else:
                # update + remove = remove
                existing.operation = "remove"
                existing.attrs = {}
        elif ch.operation == "update":
            if existing.operation == "add":
                # add + update = add with merged attrs
                existing.attrs.update(ch.attrs)
            else:
                # update + update = update with merged attrs
                existing.attrs.update(ch.attrs)
        elif ch.operation == "add":
            # 如果之前已 remove 了同 key 的边，再 add = 相当于 update
            if existing.operation == "remove":
                existing.operation = "add"
                existing.relation = ch.relation
                existing.attrs = dict(ch.attrs)
            else:
                # add + add 不应发生，覆盖
                existing.attrs.update(ch.attrs)

    return list(merged.values())


def capture_snapshot(
    wg: WorldGraph,
    world_id: str,
    session_id: str,
    game_day: int = 1,
    game_hour: int = 8,
) -> WorldSnapshot:
    """从 WorldGraph 捕获当前运行时状态快照。

    捕获内容:
    1. node_states: dirty 节点的 state 深拷贝
    2. spawned_nodes: 运行期新增节点的完整数据（取当前状态）
    3. removed_node_ids: 运行期删除的节点（排除 spawn→remove 净零）
    4. modified_edges: 压缩后的边变更记录
    """
    snapshot = WorldSnapshot(
        world_id=world_id,
        session_id=session_id,
        game_day=game_day,
        game_hour=game_hour,
    )

    # 1. node_states — dirty 节点的 state
    for nid in wg._dirty_nodes:
        node = wg.get_node(nid)
        if node:
            snapshot.node_states[nid] = copy.deepcopy(node.state)

    # 2. spawned_nodes — 排除已被 remove 的，取当前最新状态
    for nid, _original in wg._spawned_nodes.items():
        if nid in wg._removed_node_ids:
            continue  # spawn → remove = 净零
        current_node = wg.get_node(nid)
        if current_node:
            snapshot.spawned_nodes.append(current_node.model_dump())

    # 3. removed_node_ids — 排除 spawn→remove 净零
    for nid in wg._removed_node_ids:
        if nid not in wg._spawned_nodes:
            snapshot.removed_node_ids.append(nid)

    # 4. modified_edges — 压缩
    snapshot.modified_edges = _compact_edge_changes(wg._edge_changes)

    return snapshot


# =============================================================================
# Restore
# =============================================================================


def restore_snapshot(wg: WorldGraph, snapshot: WorldSnapshot) -> None:
    """将快照恢复到已 build+seal 的 WorldGraph。

    恢复顺序（有依赖关系）:
      Phase 1: 临时解除 seal（避免恢复操作被追踪）
      Phase 2: 重建 spawned 节点（后续 edge/state 恢复需要节点存在）
      Phase 3: 删除 removed 节点
      Phase 4: 重放 edge 变更（节点必须已存在）
      Phase 5: 恢复 node states（最后执行，覆盖前面步骤可能产生的副作用）
      Phase 6: 重新 seal + clear_dirty
    """
    # Phase 1: 临时解除 seal
    wg._sealed = False

    # Phase 2: 重建 spawned 节点
    for node_data in snapshot.spawned_nodes:
        try:
            node = WorldNode(**node_data)
            wg.add_node(node)
        except Exception as exc:
            logger.warning(
                "[Snapshot] 恢复 spawned 节点失败 '%s': %s",
                node_data.get("id", "?"), exc,
            )

    # Phase 3: 删除 removed 节点
    for nid in snapshot.removed_node_ids:
        if wg.has_node(nid):
            try:
                wg.remove_node(nid)
            except KeyError:
                logger.warning(
                    "[Snapshot] 恢复时删除节点 '%s' 失败: 不存在", nid,
                )
        else:
            logger.warning(
                "[Snapshot] 恢复时节点 '%s' 不存在，跳过删除", nid,
            )

    # Phase 4: 重放 edge 变更
    for ec in snapshot.modified_edges:
        try:
            if ec.operation == "add":
                if wg.has_node(ec.source) and wg.has_node(ec.target):
                    # one_way=True 仅对 CONNECTS 有意义：防止重复创建反向边
                    # 对非 CONNECTS 边不传入，避免污染边数据
                    extra = {}
                    if ec.relation == "connects":
                        extra["one_way"] = True
                    wg.add_edge(
                        ec.source, ec.target, ec.relation,
                        key=ec.key, **ec.attrs, **extra,
                    )
                else:
                    logger.warning(
                        "[Snapshot] 恢复边 add (%s→%s) 时节点不存在",
                        ec.source, ec.target,
                    )
            elif ec.operation == "update":
                if wg.graph.has_edge(ec.source, ec.target, ec.key):
                    wg.update_edge(ec.source, ec.target, ec.key, ec.attrs)
                else:
                    logger.warning(
                        "[Snapshot] 恢复边 update (%s→%s, key=%s) 时边不存在",
                        ec.source, ec.target, ec.key,
                    )
            elif ec.operation == "remove":
                if (wg.has_node(ec.source) and wg.has_node(ec.target)
                        and wg.graph.has_edge(ec.source, ec.target, ec.key)):
                    wg.remove_edge(ec.source, ec.target, ec.key)
                else:
                    logger.warning(
                        "[Snapshot] 恢复边 remove (%s→%s, key=%s) 时边不存在",
                        ec.source, ec.target, ec.key,
                    )
        except Exception as exc:
            logger.warning(
                "[Snapshot] 恢复边变更失败 (%s %s→%s): %s",
                ec.operation, ec.source, ec.target, exc,
            )

    # Phase 5: 恢复 node states（最后执行，覆盖副作用）
    for nid, state in snapshot.node_states.items():
        node = wg.get_node(nid)
        if node:
            node.state = copy.deepcopy(state)
        else:
            logger.warning(
                "[Snapshot] 恢复 state 时节点 '%s' 不存在，跳过", nid,
            )

    # Phase 6: 重新 seal + clear_dirty
    wg._sealed = True
    wg.clear_dirty()


# =============================================================================
# Serialization
# =============================================================================


def _convert_datetimes(obj: Any) -> Any:
    """递归将嵌套结构中的 datetime 转为 ISO string。"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _convert_datetimes(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_datetimes(item) for item in obj]
    return obj


def snapshot_to_dict(snapshot: WorldSnapshot) -> Dict[str, Any]:
    """WorldSnapshot → Firestore 兼容 dict（datetime → ISO string，含嵌套）。"""
    data = snapshot.model_dump()
    return _convert_datetimes(data)


def dict_to_snapshot(data: Dict[str, Any]) -> Optional[WorldSnapshot]:
    """Firestore dict → WorldSnapshot。无效数据返回 None。"""
    if not data or not isinstance(data, dict):
        return None
    try:
        # ISO string → datetime
        if isinstance(data.get("created_at"), str):
            data = dict(data)  # 防止修改原 dict
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        return WorldSnapshot(**data)
    except Exception as exc:
        logger.warning("[Snapshot] 反序列化失败: %s", exc)
        return None
