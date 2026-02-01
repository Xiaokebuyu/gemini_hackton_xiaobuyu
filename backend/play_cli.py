#!/usr/bin/env python3
"""
AI驱动RPG游戏 - 开发测试CLI工具

直接调用服务层的交互式命令行工具，无需启动HTTP服务器。

功能：
- 创建/管理游戏会话
- 完整导航系统（地图+子地点）
- NPC对话（三层AI + 调试信息）
- 时间系统
- 战斗系统
- 叙事进度
- 调试命令

使用方式:
    cd backend
    python play_cli.py [world_id]
    python play_cli.py goblin_slayer
"""
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.prompt import Prompt
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.box import ROUNDED, SIMPLE
except ImportError:
    print("需要安装 rich 库: pip install rich")
    sys.exit(1)

from app.services.admin import AdminCoordinator


# ==================== 配置 ====================

DEFAULT_WORLD = "goblin_slayer"

# 颜色主题
COLORS = {
    "gm": "bright_yellow",
    "npc": "bright_cyan",
    "player": "bright_green",
    "system": "bright_magenta",
    "location": "bright_blue",
    "time": "yellow",
    "error": "bright_red",
    "hint": "dim",
    "debug": "dim cyan",
    "combat": "bold red",
    "teammate": "bright_green",  # 队友消息
}


# ==================== 数据结构 ====================

@dataclass
class GameState:
    """本地游戏状态（用于UI显示）"""
    world_id: str
    session_id: Optional[str] = None
    location_id: Optional[str] = None
    location_name: Optional[str] = None
    sub_location_id: Optional[str] = None
    sub_location_name: Optional[str] = None
    current_npc: Optional[str] = None
    current_npc_name: Optional[str] = None
    time: Optional[Dict] = None
    in_dialogue: bool = False
    in_combat: bool = False
    combat_id: Optional[str] = None
    available_actions: List[Dict] = field(default_factory=list)
    # 队伍相关
    party_id: Optional[str] = None
    teammates: List[Dict] = field(default_factory=list)


# ==================== 显示渲染 ====================

class GameRenderer:
    """游戏界面渲染器（带调试信息）"""

    def __init__(self, debug_mode: bool = True):
        self.console = Console()
        self.debug_mode = debug_mode

    def clear(self):
        """清屏"""
        self.console.clear()

    def print_banner(self):
        """打印游戏横幅"""
        banner = """
╔═══════════════════════════════════════════════════════════════╗
║       AI驱动互动式RPG游戏 - 开发测试工具                       ║
╚═══════════════════════════════════════════════════════════════╝
"""
        self.console.print(banner, style="bold bright_blue")

    def print_help(self):
        """打印帮助信息"""
        help_text = """
[bold]导航命令:[/bold]
  go <地点>       前往指定地图（如: go 农田）
  enter <子地点>  进入子地点（如: enter tavern）
  leave           离开当前子地点
  look            查看当前位置

[bold]交互命令:[/bold]
  talk <NPC>      与NPC对话（如: talk 公会职员）
  bye/end         结束当前对话

[bold]队伍命令:[/bold]
  party           查看队伍状态
  party add <ID> <名字> [职责] [性格]  添加队友
  party remove <ID>                    移除队友
  teammates       显示队友详情

[bold]时间命令:[/bold]
  time            查看游戏时间
  wait [分钟]     等待一段时间（默认30分钟）

[bold]战斗命令:[/bold]
  combat          进入战斗（需触发条件）
  attack/defend   执行战斗行动

[bold]信息命令:[/bold]
  status          查看游戏状态
  progress        查看主线进度
  actions         查看当前可用操作

[bold]调试命令:[/bold]
  debug           显示完整游戏状态JSON
  graph [char_id] 查看图谱节点数
  npcs            列出当前位置所有NPC
  recall <NPC> <query>  测试记忆检索
  ingest <NPC> <event>  测试事件注入

[bold]系统命令:[/bold]
  help            显示此帮助
  clear           清屏
  quit/exit       退出游戏

[dim]提示: 所有发言默认广播模式，队友会听到并可能回应[/dim]
"""
        self.console.print(Panel(help_text, title="帮助", border_style="green"))

    def print_location(self, location: Dict, state: GameState):
        """打印位置信息"""
        loc_name = location.get("location_name", "未知")
        sub_loc_name = location.get("sub_location_name")
        if sub_loc_name:
            title = f"{loc_name} - {sub_loc_name}"
        else:
            title = loc_name

        time_info = location.get("time", {})
        time_str = self._format_time(time_info)

        content = []

        desc = location.get("description", "")
        if desc:
            content.append(f"[white]{desc}[/white]\n")

        atmosphere = location.get("atmosphere")
        danger = location.get("danger_level", "low")
        if atmosphere or danger:
            meta_parts = []
            if atmosphere:
                meta_parts.append(f"氛围: {atmosphere}")
            if danger:
                meta_parts.append(f"危险: {danger}")
            content.append(f"[dim]{' | '.join(meta_parts)}[/dim]\n")

        npcs = location.get("npcs_present", [])
        npcs = [n for n in npcs if n != "player"]
        if npcs:
            npc_str = ", ".join(npcs)
            content.append(f"[cyan]在场: {npc_str}[/cyan]\n")

        actions = location.get("available_actions", [])
        if actions:
            content.append(f"[dim]可做: {', '.join(actions)}[/dim]\n")

        self.console.print(Panel(
            "".join(content),
            title=f"[bold]{title}[/bold] [{COLORS['time']}]{time_str}[/]",
            border_style=COLORS["location"],
        ))

        destinations = location.get("available_destinations", [])
        if destinations and not sub_loc_name:
            dest_table = Table(show_header=False, box=None, padding=(0, 2))
            dest_table.add_column("名称", style="cyan")
            dest_table.add_column("耗时", style="dim")
            dest_table.add_column("危险", style="dim")
            for d in destinations[:6]:
                dest_table.add_row(
                    d.get("name", d.get("id", "?")),
                    d.get("travel_time", ""),
                    d.get("danger_level", ""),
                )
            self.console.print(Panel(dest_table, title="可前往", border_style="dim"))

        sub_locs = location.get("available_sub_locations", [])
        if sub_locs and not sub_loc_name:
            sub_table = Table(show_header=False, box=None, padding=(0, 2))
            sub_table.add_column("ID", style="yellow")
            sub_table.add_column("名称", style="white")
            sub_table.add_column("类型", style="dim")
            for sl in sub_locs:
                sub_table.add_row(
                    sl.get("id", "?"),
                    sl.get("name", ""),
                    sl.get("type", ""),
                )
            self.console.print(Panel(sub_table, title="子地点", border_style="dim"))

    def print_gm_message(self, message: str, speaker: str = "GM"):
        """打印GM消息"""
        self.console.print(Panel(
            message,
            title=f"[bold]{speaker}[/bold]",
            border_style=COLORS["gm"],
        ))

    def print_npc_message(
        self,
        message: str,
        speaker: str,
        debug_info: Optional[Dict] = None,
    ):
        """打印NPC消息（含调试信息）"""
        footer = ""
        if self.debug_mode and debug_info:
            parts = []
            if debug_info.get("tier_used"):
                parts.append(f"tier: {debug_info['tier_used']}")
            if debug_info.get("model"):
                parts.append(f"model: {debug_info['model']}")
            if debug_info.get("latency_ms"):
                parts.append(f"latency: {debug_info['latency_ms']:.0f}ms")
            if debug_info.get("recalled_nodes"):
                parts.append(f"recalled: {debug_info['recalled_nodes']} nodes")
            if debug_info.get("tool_called"):
                parts.append(f"tool: {debug_info['tool_called']}")
            footer = " | ".join(parts)

        self.console.print(Panel(
            message,
            title=f"[bold cyan]{speaker}[/bold cyan]",
            subtitle=f"[{COLORS['debug']}]{footer}[/]" if footer else None,
            border_style=COLORS["npc"],
        ))

    def print_teammate_message(
        self,
        message: str,
        speaker: str,
        reaction: str = "",
        debug_info: Optional[Dict] = None,
    ):
        """打印队友消息（绿色边框，区别于普通NPC）"""
        content = message
        if reaction:
            content = f"{message}\n[dim]({reaction})[/dim]"

        footer = ""
        if self.debug_mode and debug_info:
            parts = []
            if debug_info.get("model_used"):
                parts.append(f"model: {debug_info['model_used']}")
            if debug_info.get("thinking_level"):
                parts.append(f"thinking: {debug_info['thinking_level']}")
            if debug_info.get("latency_ms"):
                parts.append(f"latency: {debug_info['latency_ms']}ms")
            footer = " | ".join(parts)

        self.console.print(Panel(
            content,
            title=f"[bold green]🗡️ {speaker}[/bold green]",
            subtitle=f"[{COLORS['debug']}]{footer}[/]" if footer else None,
            border_style=COLORS["teammate"],
        ))

    def print_player_input(self, message: str):
        """打印玩家输入回显"""
        self.console.print(f"[{COLORS['player']}]> {message}[/]")

    def print_error(self, message: str):
        """打印错误"""
        self.console.print(f"[{COLORS['error']}]错误: {message}[/]")

    def print_system(self, message: str):
        """打印系统消息"""
        self.console.print(f"[{COLORS['system']}]{message}[/]")

    def print_hint(self, message: str):
        """打印提示"""
        self.console.print(f"[{COLORS['hint']}]{message}[/]")

    def print_debug(self, message: str):
        """打印调试信息"""
        if self.debug_mode:
            self.console.print(f"[{COLORS['debug']}][DEBUG] {message}[/]")

    def print_travel(self, result: Dict):
        """打印旅行结果"""
        narration = result.get("narration", "")
        if narration:
            self.console.print(Panel(
                narration,
                title="[bold]旅途[/bold]",
                border_style="yellow",
            ))

        events = result.get("events", [])
        for event in events:
            event_type = event.get("event_type", "事件")
            title = event.get("title", "")
            desc = event.get("description", "")
            self.console.print(Panel(
                desc,
                title=f"[bold]{event_type}: {title}[/bold]",
                border_style="red" if event_type == "encounter" else "cyan",
            ))

    def print_combat_state(self, combat_state: Dict, available_actions: List[Dict]):
        """打印战斗状态"""
        content = []

        player_hp = combat_state.get("player_hp", "?")
        player_max = combat_state.get("player_max_hp", "?")
        content.append(f"[green]玩家HP: {player_hp}/{player_max}[/green]\n")

        enemies = combat_state.get("enemies", [])
        for enemy in enemies:
            name = enemy.get("name", "敌人")
            hp = enemy.get("hp", "?")
            max_hp = enemy.get("max_hp", "?")
            content.append(f"[red]{name}: {hp}/{max_hp}[/red]\n")

        self.console.print(Panel(
            "".join(content),
            title="[bold red]战斗状态[/bold red]",
            border_style=COLORS["combat"],
        ))

        if available_actions:
            action_table = Table(show_header=False, box=None, padding=(0, 2))
            action_table.add_column("行动", style="yellow")
            action_table.add_column("描述", style="dim")
            for action in available_actions:
                name = action.get("display_name", action.get("action_id", "?"))
                desc = action.get("description", "")
                action_table.add_row(name, desc)
            self.console.print(Panel(action_table, title="可用行动", border_style="red"))

    def print_progress(self, progress: Dict):
        """打印叙事进度"""
        table = Table(title="主线进度", box=ROUNDED)
        table.add_column("项目", style="cyan")
        table.add_column("值", style="white")

        table.add_row("主线", progress.get("current_mainline", "未知"))

        chapter_info = progress.get("chapter_info", {})
        table.add_row("章节", chapter_info.get("name", progress.get("current_chapter", "未知")))
        table.add_row("描述", chapter_info.get("description", ""))

        objectives = chapter_info.get("objectives", [])
        completed = progress.get("objectives_completed", [])
        if objectives:
            obj_list = []
            for obj in objectives:
                status = "✓" if obj.get("id") in completed else "○"
                obj_list.append(f"{status} {obj.get('description', obj.get('id'))}")
            table.add_row("目标", "\n".join(obj_list))

        maps = chapter_info.get("available_maps", [])
        if maps:
            maps_str = ", ".join(maps) if maps != ["*"] else "全部解锁"
            table.add_row("可用地图", maps_str)

        self.console.print(table)

    def print_status(self, state: GameState, location: Dict, progress: Dict):
        """打印完整游戏状态"""
        table = Table(title="游戏状态", box=ROUNDED)
        table.add_column("项目", style="cyan")
        table.add_column("值", style="white")

        loc_str = state.location_name or state.location_id or "未知"
        if state.sub_location_name:
            loc_str += f" - {state.sub_location_name}"
        table.add_row("当前位置", loc_str)

        time_str = self._format_time(state.time) if state.time else "未知"
        table.add_row("游戏时间", time_str)

        chapter = progress.get("chapter_info", {}).get("name", "未知")
        table.add_row("当前章节", chapter)

        if state.in_dialogue and state.current_npc_name:
            table.add_row("对话中", state.current_npc_name)

        # 队伍信息
        if state.teammates:
            teammate_names = ", ".join(t.get("name", t.get("character_id", "?")) for t in state.teammates)
            table.add_row("队友", teammate_names)

        if state.in_combat:
            table.add_row("战斗", f"进行中 (ID: {state.combat_id})")

        self.console.print(table)

    def print_available_actions(self, actions: List[Dict]):
        """打印可用操作列表"""
        if not actions:
            self.console.print("[dim]当前没有特殊可用操作[/dim]")
            return

        table = Table(title="可用操作", box=SIMPLE, show_header=True)
        table.add_column("键", style="yellow", width=3)
        table.add_column("操作", style="cyan")
        table.add_column("说明", style="dim")

        for action in actions:
            hotkey = action.get("hotkey", "-")
            display_name = action.get("display_name", action.get("action_id", "?"))
            description = action.get("description", "")[:40]
            table.add_row(f"[{hotkey}]" if hotkey else "-", display_name, description)

        self.console.print(table)
        self.console.print("[dim]输入数字快速执行，或输入自然语言描述[/dim]")

    def print_party_info(self, party_info: Dict):
        """打印队伍信息"""
        if not party_info.get("has_party"):
            self.console.print("[dim]你还没有队伍。使用 'party add <ID> <名字>' 添加队友[/dim]")
            return

        table = Table(title=f"队伍 ({party_info.get('party_id', '?')[:8]})", box=ROUNDED)
        table.add_column("名字", style="green")
        table.add_column("职责", style="cyan")
        table.add_column("性格", style="dim")
        table.add_column("状态", style="yellow")

        for member in party_info.get("members", []):
            status = "活跃" if member.get("is_active") else "离队"
            mood = member.get("current_mood", "neutral")
            table.add_row(
                member.get("name", "?"),
                member.get("role", "support"),
                member.get("personality", "")[:20],
                f"{status} ({mood})",
            )

        self.console.print(table)

    def _format_time(self, time_info: Dict) -> str:
        """格式化时间"""
        if not time_info:
            return "?"
        day = time_info.get("day", 1)
        hour = time_info.get("hour", 0)
        minute = time_info.get("minute", 0)
        period = time_info.get("period", "")

        period_zh = {
            "dawn": "黎明",
            "day": "白天",
            "dusk": "黄昏",
            "night": "夜晚",
        }.get(period, "")

        return f"第{day}天 {hour:02d}:{minute:02d} {period_zh}"

    def get_input(self, state: GameState) -> str:
        """获取玩家输入"""
        # 构建提示符
        party_tag = f"👥{len(state.teammates)}" if state.teammates else ""

        if state.in_combat:
            prompt_str = f"[战斗中]{party_tag} "
        elif state.in_dialogue and state.current_npc_name:
            prompt_str = f"[与{state.current_npc_name}对话]{party_tag} "
        elif state.sub_location_name:
            prompt_str = f"[{state.sub_location_name}]{party_tag} "
        else:
            prompt_str = f"[{state.location_name or '?'}]{party_tag} "

        try:
            return Prompt.ask(f"[green]{prompt_str}[/green]")
        except (KeyboardInterrupt, EOFError):
            return "quit"


# ==================== 游戏主类 ====================

class GameCLI:
    """游戏CLI主类（直接服务调用）"""

    def __init__(self, world_id: str = DEFAULT_WORLD, debug_mode: bool = True):
        self.coordinator = AdminCoordinator.get_instance()
        self.renderer = GameRenderer(debug_mode=debug_mode)
        self.state = GameState(world_id=world_id)
        self.running = True

    async def start(self):
        """启动游戏"""
        self.renderer.clear()
        self.renderer.print_banner()

        self.renderer.print_system(f"正在创建游戏会话 (世界: {self.state.world_id})...")

        try:
            admin_state = await self.coordinator.start_session(
                world_id=self.state.world_id,
                starting_time={"day": 1, "hour": 8, "minute": 0},
            )

            self.state.session_id = admin_state.session_id
            self.state.location_id = admin_state.player_location
            self.state.time = admin_state.game_time.model_dump() if admin_state.game_time else None

            self.renderer.print_system(f"会话已创建: {self.state.session_id[:8]}...")

            location = await self.coordinator.get_current_location(
                self.state.world_id, self.state.session_id
            )
            self._update_location_state(location)
            self.renderer.print_location(location, self.state)

            # 显示可用操作
            context = await self.coordinator._build_context(
                self.state.world_id, self.state.session_id
            )
            actions = await self.coordinator._get_available_actions(
                self.state.world_id, self.state.session_id, context
            )
            self.state.available_actions = actions
            self.renderer.print_available_actions(actions)

            self.renderer.print_hint("输入数字快速操作，或输入自然语言")

        except Exception as e:
            self.renderer.print_error(f"创建会话失败: {e}")
            self.renderer.print_hint("请检查服务是否正确初始化")
            return

        await self.main_loop()

    async def main_loop(self):
        """主游戏循环"""
        while self.running:
            try:
                user_input = self.renderer.get_input(self.state)
                if not user_input.strip():
                    continue

                await self.handle_input(user_input.strip())

            except KeyboardInterrupt:
                self.renderer.print_system("\n正在退出...")
                break
            except Exception as e:
                self.renderer.print_error(f"发生错误: {e}")
                import traceback
                self.renderer.print_debug(traceback.format_exc())

    async def handle_input(self, user_input: str):
        """处理用户输入"""
        # === 快速操作：数字选择 ===
        if user_input.isdigit():
            await self._execute_action_by_hotkey(user_input)
            return

        if user_input.startswith("/"):
            await self._handle_slash_command(user_input)
            return

        parts = user_input.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        # === 系统命令 ===
        if cmd in ("quit", "exit", "q"):
            self.running = False
            return

        if cmd == "help":
            self.renderer.print_help()
            return

        if cmd == "clear":
            self.renderer.clear()
            return

        # === 导航命令 ===
        if cmd == "go" and arg:
            await self.cmd_navigate(arg)
            return

        if cmd == "enter" and arg:
            await self.cmd_enter_sub_location(arg)
            return

        if cmd == "leave":
            await self.cmd_leave_sub_location()
            return

        if cmd == "look":
            await self.cmd_look()
            return

        # === 交互命令 ===
        if cmd == "talk" and arg:
            await self.cmd_talk(arg)
            return

        if cmd in ("bye", "end"):
            await self.cmd_end_dialogue()
            return

        # === 队伍命令 ===
        if cmd == "party":
            await self.cmd_party(arg)
            return

        if cmd == "teammates":
            await self.cmd_teammates()
            return

        # === 信息命令 ===
        if cmd == "status":
            await self.cmd_status()
            return

        if cmd == "time":
            await self.cmd_time()
            return

        if cmd == "wait":
            minutes = int(arg) if arg.isdigit() else 30
            await self.cmd_wait(minutes)
            return

        if cmd == "progress":
            await self.cmd_progress()
            return

        if cmd == "actions":
            await self.cmd_show_actions()
            return

        # === 战斗命令 ===
        if cmd == "combat":
            await self.cmd_trigger_combat()
            return

        if self.state.in_combat and cmd in ("attack", "defend", "spell", "flee", "item"):
            await self.cmd_combat_action(cmd)
            return

        # === 调试命令 ===
        if cmd == "debug":
            await self.cmd_debug()
            return

        if cmd == "graph":
            await self.cmd_graph(arg if arg else None)
            return

        if cmd == "npcs":
            await self.cmd_list_npcs()
            return

        if cmd == "recall" and arg:
            parts2 = arg.split(maxsplit=1)
            if len(parts2) >= 2:
                await self.cmd_recall(parts2[0], parts2[1])
            else:
                self.renderer.print_error("用法: recall <NPC_ID> <查询>")
            return

        if cmd == "ingest" and arg:
            parts2 = arg.split(maxsplit=1)
            if len(parts2) >= 2:
                await self.cmd_ingest(parts2[0], parts2[1])
            else:
                self.renderer.print_error("用法: ingest <NPC_ID> <事件描述>")
            return

        # === 自然语言处理 ===
        await self.cmd_natural_input(user_input)

    async def _handle_slash_command(self, command: str):
        """处理斜杠命令"""
        # 直接传递给服务处理
        result = await self.coordinator.process_player_input(
            self.state.world_id,
            self.state.session_id,
            command,
        )
        self._handle_response(result)

    # ==================== 命令实现 ====================

    async def cmd_look(self):
        """查看当前位置"""
        location = await self.coordinator.get_current_location(
            self.state.world_id, self.state.session_id
        )
        if "error" in location:
            self.renderer.print_error(location["error"])
            return

        self._update_location_state(location)
        self.renderer.print_location(location, self.state)

    async def cmd_navigate(self, destination: str):
        """导航到目的地"""
        self.renderer.print_system(f"正在前往 {destination}...")

        start_time = time.time()
        result = await self.coordinator.navigate(
            self.state.world_id,
            self.state.session_id,
            destination=destination,
        )
        elapsed = (time.time() - start_time) * 1000

        if not result.get("success"):
            self.renderer.print_error(result.get("error", "导航失败"))
            if result.get("hint"):
                self.renderer.print_hint(result["hint"])
            if result.get("available_maps"):
                self.renderer.print_hint(f"可用地图: {', '.join(result['available_maps'])}")
            return

        self.renderer.print_debug(f"导航耗时: {elapsed:.0f}ms")
        self.renderer.print_travel(result)

        new_location = result.get("new_location", {})
        self._update_location_state(new_location)
        self.renderer.print_location(new_location, self.state)

    async def cmd_enter_sub_location(self, sub_location: str):
        """进入子地点"""
        location = await self.coordinator.get_current_location(
            self.state.world_id, self.state.session_id
        )
        sub_locs = location.get("available_sub_locations", [])

        sub_id = None
        for sl in sub_locs:
            if sub_location.lower() in sl.get("name", "").lower() or \
               sub_location.lower() == sl.get("id", "").lower():
                sub_id = sl.get("id")
                break

        if not sub_id:
            self.renderer.print_error(f"找不到子地点: {sub_location}")
            if sub_locs:
                self.renderer.print_hint(f"可用子地点: {', '.join(sl.get('id', '') for sl in sub_locs)}")
            return

        self.renderer.print_system(f"正在进入 {sub_location}...")

        result = await self.coordinator.enter_sub_location(
            self.state.world_id,
            self.state.session_id,
            sub_id,
        )

        if not result.get("success"):
            self.renderer.print_error(result.get("error", "进入失败"))
            return

        desc = result.get("description", "")
        if desc:
            self.renderer.print_gm_message(desc)

        sub_info = result.get("sub_location", {})
        self.state.sub_location_id = sub_info.get("id") or sub_id
        self.state.sub_location_name = sub_info.get("name")

        npcs = sub_info.get("resident_npcs", [])
        if npcs:
            self.renderer.print_hint(f"这里有: {', '.join(npcs)}")

    async def cmd_leave_sub_location(self):
        """离开子地点"""
        if not self.state.sub_location_id:
            self.renderer.print_error("你已经在外面了")
            return

        self.renderer.print_system("正在离开...")

        result = await self.coordinator.leave_sub_location(
            self.state.world_id,
            self.state.session_id,
        )

        if not result.get("success"):
            self.renderer.print_error(result.get("error", "离开失败"))
            return

        self.state.sub_location_id = None
        self.state.sub_location_name = None

        location = await self.coordinator.get_current_location(
            self.state.world_id, self.state.session_id
        )
        self._update_location_state(location)
        self.renderer.print_location(location, self.state)

    async def cmd_talk(self, npc_name: str):
        """开始与NPC对话"""
        self.renderer.print_system(f"正在接近 {npc_name}...")

        start_time = time.time()
        result = await self.coordinator.start_dialogue(
            self.state.world_id,
            self.state.session_id,
            npc_name,
        )
        elapsed = (time.time() - start_time) * 1000

        if result.get("type") == "error":
            self.renderer.print_error(result.get("response", "无法开始对话"))
            return

        self.state.in_dialogue = True
        self.state.current_npc = result.get("npc_id", npc_name)
        self.state.current_npc_name = result.get("speaker", npc_name)

        self.renderer.print_npc_message(
            result.get("response", "……"),
            result.get("speaker", npc_name),
            debug_info={"latency_ms": elapsed, "tier_used": result.get("tier_used")},
        )

    async def cmd_end_dialogue(self):
        """结束对话"""
        if not self.state.in_dialogue:
            self.renderer.print_hint("你当前没有在对话")
            return

        await self.coordinator.end_dialogue(
            self.state.world_id,
            self.state.session_id,
        )

        self.state.in_dialogue = False
        self.state.current_npc = None
        self.state.current_npc_name = None

        self.renderer.print_system("对话已结束")

    async def cmd_time(self):
        """显示时间"""
        result = await self.coordinator.get_game_time(
            self.state.world_id, self.state.session_id
        )

        if isinstance(result, dict) and "error" not in result:
            self.state.time = result
            time_str = self.renderer._format_time(result)
            self.renderer.print_system(f"当前时间: {time_str}")
        else:
            self.renderer.print_error("无法获取时间")

    async def cmd_wait(self, minutes: int):
        """等待"""
        self.renderer.print_system(f"等待 {minutes} 分钟...")

        result = await self.coordinator.advance_time(
            self.state.world_id,
            self.state.session_id,
            minutes,
        )

        if isinstance(result, dict):
            self.state.time = result.get("time", self.state.time)
            time_str = self.renderer._format_time(self.state.time)
            self.renderer.print_system(f"时间已推进到: {time_str}")

            events = result.get("events", [])
            for event in events:
                desc = event.get("description", event.get("event_type", ""))
                if desc:
                    self.renderer.print_system(f"  - {desc}")

    async def cmd_status(self):
        """显示状态"""
        location = await self.coordinator.get_current_location(
            self.state.world_id, self.state.session_id
        )
        progress = await self._get_narrative_progress()

        self._update_location_state(location)
        self.renderer.print_status(self.state, location, progress)

    async def cmd_progress(self):
        """显示主线进度"""
        progress = await self._get_narrative_progress()
        self.renderer.print_progress(progress)

    async def _get_narrative_progress(self) -> Dict:
        """获取叙事进度"""
        try:
            progress = await self.coordinator.narrative_service.get_progress(
                self.state.world_id, self.state.session_id
            )
            chapter_info = self.coordinator.narrative_service.get_chapter_info(
                progress.current_chapter
            )
            return {
                "current_mainline": progress.current_mainline,
                "current_chapter": progress.current_chapter,
                "chapter_info": chapter_info or {},
                "objectives_completed": progress.objectives_completed,
                "events_triggered": progress.events_triggered,
            }
        except Exception:
            return {
                "current_mainline": "unknown",
                "current_chapter": "unknown",
                "chapter_info": {},
                "objectives_completed": [],
            }

    # ==================== 队伍命令 ====================

    async def cmd_party(self, arg: str):
        """队伍管理命令"""
        parts = arg.split() if arg else []
        sub_cmd = parts[0].lower() if parts else ""

        if not sub_cmd or sub_cmd == "list" or sub_cmd == "status":
            # 显示队伍状态
            party_info = await self.coordinator.get_party_info(
                self.state.world_id, self.state.session_id
            )
            self._update_party_state(party_info)
            self.renderer.print_party_info(party_info)
            return

        # 智能识别：如果第一个参数不是子命令，假设是 add
        if sub_cmd not in ("add", "remove", "list", "status") and len(parts) >= 2:
            # party <ID> <名字> [职责] [性格] => party add <ID> <名字> ...
            char_id = parts[0]
            name = parts[1]
            role = parts[2] if len(parts) > 2 else "support"
            personality = " ".join(parts[3:]) if len(parts) > 3 else ""

            result = await self.coordinator.add_teammate(
                world_id=self.state.world_id,
                session_id=self.state.session_id,
                character_id=char_id,
                name=name,
                role=role,
                personality=personality,
            )

            if result.get("success"):
                self.renderer.print_system(f"✓ {name} 加入了队伍！职责: {result.get('role')}")
                party_info = await self.coordinator.get_party_info(
                    self.state.world_id, self.state.session_id
                )
                self._update_party_state(party_info)
            else:
                self.renderer.print_error(result.get("error", "添加队友失败"))
            return

        if sub_cmd == "add" and len(parts) >= 3:
            # party add <ID> <名字> [职责] [性格]
            char_id = parts[1]
            name = parts[2]
            role = parts[3] if len(parts) > 3 else "support"
            personality = " ".join(parts[4:]) if len(parts) > 4 else ""

            result = await self.coordinator.add_teammate(
                world_id=self.state.world_id,
                session_id=self.state.session_id,
                character_id=char_id,
                name=name,
                role=role,
                personality=personality,
            )

            if result.get("success"):
                self.renderer.print_system(f"✓ {name} 加入了队伍！职责: {result.get('role')}")
                # 更新本地状态
                party_info = await self.coordinator.get_party_info(
                    self.state.world_id, self.state.session_id
                )
                self._update_party_state(party_info)
            else:
                self.renderer.print_error(result.get("error", "添加队友失败"))
            return

        if sub_cmd == "remove" and len(parts) >= 2:
            # party remove <ID>
            char_id = parts[1]
            result = await self.coordinator.remove_teammate(
                self.state.world_id, self.state.session_id, char_id
            )

            if result.get("success"):
                self.renderer.print_system(f"✓ {char_id} 离开了队伍")
                # 更新本地状态
                party_info = await self.coordinator.get_party_info(
                    self.state.world_id, self.state.session_id
                )
                self._update_party_state(party_info)
            else:
                self.renderer.print_error("移除队友失败")
            return

        # 显示用法
        self.renderer.print_hint("用法: party [add <ID> <名字> [职责] [性格]] | [remove <ID>] | [list]")
        self.renderer.print_hint("职责: warrior, healer, mage, rogue, support, scout, scholar")

    async def cmd_teammates(self):
        """显示队友详情"""
        party_info = await self.coordinator.get_party_info(
            self.state.world_id, self.state.session_id
        )

        if not party_info.get("has_party") or not party_info.get("members"):
            self.renderer.print_hint("你还没有队友。使用 'party add <ID> <名字>' 添加队友")
            return

        self._update_party_state(party_info)

        for member in party_info.get("members", []):
            content = f"""职责: {member.get('role', 'support')}
性格: {member.get('personality', '（无描述）')}
情绪: {member.get('current_mood', 'neutral')}
回复倾向: {member.get('response_tendency', 0.5):.1f}"""

            status = "🟢 活跃" if member.get("is_active") else "🔴 离队"
            self.renderer.console.print(Panel(
                content,
                title=f"[bold green]🗡️ {member.get('name', '?')}[/bold green] {status}",
                border_style="green",
            ))

    async def cmd_show_actions(self):
        """显示当前可用操作"""
        # 获取位置信息
        location = await self.coordinator.get_current_location(
            self.state.world_id, self.state.session_id
        )

        # 构建上下文并获取操作
        context = await self.coordinator._build_context(
            self.state.world_id, self.state.session_id
        )
        actions = await self.coordinator._get_available_actions(
            self.state.world_id, self.state.session_id, context
        )

        self.state.available_actions = actions
        self.renderer.print_available_actions(actions)

    async def _execute_action_by_hotkey(self, hotkey: str):
        """通过快捷键执行操作"""
        # 如果没有缓存的操作列表，先获取
        if not self.state.available_actions:
            context = await self.coordinator._build_context(
                self.state.world_id, self.state.session_id
            )
            self.state.available_actions = await self.coordinator._get_available_actions(
                self.state.world_id, self.state.session_id, context
            )

        # 查找匹配的操作
        action = None
        for a in self.state.available_actions:
            if a.get("hotkey") == hotkey:
                action = a
                break

        if not action:
            self.renderer.print_error(f"没有找到快捷键 [{hotkey}] 对应的操作")
            self.renderer.print_available_actions(self.state.available_actions)
            return

        action_id = action.get("action_id")
        display_name = action.get("display_name", action_id)

        self.renderer.print_system(f"执行: {display_name}")

        try:
            import time
            start = time.time()

            result = await self.coordinator.execute_action(
                self.state.world_id,
                self.state.session_id,
                action_id,
            )

            elapsed = (time.time() - start) * 1000
            self.renderer.print_debug(f"操作完成 ({elapsed:.0f}ms)")

            # 处理响应
            self._handle_response_v2(result)

            # 更新位置显示
            location = await self.coordinator.get_current_location(
                self.state.world_id, self.state.session_id
            )
            if location:
                self._update_location_state(location)
                self.renderer.print_location(location, self.state)

        except Exception as e:
            import traceback
            self.renderer.print_error(f"操作失败: {e}")
            self.renderer.print_debug(traceback.format_exc())

    def _update_party_state(self, party_info: Dict):
        """更新本地队伍状态"""
        self.state.party_id = party_info.get("party_id")
        self.state.teammates = party_info.get("members", [])

    async def cmd_trigger_combat(self):
        """触发演示战斗"""
        self.renderer.print_system("触发战斗...")

        player_state = {
            "name": "冒险者",
            "hp": 30,
            "max_hp": 30,
            "ac": 15,
            "attack_bonus": 5,
            "damage_dice": "1d8",
            "damage_bonus": 3,
            "damage_type": "slashing",
            "initiative_bonus": 2,
        }

        enemies = [{"type": "goblin", "level": 1}]

        result = await self.coordinator.trigger_combat(
            world_id=self.state.world_id,
            session_id=self.state.session_id,
            enemies=enemies,
            player_state=player_state,
            combat_description="一只哥布林从灌木丛中跳出来，龇牙咧嘴地看着你！",
        )

        if result.get("type") == "error":
            self.renderer.print_error(result.get("response", "无法开始战斗"))
            return

        self.state.in_combat = True
        self.state.combat_id = result.get("combat_id")
        self.state.available_actions = result.get("available_actions", [])

        self.renderer.print_gm_message(result.get("narration", "战斗开始！"))
        self.renderer.print_combat_state(
            result.get("combat_state", {}),
            self.state.available_actions,
        )

    async def cmd_combat_action(self, action_id: str):
        """执行战斗行动"""
        if not self.state.in_combat:
            self.renderer.print_error("当前不在战斗中")
            return

        result = await self.coordinator.execute_combat_action(
            self.state.world_id,
            self.state.session_id,
            action_id,
        )

        if result.get("type") == "error":
            self.renderer.print_error(result.get("response", "行动失败"))
            return

        self.renderer.print_gm_message(result.get("narration", ""))

        if result.get("phase") == "end":
            self.state.in_combat = False
            self.state.combat_id = None
            self.state.available_actions = []
            self.renderer.print_system("战斗结束！")
            combat_result = result.get("result", {})
            if combat_result:
                self.renderer.print_system(f"结果: {combat_result.get('result', '')}")
        else:
            self.state.available_actions = result.get("available_actions", [])
            self.renderer.print_combat_state({}, self.state.available_actions)

    async def cmd_natural_input(self, user_input: str):
        """处理自然语言输入（使用 Pro-First v2 流程）"""
        self.renderer.print_player_input(user_input)

        start_time = time.time()

        # 使用新的 Pro-First 流程
        try:
            result = await self.coordinator.process_player_input_v2(
                self.state.world_id,
                self.state.session_id,
                user_input,
            )
            elapsed = (time.time() - start_time) * 1000
            self.renderer.print_debug(f"处理耗时: {elapsed:.0f}ms")
            self._handle_response_v2(result)
        except Exception as e:
            # 降级到旧流程
            self.renderer.print_debug(f"V2 流程失败，降级到旧流程: {e}")
            result = await self.coordinator.process_player_input(
                self.state.world_id,
                self.state.session_id,
                user_input,
            )
            elapsed = (time.time() - start_time) * 1000
            self.renderer.print_debug(f"处理耗时: {elapsed:.0f}ms")
            self._handle_response(result)

    # ==================== 调试命令 ====================

    async def cmd_debug(self):
        """显示完整游戏状态JSON"""
        context = await self.coordinator.get_context_async(
            self.state.world_id, self.state.session_id
        )

        debug_data = {
            "local_state": {
                "world_id": self.state.world_id,
                "session_id": self.state.session_id,
                "location_id": self.state.location_id,
                "location_name": self.state.location_name,
                "sub_location_id": self.state.sub_location_id,
                "in_dialogue": self.state.in_dialogue,
                "current_npc": self.state.current_npc,
                "in_combat": self.state.in_combat,
                "party_id": self.state.party_id,
                "teammates": [t.get("name") for t in self.state.teammates],
            },
            "server_context": {
                "phase": context.phase.value if context else None,
                "game_day": context.game_day if context else None,
                "current_npc": context.current_npc if context else None,
                "known_characters": context.known_characters if context else [],
            } if context else None,
            "time": self.state.time,
        }

        self.renderer.console.print_json(json.dumps(debug_data, ensure_ascii=False, indent=2))

    async def cmd_graph(self, character_id: Optional[str]):
        """查看图谱节点数"""
        try:
            graph_store = self.coordinator.graph_store

            if character_id:
                graph = graph_store.get_character_graph(self.state.world_id, character_id)
                if graph:
                    self.renderer.print_system(
                        f"角色 {character_id} 图谱: {len(graph.nodes())} 节点, {len(graph.edges())} 边"
                    )
                else:
                    self.renderer.print_hint(f"角色 {character_id} 没有图谱数据")
            else:
                gm_graph = graph_store.get_gm_graph(self.state.world_id)
                if gm_graph:
                    self.renderer.print_system(
                        f"GM图谱: {len(gm_graph.nodes())} 节点, {len(gm_graph.edges())} 边"
                    )
                else:
                    self.renderer.print_hint("GM图谱为空")

        except Exception as e:
            self.renderer.print_error(f"获取图谱失败: {e}")

    async def cmd_list_npcs(self):
        """列出当前位置所有NPC"""
        location = await self.coordinator.get_current_location(
            self.state.world_id, self.state.session_id
        )
        npcs = location.get("npcs_present", [])

        if not npcs:
            self.renderer.print_hint("当前位置没有NPC")
            return

        table = Table(title="当前位置NPC", box=ROUNDED)
        table.add_column("NPC ID", style="cyan")
        table.add_column("层级", style="yellow")
        table.add_column("备注", style="dim")

        for npc_id in npcs:
            if npc_id == "player":
                continue
            table.add_row(npc_id, "—", "")

        self.renderer.console.print(table)

    async def cmd_recall(self, npc_id: str, query: str):
        """测试记忆检索"""
        self.renderer.print_system(f"测试检索 {npc_id} 的记忆: {query}")

        try:
            from app.models.flash import RecallRequest

            start_time = time.time()
            result = await self.coordinator.flash_service.recall_memory(
                world_id=self.state.world_id,
                character_id=npc_id,
                request=RecallRequest(
                    query=query,
                    top_k=5,
                    include_graph=True,
                ),
            )
            elapsed = (time.time() - start_time) * 1000

            self.renderer.print_debug(f"检索耗时: {elapsed:.0f}ms")

            if hasattr(result, "memories") and result.memories:
                for mem in result.memories[:3]:
                    self.renderer.print_system(f"  - {mem.content[:100]}...")
            else:
                self.renderer.print_hint("没有找到相关记忆")

        except Exception as e:
            self.renderer.print_error(f"记忆检索失败: {e}")

    async def cmd_ingest(self, npc_id: str, event_desc: str):
        """测试事件注入"""
        self.renderer.print_system(f"注入事件到 {npc_id}: {event_desc}")

        try:
            from app.models.flash import NaturalEventIngestRequest

            start_time = time.time()
            result = await self.coordinator.flash_service.ingest_event_natural(
                world_id=self.state.world_id,
                character_id=npc_id,
                request=NaturalEventIngestRequest(
                    event_description=event_desc,
                    game_day=self.state.time.get("day", 1) if self.state.time else 1,
                    location=self.state.location_id,
                ),
            )
            elapsed = (time.time() - start_time) * 1000

            self.renderer.print_debug(f"注入耗时: {elapsed:.0f}ms")
            self.renderer.print_system("事件已注入")

        except Exception as e:
            self.renderer.print_error(f"事件注入失败: {e}")

    # ==================== 辅助方法 ====================

    def _handle_response(self, result: Dict):
        """处理服务响应"""
        response_type = result.get("type", "")
        response = result.get("response", "")
        speaker = result.get("speaker", "")

        if response_type == "dialogue":
            self.state.in_dialogue = True
            self.state.current_npc = result.get("npc_id")
            self.state.current_npc_name = speaker

            self.renderer.print_npc_message(
                response,
                speaker,
                debug_info={
                    "tier_used": result.get("tier_used"),
                    "latency_ms": result.get("latency_ms"),
                    "recalled_nodes": result.get("recalled_count"),
                },
            )

            recalled = result.get("recalled_memory")
            if recalled:
                self.renderer.print_hint(f"[记忆: {recalled[:50]}...]")

        elif response_type == "navigation" or response_type == "narration":
            if result.get("new_location"):
                self.renderer.print_travel(result)
                self._update_location_state(result["new_location"])
                self.renderer.print_location(result["new_location"], self.state)
            elif response:
                self.renderer.print_gm_message(response, speaker or "GM")

        elif response_type == "combat":
            self.renderer.print_gm_message(response or result.get("narration", ""), "战斗")
            if result.get("available_actions"):
                self.renderer.print_combat_state({}, result["available_actions"])

        elif response_type == "system":
            self.renderer.print_system(response)

        elif response_type == "error":
            self.renderer.print_error(response)

        else:
            if response:
                self.renderer.print_gm_message(response, speaker or "GM")

        if "time" in result:
            self.state.time = result["time"]

        for extra in result.get("responses", []) or []:
            extra_response = extra.get("response")
            if extra_response:
                self.renderer.print_npc_message(
                    extra_response,
                    extra.get("speaker", "NPC"),
                    debug_info={
                        "tier_used": extra.get("tier_used"),
                        "latency_ms": extra.get("latency_ms"),
                    },
                )

    def _update_location_state(self, location: Dict):
        """更新本地位置状态"""
        self.state.location_id = location.get("location_id") or location.get("map_id")
        self.state.location_name = location.get("location_name") or location.get("map_name")
        self.state.sub_location_id = location.get("sub_location_id")
        self.state.sub_location_name = location.get("sub_location_name")
        self.state.time = location.get("time", self.state.time)

    def _handle_response_v2(self, result):
        """处理 Pro-First v2 响应（CoordinatorResponse）"""
        from app.models.admin_protocol import CoordinatorResponse

        # 如果是 CoordinatorResponse 对象
        if isinstance(result, CoordinatorResponse):
            narration = result.narration
            speaker = result.speaker
            teammate_responses = result.teammate_responses
            available_actions = result.available_actions
            metadata = result.metadata
        else:
            # 兼容字典格式
            narration = result.get("narration", result.get("response", ""))
            speaker = result.get("speaker", "GM")
            teammate_responses = result.get("teammate_responses", [])
            available_actions = result.get("available_actions", [])
            metadata = result.get("metadata", {})

        # 1. 打印 GM 叙述
        if narration:
            self.renderer.print_gm_message(narration, speaker)

        # 2. 打印队友响应
        for teammate in teammate_responses:
            response_text = teammate.get("response")
            if response_text:
                self.renderer.print_teammate_message(
                    message=response_text,
                    speaker=teammate.get("name", teammate.get("character_id", "队友")),
                    reaction=teammate.get("reaction", ""),
                    debug_info={
                        "model_used": teammate.get("model_used"),
                        "thinking_level": teammate.get("thinking_level"),
                        "latency_ms": teammate.get("latency_ms"),
                    },
                )

        # 3. 更新可用操作
        if available_actions:
            self.state.available_actions = available_actions

        # 4. 显示意图类型（调试）
        intent_type = metadata.get("intent_type")
        confidence = metadata.get("confidence")
        if intent_type:
            self.renderer.print_debug(f"意图: {intent_type} (置信度: {confidence:.2f})" if confidence else f"意图: {intent_type}")

        # 5. 如果有队友回复，显示提示
        teammate_count = metadata.get("teammate_count", len(teammate_responses))
        if teammate_count > 0:
            self.renderer.print_debug(f"队友回复: {teammate_count} 人")


# ==================== 入口 ====================

async def main():
    """主入口"""
    import argparse

    parser = argparse.ArgumentParser(description="AI驱动RPG游戏 - 开发测试CLI")
    parser.add_argument("world_id", nargs="?", default=DEFAULT_WORLD, help="世界ID")
    parser.add_argument("--no-debug", action="store_true", help="禁用调试信息")
    args = parser.parse_args()

    game = GameCLI(world_id=args.world_id, debug_mode=not args.no_debug)
    await game.start()


if __name__ == "__main__":
    asyncio.run(main())
