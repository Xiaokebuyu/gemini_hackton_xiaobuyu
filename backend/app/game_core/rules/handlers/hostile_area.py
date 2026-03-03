"""Hostile Area 敌对区域进入 handler（noop 占位）。

设计意图：
- enter_sub_location 时目标有 hostile_config 且未清理 → 自动潜行检定
  d20 + DEX_mod + (proficiency if stealth)，dc = stealth_dc + (5 if alert)
  重甲劣势，夜间优势
- 成功 → ambush / stealth_pass / retreat 选项
- 失败 → 正常战斗 / counter-ambush（临界失败 + role=ambush）
- blocking=True → 必须击败才能离开

当前状态：noop 占位。
"""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateContainer


class HostileAreaHandler(StaticCommandHandler):
    COMMAND_TYPES = ("enter_hostile",)

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
            metadata={"status": "deferred", "reason": "hostile area system not yet implemented"},
        )
