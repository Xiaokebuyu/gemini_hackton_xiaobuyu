"""Interactable 交互物系统 handler（noop 占位）。

设计意图：
- 玩家选择交互物 → 展示 CheckPath 列表（OR 分支）
- 玩家选择 check path → 技能检定 vs dc
- 成功 → reward + mark one_time；失败 → fail_consequence
- container 特殊路径：locked/breakable/trap 各有对应检定
- 注：container.py 的现有 interact_object 保持不动，本 handler 是未来替代目标

当前状态：noop 占位，command_type 使用 interact_object_v2 避免冲突。
"""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateContainer


class InteractableHandler(StaticCommandHandler):
    COMMAND_TYPES = ("interact_object_v2",)

    def validate(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ValidationResult:
        return ValidationResult(ok=True)

    def compute(
        self,
        cmd: Command,
        state: StateContainer,
        world: WorldInstance,
    ) -> ExecuteResult:
        return ExecuteResult(
            success=True,
            metadata={"status": "deferred", "reason": "interactable system not yet implemented"},
        )
