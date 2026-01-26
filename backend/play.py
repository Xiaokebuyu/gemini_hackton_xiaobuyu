#!/usr/bin/env python3
"""
交互式 RPG 游戏客户端 (Claude Code 风格)

用法:
    ./venv/bin/python play.py
    ./venv/bin/python play.py --world goblin_slayer
"""
import asyncio
import argparse
import sys
import time
from typing import Optional, List

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown
from rich.table import Table
from rich.live import Live
from rich.spinner import Spinner
from rich.style import Style
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.formatted_text import HTML

from app.services.game_master_service import GameMasterService
from app.models.game import SceneState


console = Console()


class GameClient:
    """Claude Code 风格的交互式游戏客户端"""

    COMMANDS = {
        "/quit": "退出游戏",
        "/q": "退出游戏 (简写)",
        "/scene": "切换场景 - /scene <场景名>",
        "/status": "查看当前状态",
        "/talk": "开始对话 - /talk <NPC名>",
        "/end": "结束当前对话",
        "/day": "推进到下一天",
        "/instances": "查看 NPC 实例池状态",
        "/help": "显示帮助",
        "/clear": "清屏",
    }

    def __init__(self, world_id: str = "goblin_slayer"):
        self.world_id = world_id
        self.session_id: Optional[str] = None
        self.gm = GameMasterService()
        self.running = False
        self.prompt_session = PromptSession(
            history=InMemoryHistory(),
            auto_suggest=AutoSuggestFromHistory(),
        )

    def _get_status_text(self) -> str:
        """获取状态栏文本"""
        context = self.gm.get_context(self.world_id, self.session_id) if self.session_id else None
        if not context:
            return f"[dim]世界: {self.world_id}[/dim]"

        parts = [f"[cyan]{self.world_id}[/cyan]"]
        parts.append(f"[yellow]Day {context.game_day}[/yellow]")

        if context.current_scene and context.current_scene.location:
            parts.append(f"[green]{context.current_scene.location}[/green]")

        if context.current_npc:
            parts.append(f"[magenta]对话: {context.current_npc}[/magenta]")
            # 显示 NPC 实例的上下文使用率
            npc_instance = self.gm.instance_manager.get(self.world_id, context.current_npc)
            if npc_instance:
                usage = npc_instance.context_window.usage_ratio
                usage_color = "green" if usage < 0.5 else "yellow" if usage < 0.8 else "red"
                parts.append(f"[{usage_color}]ctx:{usage:.0%}[/{usage_color}]")

        return " │ ".join(parts)

    def _print_welcome(self):
        """打印欢迎信息"""
        console.clear()
        title = Text()
        title.append("═" * 50 + "\n", style="cyan")
        title.append(f"  哥布林杀手 - 交互式 RPG\n", style="bold white")
        title.append(f"  世界: {self.world_id}\n", style="dim")
        title.append("═" * 50, style="cyan")
        console.print(Panel(title, border_style="cyan", padding=(0, 2)))
        console.print()

    def _print_help(self):
        """打印帮助信息"""
        table = Table(title="命令列表", border_style="dim", show_header=True)
        table.add_column("命令", style="cyan")
        table.add_column("说明", style="white")

        for cmd, desc in self.COMMANDS.items():
            table.add_row(cmd, desc)

        table.add_row("", "")
        table.add_row("[dim](直接输入文字)[/dim]", "[dim]进行游戏交互[/dim]")

        console.print(table)
        console.print()

    def _print_gm(self, text: str):
        """打印 GM 叙述（带打字机效果）"""
        console.print()

        # 分段落打印，模拟流式输出
        paragraphs = text.split("\n\n")
        for i, para in enumerate(paragraphs):
            if not para.strip():
                continue

            # 打字机效果
            with console.status("", spinner="dots") as status:
                displayed = ""
                for char in para:
                    displayed += char
                    status.update(Text(displayed, style="white"))
                    time.sleep(0.01)  # 打字速度

            console.print(Text(para, style="white"))
            if i < len(paragraphs) - 1:
                console.print()

        console.print()

    def _print_npc(self, name: str, text: str):
        """打印 NPC 对话"""
        console.print()
        console.print(f"[bold magenta]【{name}】[/bold magenta]")

        # 对话用引号包裹
        console.print(Panel(
            Text(text, style="italic"),
            border_style="magenta",
            padding=(0, 1),
        ))

    def _print_system(self, text: str, style: str = "dim"):
        """打印系统消息"""
        console.print(f"[{style}]▸ {text}[/{style}]")

    def _print_error(self, text: str):
        """打印错误消息"""
        console.print(f"[red]✗ {text}[/red]")

    async def _show_loading(self, message: str = "思考中"):
        """显示加载动画"""
        with console.status(f"[cyan]{message}...[/cyan]", spinner="dots"):
            await asyncio.sleep(0.1)  # 让动画有机会显示

    async def start(self, initial_scene: str = "边境小镇"):
        """启动游戏"""
        self._print_welcome()
        self._print_help()

        # 创建会话
        import uuid
        self.session_id = f"play_{uuid.uuid4().hex[:8]}"

        with console.status("[cyan]正在初始化游戏世界...[/cyan]", spinner="dots"):
            context = await self.gm.start_session(
                world_id=self.world_id,
                session_id=self.session_id,
                participants=["player"],
            )

        self._print_system(f"会话已创建: {self.session_id}")
        console.print()

        # 进入初始场景
        await self._enter_scene(initial_scene)

        # 游戏主循环
        self.running = True
        await self._game_loop()

    async def _enter_scene(self, location: str):
        """进入场景"""
        scene = SceneState(
            scene_id=location.replace(" ", "_").lower(),
            location=location,
            description=location,
            participants=["player"],
        )

        with console.status(f"[cyan]正在进入 {location}...[/cyan]", spinner="dots"):
            result = await self.gm.enter_scene(
                world_id=self.world_id,
                session_id=self.session_id,
                scene=scene,
                generate_description=True,
            )

        desc = result.get("description", "")
        if desc:
            self._print_gm(desc)

    async def _game_loop(self):
        """游戏主循环"""
        while self.running:
            try:
                # 显示状态栏 + 提示符
                status = self._get_status_text()
                console.print(f"[dim]─── {status} ───[/dim]")

                # 获取用户输入
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self.prompt_session.prompt(
                        HTML("<ansigreen>❯ </ansigreen>"),
                    )
                )
                user_input = user_input.strip()

                if not user_input:
                    continue

                # 处理命令
                if user_input.startswith("/"):
                    await self._handle_command(user_input)
                else:
                    await self._handle_input(user_input)

            except KeyboardInterrupt:
                console.print("\n")
                self._print_system("按 Ctrl+C 再次退出，或输入 /quit")
                try:
                    await asyncio.sleep(2)
                except KeyboardInterrupt:
                    console.print("\n[yellow]游戏已退出[/yellow]")
                    self.running = False
            except EOFError:
                console.print("\n[yellow]游戏结束[/yellow]")
                self.running = False

    async def _handle_command(self, cmd: str):
        """处理命令"""
        parts = cmd.split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if command in ("/quit", "/q", "/exit"):
            console.print("\n[yellow]再见，冒险者！[/yellow]\n")
            self.running = False

        elif command == "/scene":
            if args:
                await self._enter_scene(args)
            else:
                self._print_error("用法: /scene <场景名称>")

        elif command == "/status":
            context = self.gm.get_context(self.world_id, self.session_id)
            if context:
                table = Table(title="当前状态", border_style="cyan")
                table.add_column("属性", style="dim")
                table.add_column("值", style="white")

                table.add_row("世界", self.world_id)
                table.add_row("会话", self.session_id)
                table.add_row("阶段", str(context.phase.value))
                table.add_row("游戏日", str(context.game_day))

                if context.current_scene:
                    table.add_row("场景", context.current_scene.location or "-")

                if context.current_npc:
                    table.add_row("对话NPC", context.current_npc)
                    # 显示 NPC 实例详情
                    npc_instance = self.gm.instance_manager.get(self.world_id, context.current_npc)
                    if npc_instance:
                        table.add_row("", "")  # 分隔
                        table.add_row("[cyan]─ NPC实例 ─[/cyan]", "")
                        table.add_row("  上下文tokens", f"{npc_instance.context_window.current_tokens:,}")
                        table.add_row("  上下文使用率", f"{npc_instance.context_window.usage_ratio:.1%}")
                        table.add_row("  对话轮次", str(npc_instance.state.conversation_turn_count))
                        table.add_row("  图谱化次数", str(npc_instance.state.graphize_count))

                # 实例池概况
                pool_stats = self.gm.instance_manager.get_stats()
                table.add_row("", "")
                table.add_row("[cyan]─ 实例池 ─[/cyan]", "")
                table.add_row("  活跃实例", f"{pool_stats['active_instances']}/{pool_stats['max_instances']}")

                console.print(table)

        elif command == "/help":
            self._print_help()

        elif command == "/clear":
            console.clear()
            self._print_welcome()

        elif command == "/talk":
            if args:
                await self._start_dialogue(args)
            else:
                self._print_error("用法: /talk <NPC名称或ID>")

        elif command == "/end":
            with console.status("[cyan]结束对话...[/cyan]", spinner="dots"):
                result = await self.gm.end_dialogue(self.world_id, self.session_id)
            self._print_system(result.get("response", "对话已结束"))
            # 显示对话统计
            instance_stats = result.get("instance_stats")
            if instance_stats:
                console.print(f"  [dim]对话统计: {instance_stats['turn_count']}轮, "
                            f"上下文{instance_stats['context_usage']}, "
                            f"图谱化{instance_stats['graphize_count']}次[/dim]")

        elif command == "/instances":
            # 显示实例池状态
            stats = self.gm.instance_manager.get_stats()
            table = Table(title="NPC 实例池", border_style="magenta")
            table.add_column("NPC", style="magenta")
            table.add_column("世界", style="dim")
            table.add_column("Tokens", style="cyan")
            table.add_column("使用率", style="white")
            table.add_column("图谱化", style="yellow")

            for inst_info in stats.get("instances", []):
                usage_ratio = inst_info.get("context_usage_ratio", 0)
                usage_str = f"{usage_ratio:.1%}"
                table.add_row(
                    inst_info.get("name", inst_info.get("npc_id", "?")),
                    inst_info.get("world_id", "?"),
                    f"{inst_info.get('context_tokens', 0):,}",
                    usage_str,
                    str(inst_info.get("graphize_count", 0)),
                )

            if not stats.get("instances"):
                table.add_row("[dim]无活跃实例[/dim]", "", "", "", "")

            console.print(table)
            console.print(f"[dim]总计: {stats['active_instances']}/{stats['max_instances']} 实例, "
                        f"已创建 {stats['total_created']}, 已淘汰 {stats['total_evicted']}[/dim]")

        elif command == "/day":
            with console.status("[cyan]时光流逝...[/cyan]", spinner="moon"):
                result = await self.gm.advance_day(self.world_id, self.session_id)
            self._print_system(result.get("response", "新的一天开始了"), "yellow")

        else:
            self._print_error(f"未知命令: {command}")
            console.print("[dim]输入 /help 查看可用命令[/dim]")

    async def _handle_input(self, user_input: str):
        """处理玩家输入"""
        with console.status("[cyan]...[/cyan]", spinner="dots"):
            result = await self.gm.process_player_input(
                world_id=self.world_id,
                session_id=self.session_id,
                player_input=user_input,
            )

        response = result.get("response", "")
        speaker = result.get("speaker", "GM")
        resp_type = result.get("type", "narration")

        if resp_type == "error":
            self._print_error(response)
        elif speaker == "GM" or resp_type == "narration":
            self._print_gm(response)
        else:
            self._print_npc(speaker, response)

            # 显示图谱化触发信息
            if result.get("graphize_triggered"):
                gr = result.get("graphize_result", {})
                console.print(f"  [yellow]⚡ 记忆图谱化: 处理{gr.get('messages_processed', 0)}条消息, "
                            f"新增{gr.get('nodes_added', 0)}个节点[/yellow]")

            # 显示记忆检索信息
            if result.get("recalled_memory"):
                console.print(f"  [dim]💭 调用了记忆: {result.get('recalled_memory', '')[:50]}...[/dim]")

        # 显示可用行动（如果有）
        actions = result.get("available_actions", [])
        if actions:
            console.print("[dim]可用行动:[/dim]")
            for i, action in enumerate(actions, 1):
                if isinstance(action, dict):
                    name = action.get("name", action.get("id", "???"))
                    desc = action.get("description", "")
                    console.print(f"  [cyan]{i}.[/cyan] {name}" + (f" [dim]- {desc}[/dim]" if desc else ""))
                else:
                    console.print(f"  [cyan]{i}.[/cyan] {action}")

    async def _start_dialogue(self, npc_id: str):
        """开始与NPC对话"""
        with console.status(f"[cyan]寻找 {npc_id}...[/cyan]", spinner="dots"):
            result = await self.gm.start_dialogue(
                world_id=self.world_id,
                session_id=self.session_id,
                npc_id=npc_id,
            )

        if result.get("type") == "error":
            self._print_error(result.get("response", "无法开始对话"))
        else:
            speaker = result.get("speaker", npc_id)
            response = result.get("response", "")
            self._print_npc(speaker, response)

            # 显示实例信息
            instance_info = result.get("instance_info")
            if instance_info:
                console.print(f"  [dim]🧠 NPC实例已激活 (上下文: {instance_info.get('context_usage', '?')})[/dim]")


async def main():
    parser = argparse.ArgumentParser(
        description="交互式 RPG 游戏客户端",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python play.py                          # 使用默认世界
  python play.py -w goblin_slayer         # 指定世界
  python play.py -s "冒险者公会"           # 指定初始场景
        """
    )
    parser.add_argument("--world", "-w", default="goblin_slayer", help="世界ID")
    parser.add_argument("--scene", "-s", default="边境小镇", help="初始场景")
    args = parser.parse_args()

    client = GameClient(world_id=args.world)

    try:
        await client.start(initial_scene=args.scene)
    except Exception as e:
        console.print(f"\n[red]错误: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
