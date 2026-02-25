"""Agentic 系统提示加载器（从 FlashCPUService 提取）。"""
from __future__ import annotations

from pathlib import Path

_PROMPT: str | None = None
_DEFAULT_PATH = Path("app/prompts/flash_agentic_system.md")
_FALLBACK = (
    "你是一个 RPG GM。先使用工具执行必要操作，再输出2-4段中文叙述。"
    "叙述必须基于工具真实返回结果，不要编造。"
)


def load_agentic_prompt(path: Path | None = None) -> str:
    global _PROMPT
    if _PROMPT is None:
        p = path or _DEFAULT_PATH
        if p.exists():
            _PROMPT = p.read_text(encoding="utf-8")
        else:
            _PROMPT = _FALLBACK
    return _PROMPT
