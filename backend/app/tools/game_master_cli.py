#!/usr/bin/env python3
"""
Game Master CLI - 交互式游戏测试工具

一个类似 Claude Code 的交互式命令行工具，用于测试完整的游戏循环系统。

功能：
- 创建/管理游戏会话
- 进入/管理场景
- 与NPC对话
- 触发战斗
- 查看图谱状态

运行方式：
    cd backend
    python -m app.tools.game_master_cli

示例世界设置：
    python -m app.tools.game_master_cli --setup-demo
"""
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.models.game import SceneState
from app.models.character_profile import CharacterProfile
from app.services.admin.admin_coordinator import AdminCoordinator


# ANSI 颜色代码
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'


def colorize(text: str, color: str) -> str:
    """添加颜色到文本"""
    return f"{color}{text}{Colors.ENDC}"


def print_header(text: str) -> None:
    """打印标题"""
    print(colorize(f"\n{'='*60}", Colors.CYAN))
    print(colorize(f"  {text}", Colors.CYAN + Colors.BOLD))
    print(colorize(f"{'='*60}", Colors.CYAN))


def print_subheader(text: str) -> None:
    """打印子标题"""
    print(colorize(f"\n--- {text} ---", Colors.YELLOW))


def print_gm(text: str) -> None:
    """打印GM叙述"""
    print(colorize("\n[GM] ", Colors.GREEN + Colors.BOLD) + text)


def print_npc(name: str, text: str) -> None:
    """打印NPC对话"""
    print(colorize(f"\n[{name}] ", Colors.BLUE + Colors.BOLD) + text)


def print_system(text: str) -> None:
    """打印系统消息"""
    print(colorize(f"\n[系统] ", Colors.DIM) + text)


def print_error(text: str) -> None:
    """打印错误"""
    print(colorize(f"\n[错误] ", Colors.RED + Colors.BOLD) + text)


def print_combat(text: str) -> None:
    """打印战斗信息"""
    print(colorize(f"\n[战斗] ", Colors.RED) + text)


class GameMasterCLI:
    """交互式游戏CLI"""

    def __init__(self) -> None:
        self.gm_service = AdminCoordinator.get_instance()
        self.world_id: Optional[str] = None
        self.session_id: Optional[str] = None
        self.running = True

        # 预设的演示数据
        self.demo_characters = {
            "gorn": CharacterProfile(
                name="Gorn",
                occupation="铁匠",
                age=45,
                personality="严肃但热心，对自己的手艺非常自豪。对帮助过自己的人心存感激。",
                speech_pattern="说话直接，经常用锻造相关的比喻。",
                example_dialogue="好钢需要千锤百炼，人也是一样。",
            ),
            "marcus": CharacterProfile(
                name="Marcus",
                occupation="猎人",
                age=28,
                personality="年轻活泼，喜欢冒险。对森林非常熟悉。",
                speech_pattern="说话快，喜欢讲故事。",
                example_dialogue="你不会相信我在森林里看到了什么！",
            ),
            "elena": CharacterProfile(
                name="Elena",
                occupation="旅店老板娘",
                age=35,
                personality="温和善良，善于倾听。知道镇上所有的八卦。",
                speech_pattern="说话温柔，喜欢问问题。",
                example_dialogue="亲爱的，你看起来累了，要来杯热汤吗？",
            ),
        }

        self.demo_scenes = {
            "blacksmith": SceneState(
                scene_id="blacksmith",
                description="这是一间热气腾腾的铁匠铺。火炉里的火焰跳动着，铁砧上放着半成品的武器。空气中弥漫着金属和煤烟的气味。",
                location="铁匠铺",
                atmosphere="忙碌而温暖",
                participants=["gorn"],
            ),
            "tavern": SceneState(
                scene_id="tavern",
                description="旅店的大厅温暖而热闹。壁炉里的火噼啪作响，木桌上放着啤酒杯。几个旅人正在角落里低声交谈。",
                location="金麦旅店",
                atmosphere="温馨热闹",
                participants=["elena", "marcus"],
            ),
            "forest_edge": SceneState(
                scene_id="forest_edge",
                description="你站在森林的边缘。高大的古树遮蔽了阳光，只有斑驳的光影落在地上。远处传来奇怪的声响。",
                location="森林边缘",
                atmosphere="神秘而略带危险",
                participants=[],
            ),
        }

    async def setup_demo_world(self) -> None:
        """设置演示世界"""
        print_header("设置演示世界")

        self.world_id = "demo_world"

        # 创建角色资料
        print_subheader("创建NPC资料")
        for char_id, profile in self.demo_characters.items():
            await self.gm_service.graph_store.set_character_profile(
                self.world_id,
                char_id,
                profile.model_dump(exclude_none=True),
            )
            print(f"  ✓ 创建角色: {profile.name} ({char_id})")

        # 初始化角色的初始记忆
        print_subheader("初始化角色记忆")
        await self._init_character_memories()

        print(colorize("\n✓ 演示世界设置完成！", Colors.GREEN))

    async def _init_character_memories(self) -> None:
        """初始化角色的初始记忆"""
        from app.models.flash import NaturalEventIngestRequest

        # Gorn 的初始记忆
        await self.gm_service.flash_service.ingest_event_natural(
            self.world_id,
            "gorn",
            NaturalEventIngestRequest(
                event_description="我已经在这个镇上当了二十年铁匠。我的炉子最近有点问题，需要找人帮忙。",
                game_day=0,
                location="铁匠铺",
            ),
        )
        print("  ✓ Gorn 初始记忆已设置")

        # Marcus 的初始记忆
        await self.gm_service.flash_service.ingest_event_natural(
            self.world_id,
            "marcus",
            NaturalEventIngestRequest(
                event_description="我是这片森林最好的猎人。最近森林里有些不对劲，好像有什么东西在活动。",
                game_day=0,
                location="森林",
            ),
        )
        print("  ✓ Marcus 初始记忆已设置")

        # Elena 的初始记忆
        await self.gm_service.flash_service.ingest_event_natural(
            self.world_id,
            "elena",
            NaturalEventIngestRequest(
                event_description="金麦旅店是我和已故丈夫一起建的。这里是镇上消息最灵通的地方。",
                game_day=0,
                location="金麦旅店",
            ),
        )
        print("  ✓ Elena 初始记忆已设置")

    async def start_game(self) -> None:
        """开始新游戏"""
        if not self.world_id:
            self.world_id = "demo_world"

        print_subheader("创建游戏会话")

        context = await self.gm_service.start_session(
            world_id=self.world_id,
            known_characters=list(self.demo_characters.keys()),
            character_locations={
                "gorn": "铁匠铺",
                "marcus": "金麦旅店",
                "elena": "金麦旅店",
            },
        )

        self.session_id = context.session_id
        print(f"  会话ID: {self.session_id}")
        game_day = getattr(context, "game_day", None)
        if game_day is None and getattr(context, "game_time", None):
            game_day = context.game_time.day
        print(f"  游戏日: 第 {game_day or 1} 天")
        print(colorize("\n✓ 游戏会话已创建！", Colors.GREEN))

    async def enter_scene(self, scene_id: str) -> None:
        """进入场景"""
        if not self.session_id:
            print_error("请先开始游戏")
            return

        scene = self.demo_scenes.get(scene_id)
        if not scene:
            print_error(f"未知场景: {scene_id}")
            print(f"可用场景: {', '.join(self.demo_scenes.keys())}")
            return

        result = await self.gm_service.enter_scene(
            self.world_id, self.session_id, scene
        )

        print_gm(result["description"])

        if result.get("npc_memories"):
            print_subheader("NPC状态")
            for npc_id, memory in result["npc_memories"].items():
                if memory:
                    print(f"  {npc_id}: {memory[:100]}...")

    async def process_input(self, user_input: str) -> None:
        """处理用户输入"""
        if not self.session_id:
            print_error("请先开始游戏")
            return

        result = await self.gm_service.process_player_input_v3(
            self.world_id, self.session_id, user_input
        )

        # 处理 CoordinatorResponse
        if result.narration:
            print_gm(result.narration)

        for teammate in result.teammate_responses:
            response_text = teammate.get("response")
            if response_text:
                name = teammate.get("name", teammate.get("character_id", "队友"))
                print_npc(name, response_text)

    async def talk_to(self, npc_id: str) -> None:
        """开始与NPC对话"""
        if not self.session_id:
            print_error("请先开始游戏")
            return

        result = await self.gm_service.start_dialogue(
            self.world_id, self.session_id, npc_id
        )

        if result.get("type") == "error":
            print_error(result.get("response"))
        else:
            print_npc(result.get("speaker", npc_id), result.get("response", ""))
            print_system(f"(你现在正在与 {npc_id} 对话。输入 /leave 结束对话)")

    async def leave_dialogue(self) -> None:
        """离开对话"""
        if not self.session_id:
            return

        result = await self.gm_service.end_dialogue(
            self.world_id, self.session_id
        )
        print_system(result.get("response", ""))

    async def trigger_combat(self) -> None:
        """触发演示战斗"""
        if not self.session_id:
            print_error("请先开始游戏")
            return

        # 演示玩家状态
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

        # 演示敌人
        enemies = [
            {"type": "goblin", "level": 1},
        ]

        result = await self.gm_service.trigger_combat(
            world_id=self.world_id,
            session_id=self.session_id,
            enemies=enemies,
            player_state=player_state,
            combat_description="一只哥布林从灌木丛中跳出来，龇牙咧嘴地看着你！",
        )

        print_combat(result.get("narration", "战斗开始！"))

        if result.get("available_actions"):
            print_subheader("可用行动")
            for action in result["available_actions"]:
                if isinstance(action, dict):
                    name = action.get('display_name', action.get('action_id'))
                    desc = action.get('description', '')
                    print(f"  - {name}: {desc}")
                elif hasattr(action, 'display_name'):
                    print(f"  - {action.display_name}: {action.description}")

    async def combat_action(self, action_id: str) -> None:
        """执行战斗行动"""
        if not self.session_id:
            print_error("请先开始游戏")
            return

        result = await self.gm_service.execute_combat_action(
            self.world_id, self.session_id, action_id
        )

        if result.get("type") == "error":
            print_error(result.get("response"))
            return

        print_combat(result.get("narration", ""))

        if result.get("phase") == "end":
            print_subheader("战斗结束！")
            combat_result = result.get("result", {})
            if combat_result:
                print(f"  结果: {combat_result.get('result', 'unknown')}")
                print(f"  总结: {combat_result.get('summary', '')}")
        elif result.get("available_actions"):
            print_subheader("可用行动")
            for action in result["available_actions"]:
                if isinstance(action, dict):
                    print(f"  - {action.get('display_name', action.get('action_id'))}")
                elif hasattr(action, 'display_name'):
                    print(f"  - {action.display_name}")

    def print_help(self) -> None:
        """打印帮助信息"""
        print_header("游戏大师CLI帮助")
        print("""
游戏命令：
  /start              - 开始新游戏
  /scene <场景ID>     - 进入场景 (blacksmith, tavern, forest_edge)
  /talk <NPC ID>      - 开始与NPC对话 (gorn, marcus, elena)
  /leave              - 结束当前对话
  /status             - 查看当前状态
  /combat             - 触发演示战斗
  /action <行动ID>    - 执行战斗行动

系统命令：
  /setup              - 设置演示世界（初始化角色和记忆）
  /help               - 显示此帮助
  /quit               - 退出游戏

游戏中：
  直接输入文字进行行动或对话
  使用引号("")表示对话

示例流程：
  1. /setup           - 初始化演示世界
  2. /start           - 开始游戏
  3. /scene tavern    - 进入旅店
  4. /talk elena      - 与Elena对话
  5. "你好，有什么新消息吗？"  - 说话
  6. /leave           - 结束对话
""")

    def print_status(self) -> None:
        """打印当前状态"""
        print_subheader("当前状态")

        if not self.session_id:
            print("  游戏未开始")
            return

        context = self.gm_service.get_context(self.world_id, self.session_id)
        if context:
            print(f"  世界: {context.world_id}")
            print(f"  会话: {context.session_id}")
            print(f"  阶段: {context.phase.value}")
            print(f"  游戏日: 第 {context.game_day} 天")
            if context.current_scene:
                print(f"  当前场景: {context.current_scene.location}")
            if context.current_npc:
                print(f"  对话NPC: {context.current_npc}")
        else:
            print("  无法获取上下文")

    async def run(self) -> None:
        """运行交互式循环"""
        print_header("游戏大师CLI - Phase 6 测试工具")
        print(colorize("输入 /help 查看帮助，/quit 退出", Colors.DIM))

        while self.running:
            try:
                # 获取当前状态
                prompt = colorize("> ", Colors.CYAN + Colors.BOLD)
                if self.session_id:
                    context = self.gm_service.get_context(self.world_id, self.session_id)
                    if context:
                        if context.current_npc:
                            prompt = colorize(f"[与{context.current_npc}对话] > ", Colors.BLUE + Colors.BOLD)
                        elif getattr(context.phase, "value", context.phase) == "combat":
                            prompt = colorize("[战斗中] > ", Colors.RED + Colors.BOLD)

                user_input = input(prompt).strip()

                if not user_input:
                    continue

                # 处理命令
                if user_input.startswith("/"):
                    await self._handle_command(user_input)
                else:
                    # 普通输入
                    await self.process_input(user_input)

            except KeyboardInterrupt:
                print("\n")
                self.running = False
            except EOFError:
                self.running = False
            except Exception as e:
                print_error(f"发生错误: {e}")

        print(colorize("\n再见！", Colors.CYAN))

    async def _handle_command(self, command: str) -> None:
        """处理命令"""
        parts = command.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ("/quit", "/exit", "/q"):
            self.running = False

        elif cmd == "/help":
            self.print_help()

        elif cmd == "/setup":
            await self.setup_demo_world()

        elif cmd == "/start":
            await self.start_game()

        elif cmd == "/scene":
            if not arg:
                print_error("用法: /scene <场景ID>")
                print(f"可用场景: {', '.join(self.demo_scenes.keys())}")
            else:
                await self.enter_scene(arg)

        elif cmd == "/talk":
            if not arg:
                print_error("用法: /talk <NPC ID>")
                print(f"可用NPC: {', '.join(self.demo_characters.keys())}")
            else:
                await self.talk_to(arg)

        elif cmd == "/leave":
            await self.leave_dialogue()

        elif cmd == "/status":
            self.print_status()

        elif cmd == "/combat":
            await self.trigger_combat()

        elif cmd == "/action":
            if not arg:
                print_error("用法: /action <行动ID>")
            else:
                await self.combat_action(arg)

        elif cmd == "/day":
            if self.session_id:
                result = await self.gm_service.advance_day(self.world_id, self.session_id)
                print_system(result.get("response", ""))

        else:
            # 尝试作为系统命令处理
            await self.process_input(command)


def main() -> None:
    """主入口"""
    import argparse

    parser = argparse.ArgumentParser(description="Game Master CLI")
    parser.add_argument("--setup-demo", action="store_true", help="设置演示世界后退出")
    args = parser.parse_args()

    cli = GameMasterCLI()

    if args.setup_demo:
        asyncio.run(cli.setup_demo_world())
    else:
        asyncio.run(cli.run())


if __name__ == "__main__":
    main()
