"""
Pro DM service - narration/interaction layer.

Pro-First 架构：
1. parse_intent() - 解析玩家意图，生成 Flash 请求
2. narrate_v2() - 基于意图和执行结果生成叙述
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.models.admin_protocol import (
    FlashOperation,
    FlashRequest,
    FlashResponse,
    IntentParseResult,
    IntentType,
    ParsedIntent,
    ProResponse,
)
from app.services.llm_service import LLMService


class ProDMService:
    """Pro DM service with intent parsing and narration."""

    def __init__(
        self,
        llm_service: Optional[LLMService] = None,
        system_prompt_path: Optional[Path] = None,
    ) -> None:
        self.llm_service = llm_service or LLMService()
        self.system_prompt_path = system_prompt_path or Path("app/prompts/pro_dm_system.md")
        self.intent_prompt_path = Path("app/prompts/intent_parse.md")

    def _load_system_prompt(self) -> str:
        if self.system_prompt_path.exists():
            return self.system_prompt_path.read_text(encoding="utf-8")
        return "你是叙述者（GM），负责以沉浸式、简洁的方式描述世界与事件。"

    def _load_intent_prompt(self) -> str:
        if self.intent_prompt_path.exists():
            return self.intent_prompt_path.read_text(encoding="utf-8")
        return ""

    # =========================================================================
    # Pro-First: 意图解析
    # =========================================================================

    async def parse_intent(
        self,
        player_input: str,
        context: Dict[str, Any],
    ) -> IntentParseResult:
        """
        解析玩家意图，生成 Flash 请求。

        Args:
            player_input: 玩家输入
            context: 当前上下文，包含：
                - location: 当前位置信息
                - time: 当前时间
                - state: 当前状态（exploring/in_dialogue/combat）
                - active_npc: 当前对话NPC（如有）
                - teammates: 队友列表
                - available_destinations: 可用目的地

        Returns:
            IntentParseResult: 解析结果
        """
        # 快速路由：系统命令
        if player_input.startswith("/"):
            return self._parse_system_command(player_input)

        # 构建上下文字符串
        prompt_template = self._load_intent_prompt()
        if not prompt_template:
            # 无提示词模板，返回默认 roleplay
            return self._default_roleplay_intent(player_input)

        # 填充上下文
        location = context.get("location") or {}
        time_info = context.get("time") or {}
        state = context.get("state", "exploring")
        active_npc = context.get("active_npc")
        teammates = context.get("teammates") or []
        available_destinations = context.get("available_destinations") or []
        sub_locations = context.get("sub_locations") or location.get("sub_locations") or []

        filled_prompt = prompt_template.format(
            location_name=location.get("location_name", "未知地点"),
            available_destinations=", ".join(
                d.get("name", d.get("id", str(d)))
                if isinstance(d, dict) else str(d)
                for d in available_destinations
            ) or "无",
            sub_locations=", ".join(
                f"{s.get('name', s.get('id', str(s)))}({s.get('id', '')})"
                if isinstance(s, dict) else str(s)
                for s in sub_locations
            ) or "无",
            npcs_present=", ".join(location.get("npcs_present", [])) or "无",
            teammates=", ".join(t.get("name", t) if isinstance(t, dict) else str(t) for t in teammates) or "无",
            time=time_info.get("formatted", "未知"),
            current_state=state,
            active_npc=active_npc or "无",
            player_input=player_input,
        )

        # 调用 LLM 解析
        try:
            import time as _time
            print(f"[ProDM] 开始意图解析: '{player_input[:50]}...'")
            start = _time.time()
            result = await self.llm_service.generate_simple(
                filled_prompt,
                model_override=settings.gemini_flash_model,
            )
            elapsed = (_time.time() - start) * 1000
            print(f"[ProDM] LLM 返回 ({elapsed:.0f}ms): {result[:100] if result else 'empty'}...")
            parsed = self.llm_service.parse_json(result)
            if parsed:
                return self._build_intent_result(parsed, player_input, context)
            else:
                print(f"[ProDM] JSON 解析返回空: {result[:200] if result else 'empty'}")
        except Exception as e:
            import traceback
            print(f"[ProDM] 意图解析失败: {e}")
            print(f"[ProDM] {traceback.format_exc()}")

        # 解析失败，返回默认 roleplay
        return self._default_roleplay_intent(player_input)

    def _parse_system_command(self, player_input: str) -> IntentParseResult:
        """解析系统命令"""
        cmd_parts = player_input[1:].split(maxsplit=1)
        cmd = cmd_parts[0].lower() if cmd_parts else ""
        arg = cmd_parts[1] if len(cmd_parts) > 1 else ""

        intent = ParsedIntent(
            intent_type=IntentType.SYSTEM_COMMAND,
            confidence=1.0,
            target=cmd,
            action=cmd,
            parameters={"command": cmd, "argument": arg},
            raw_input=player_input,
        )
        return IntentParseResult(primary_intent=intent)

    def _default_roleplay_intent(self, player_input: str) -> IntentParseResult:
        """返回默认角色扮演意图"""
        intent = ParsedIntent(
            intent_type=IntentType.ROLEPLAY,
            confidence=0.5,
            raw_input=player_input,
            interpretation="无法解析，作为角色扮演处理",
        )
        return IntentParseResult(primary_intent=intent)

    def _build_intent_result(
        self,
        parsed: Dict[str, Any],
        player_input: str,
        context: Dict[str, Any],
    ) -> IntentParseResult:
        """从 LLM 解析结果构建 IntentParseResult"""
        intent_type_str = parsed.get("intent_type", "roleplay")
        try:
            intent_type = IntentType(intent_type_str)
        except ValueError:
            intent_type = IntentType.ROLEPLAY

        # 构建 FlashRequest 列表
        flash_requests = []
        for req_data in parsed.get("flash_requests", []):
            try:
                op_str = req_data.get("operation", "")
                operation = FlashOperation(op_str)
                flash_requests.append(FlashRequest(
                    operation=operation,
                    parameters=req_data.get("parameters", {}),
                    priority=req_data.get("priority", "normal"),
                ))
            except (ValueError, KeyError):
                continue

        intent = ParsedIntent(
            intent_type=intent_type,
            confidence=parsed.get("confidence", 0.8),
            target=parsed.get("target"),
            action=parsed.get("action"),
            parameters=parsed.get("parameters", {}),
            raw_input=player_input,
            interpretation=parsed.get("interpretation"),
            player_emotion=parsed.get("player_emotion"),
            flash_requests=flash_requests,
        )

        return IntentParseResult(
            primary_intent=intent,
            context_used=context,
            reasoning=parsed.get("reasoning"),
        )

    # =========================================================================
    # Pro-First: 基于意图的叙述
    # =========================================================================

    async def narrate_v2(
        self,
        player_input: str,
        intent_result: IntentParseResult,
        flash_results: List[FlashResponse],
        context: Optional[Dict[str, Any]] = None,
    ) -> ProResponse:
        """
        基于意图解析结果和 Flash 执行结果生成叙述。

        Args:
            player_input: 玩家输入
            intent_result: 意图解析结果
            flash_results: Flash 执行结果列表
            context: 额外上下文

        Returns:
            ProResponse: 叙述响应
        """
        intent = intent_result.primary_intent

        # 系统命令：直接返回结果
        if intent.intent_type == IntentType.SYSTEM_COMMAND:
            return self._narrate_system_command(intent, flash_results)

        # 检查是否有 Flash 结果包含现成叙述
        for flash_result in flash_results:
            if flash_result.result.get("narration"):
                return ProResponse(
                    narration=flash_result.result["narration"],
                    speaker="GM",
                    metadata={
                        "source": "flash",
                        "intent_type": intent.intent_type.value,
                    },
                )

        # 构建叙述提示
        system_prompt = self._load_system_prompt()
        context = context or {}

        # 构建执行结果摘要
        results_summary = self._summarize_flash_results(flash_results)

        # 构建上下文
        location = context.get("location") or {}
        time_info = context.get("time") or {}

        narration_prompt = f"""{system_prompt}

## 当前场景
- 位置: {location.get("location_name", "未知")}
- 氛围: {location.get("atmosphere", "")}
- 时间: {time_info.get("formatted", "")}

## 玩家行动
意图类型: {intent.intent_type.value}
玩家输入: {player_input}
玩家情绪: {intent.player_emotion or "未知"}
你的理解: {intent.interpretation or ""}

## 系统执行结果
{results_summary}

## 任务
请基于以上信息，以 GM 的视角生成简洁、沉浸式的叙述。
- 如果是导航，描述旅途和到达
- 如果是对话，可以简单过渡或整合 NPC 的回应
- 如果是角色扮演，回应玩家的行动并推动剧情
- 保持简洁，一般 2-4 句话即可
"""

        try:
            import time as _time
            print(f"[ProDM] 开始生成叙述...")
            start = _time.time()
            response = await self.llm_service.generate_response(
                context=narration_prompt,
                user_query=f"玩家: {player_input}",
                thinking_level=settings.admin_pro_thinking_level,
            )
            elapsed = (_time.time() - start) * 1000
            print(f"[ProDM] 叙述生成完成 ({elapsed:.0f}ms)")
            return ProResponse(
                narration=response.text,
                speaker="GM",
                metadata={
                    "source": "pro_dm_v2",
                    "intent_type": intent.intent_type.value,
                    "confidence": intent.confidence,
                },
            )
        except Exception as e:
            import traceback
            print(f"[ProDM] 叙述生成失败: {e}")
            print(f"[ProDM] {traceback.format_exc()}")
            # 降级：返回简单叙述
            return ProResponse(
                narration=f"[系统] 处理出错: {e}",
                speaker="GM",
                metadata={"error": str(e)},
            )

    def _narrate_system_command(
        self,
        intent: ParsedIntent,
        flash_results: List[FlashResponse],
    ) -> ProResponse:
        """处理系统命令的叙述"""
        cmd = intent.parameters.get("command", "")

        # 检查 Flash 结果
        for result in flash_results:
            if result.result.get("response"):
                return ProResponse(
                    narration=result.result["response"],
                    speaker="System",
                    metadata={"command": cmd},
                )

        return ProResponse(
            narration=f"[系统] 命令 /{cmd} 已处理",
            speaker="System",
            metadata={"command": cmd},
        )

    def _summarize_flash_results(self, flash_results: List[FlashResponse]) -> str:
        """汇总 Flash 执行结果"""
        if not flash_results:
            return "无系统操作执行"

        summaries = []
        for result in flash_results:
            op = result.operation.value if result.operation else "unknown"
            if result.success:
                detail = result.result.get("summary", str(result.result)[:100])
                summaries.append(f"- [{op}] 成功: {detail}")
            else:
                summaries.append(f"- [{op}] 失败: {result.error or '未知错误'}")

            # 包含 NPC 反应
            if result.npc_reactions:
                for npc in result.npc_reactions:
                    summaries.append(f"  - {npc.name or npc.npc_id}: {npc.response[:50]}...")

        return "\n".join(summaries) if summaries else "无系统操作执行"

    async def narrate(
        self,
        player_input: str,
        flash_result: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> ProResponse:
        """Generate narration. If flash_result already contains narration, passthrough."""
        if flash_result and flash_result.get("response"):
            return ProResponse(
                narration=flash_result.get("response", ""),
                speaker=flash_result.get("speaker", "GM"),
                metadata={"source": "legacy_gm"},
            )

        system_prompt = self._load_system_prompt()
        extra_context = ""
        if context:
            location = context.get("location") or {}
            time_info = context.get("time") or {}
            loc_name = location.get("location_name") or location.get("location_id") or "未知地点"
            atmosphere = location.get("atmosphere") or ""
            time_text = time_info.get("formatted") or time_info.get("formatted_time") or ""
            extra_context = (
                f"当前地点: {loc_name}\n"
                f"环境氛围: {atmosphere}\n"
                f"当前时间: {time_text}\n"
            )

        full_context = f"""{system_prompt}

{extra_context}
"""
        response = await self.llm_service.generate_response(
            context=full_context,
            user_query=player_input,
            thinking_level=getattr(settings, "admin_pro_thinking_level", None),
        )

        return ProResponse(narration=response.text, metadata={"source": "pro_dm"})
