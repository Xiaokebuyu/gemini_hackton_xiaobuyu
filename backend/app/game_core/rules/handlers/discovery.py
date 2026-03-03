"""Discovery 发现系统 handler（noop 占位）。

设计意图：
- 玩家进入新区域时自动被动感知检定（passive_perception = 10 + WIS_mod）
- 对 AreaTemplate.discoveries 每条 Discovery 检定 dc
  - 通过且未发现 → mark_discovery + 可能触发 DynamicSubAreaManager
  - 推送 SSE discovery_reveal
- InteractableTemplate.visibility_dc 决定 location_overview 显示

当前状态：noop 占位，等待深化。
"""

from __future__ import annotations

from app.game_core.content import WorldInstance
from app.game_core.rules.base import StaticCommandHandler
from app.game_core.rules.models import Command, ExecuteResult, ValidationResult
from app.game_core.state import StateContainer


class DiscoveryHandler(StaticCommandHandler):
    COMMAND_TYPES = ("discover", "passive_scan")

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
            metadata={"status": "deferred", "reason": "discovery system not yet implemented"},
        )
