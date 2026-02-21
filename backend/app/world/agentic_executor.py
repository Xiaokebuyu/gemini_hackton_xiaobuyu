"""AgenticExecutor — 统一 Agent 执行器。

GM / NPC / 队友共用。将 RoleRegistry 工具 + LLMService agentic 循环
封装为单一 run() 接口。

Usage::

    executor = AgenticExecutor(llm_service)
    result = await executor.run(
        ctx=ctx,
        system_prompt="You are a tavern keeper...",
        user_prompt="The adventurer asks about rumors.",
    )
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set

from app.models.admin_protocol import AgenticResult, AgenticToolCall

logger = logging.getLogger(__name__)

_PASS_MARKERS = frozenset({"[PASS]", "[pass]", "[Pass]"})


class AgenticExecutor:
    """统一 Agent 执行器 — GM/NPC/队友共用。"""

    def __init__(self, llm_service: Any):
        self.llm = llm_service

    async def run(
        self,
        *,
        ctx: Any,
        system_prompt: str,
        user_prompt: str,
        traits: Optional[Set[str]] = None,
        extra_tools: Optional[List[Callable]] = None,
        exclude_tools: Optional[Set[str]] = None,
        event_queue: Optional[asyncio.Queue] = None,
        model_override: Optional[str] = None,
        thinking_level: Optional[str] = None,
        max_tool_rounds: int = 5,
    ) -> AgenticResult:
        """执行一次 agentic 会话。

        Args:
            ctx: AgenticContext — 包含 session/agent_id/role/scene_bus 等
            system_prompt: 系统提示
            user_prompt: 用户提示（通常是 SceneBus 摘要 + 玩家输入）
            traits: NPC 特质集合
            extra_tools: 额外工具（如战斗工具），已绑定，直接追加
            exclude_tools: 工具名黑名单（引擎已执行的操作对应的工具）
            event_queue: SSE 事件队列
            model_override: 模型覆盖
            thinking_level: thinking 级别
            max_tool_rounds: 最大工具调用轮数

        Returns:
            AgenticResult
        """
        from app.world.role_registry import RoleRegistry

        # 1. 收集工具
        tools = RoleRegistry.get_tools(ctx.role, traits, ctx=ctx)
        if exclude_tools:
            tools = [t for t in tools if t.__name__ not in exclude_tools]
        if extra_tools:
            tools.extend(extra_tools)

        # 2. 包装录制
        tool_calls: List[AgenticToolCall] = []
        wrapped = [self._wrap_recording(t, tool_calls, event_queue) for t in tools]

        # 3. 调用 LLM agentic 循环
        resp = await self.llm.agentic_generate(
            user_prompt=user_prompt,
            system_instruction=system_prompt,
            tools=wrapped,
            model_override=model_override,
            thinking_level=thinking_level,
            max_remote_calls=max_tool_rounds,
        )

        # 4. 提取结果
        narration = resp.text or ""

        # 5. [PASS] 检测
        if narration.strip() in _PASS_MARKERS:
            narration = ""

        # 6. 构建 usage
        usage = {}
        if resp.thinking:
            usage = {
                "thinking_token_count": resp.thinking.thoughts_token_count,
                "output_token_count": resp.thinking.output_token_count,
                "total_token_count": resp.thinking.total_token_count,
            }

        return AgenticResult(
            narration=narration,
            thinking_summary=resp.thinking.thoughts_summary if resp.thinking else "",
            tool_calls=tool_calls,
            usage=usage,
        )

    def _wrap_recording(
        self,
        tool_fn: Callable,
        tool_calls: List[AgenticToolCall],
        event_queue: Optional[asyncio.Queue],
    ) -> Callable:
        """包装工具函数：计时 + 录制 + SSE 推送。保留 Gemini SDK 所需的签名。"""

        @functools.wraps(tool_fn)
        async def wrapper(**kwargs):
            started = time.perf_counter()
            result: Optional[Dict[str, Any]] = None
            success = True
            error: Optional[str] = None

            try:
                result = await asyncio.wait_for(tool_fn(**kwargs), timeout=30)
            except asyncio.TimeoutError:
                result = {"success": False, "error": f"timeout: {tool_fn.__name__}"}
                success = False
                error = f"timeout: {tool_fn.__name__}"
            except Exception as e:
                result = {"success": False, "error": f"{type(e).__name__}: {e}"}
                success = False
                error = f"{type(e).__name__}: {e}"
                logger.warning(
                    "[AgenticExecutor] tool %s error: %s", tool_fn.__name__, e,
                )

            duration_ms = int((time.perf_counter() - started) * 1000)

            # 记录
            tool_calls.append(AgenticToolCall(
                name=tool_fn.__name__,
                args=kwargs,
                success=success,
                duration_ms=duration_ms,
                error=error,
                result=result or {},
            ))

            # SSE 推送
            if event_queue is not None:
                event: Dict[str, Any] = {
                    "type": "agentic_tool_call",
                    "name": tool_fn.__name__,
                    "success": success,
                    "duration_ms": duration_ms,
                    "tool_index": len(tool_calls),
                }
                if error:
                    event["error"] = error
                # disposition_change 特殊事件（NPC/队友版 + GM 版）
                if (
                    tool_fn.__name__ in ("react_to_interaction", "update_disposition")
                    and success
                    and isinstance(result, dict)
                    and "error" not in result
                ):
                    event["disposition_change"] = {
                        "npc_id": result.get("npc_id", ""),
                        "deltas": result.get("applied_deltas", {}),
                        "current": {
                            k: v for k, v in result.get("current", {}).items()
                            if k in ("approval", "trust", "fear", "romance")
                        } if isinstance(result.get("current"), dict) else {},
                    }
                try:
                    event_queue.put_nowait(event)
                except Exception:
                    pass  # 非阻塞，丢失可接受

            return result

        # 保留 Gemini SDK 所需的签名和注解
        wrapper.__annotations__ = tool_fn.__annotations__
        wrapper.__signature__ = inspect.signature(tool_fn)
        return wrapper
