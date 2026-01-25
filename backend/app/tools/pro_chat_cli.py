#!/usr/bin/env python3
"""
Pro Chat CLI - 测试Pro与Flash联动的对话功能

用法:
    # 单次对话
    python -m app.tools.pro_chat_cli chat <world_id> <character_id> "<消息>"

    # 交互式对话
    python -m app.tools.pro_chat_cli interactive <world_id> <character_id>

示例:
    # 单次对话测试
    python -m app.tools.pro_chat_cli chat test_world gorn "你好，最近怎么样？"
    python -m app.tools.pro_chat_cli chat test_world gorn "你还记得那个帮你修炉子的人吗？"

    # 交互式对话
    python -m app.tools.pro_chat_cli interactive test_world gorn
"""
import asyncio
import sys
from typing import List

from app.models.pro import ChatMessage, ChatRequest, SceneContext
from app.services.pro_service import ProService


async def single_chat(world_id: str, character_id: str, message: str):
    """单次对话测试"""
    pro_service = ProService()

    print(f"\n💬 对话测试")
    print(f"世界: {world_id}, 角色: {character_id}")
    print("-" * 50)

    # 获取角色信息
    profile = await pro_service.get_profile(world_id, character_id)
    print(f"角色: {profile.name} ({profile.occupation or '未知职业'})")
    print("-" * 50)

    print(f"\n👤 你: {message}")

    request = ChatRequest(message=message)
    response = await pro_service.chat(world_id, character_id, request)

    if response.tool_called:
        print(f"\n🔍 [Pro调用了记忆工具]")
        print(f"   查询: {response.recall_query}")
        print(f"   记忆: {response.recalled_memory[:100]}..." if response.recalled_memory and len(response.recalled_memory) > 100 else f"   记忆: {response.recalled_memory}")

    print(f"\n🎭 {profile.name}: {response.response}")


async def interactive_chat(world_id: str, character_id: str):
    """交互式对话"""
    pro_service = ProService()

    print(f"\n💬 交互式对话")
    print(f"世界: {world_id}, 角色: {character_id}")
    print("-" * 50)

    # 获取角色信息
    profile = await pro_service.get_profile(world_id, character_id)
    if not profile.name:
        print(f"⚠️ 角色 {character_id} 没有设置profile，请先运行:")
        print(f"   python -m app.tools.flash_natural_cli setup {world_id} {character_id}")
        return

    print(f"角色: {profile.name}")
    if profile.occupation:
        print(f"职业: {profile.occupation}")
    if profile.personality:
        print(f"性格: {profile.personality[:50]}..." if len(profile.personality) > 50 else f"性格: {profile.personality}")
    print("-" * 50)
    print("输入 'quit' 或 'exit' 退出对话")
    print("输入 'history' 查看对话历史")
    print("输入 'clear' 清空对话历史")
    print("-" * 50)

    conversation_history: List[ChatMessage] = []

    # 可选：设置场景
    scene = SceneContext(
        description="一个普通的日子",
        location=profile.metadata.get("default_location", "未知地点"),
    )

    while True:
        try:
            user_input = input(f"\n👤 你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n再见！")
            break

        if not user_input:
            continue

        if user_input.lower() in ('quit', 'exit'):
            print("\n再见！")
            break

        if user_input.lower() == 'history':
            if not conversation_history:
                print("（对话历史为空）")
            else:
                print("\n📜 对话历史:")
                for msg in conversation_history:
                    role = "你" if msg.role == "user" else profile.name
                    print(f"  {role}: {msg.content[:50]}..." if len(msg.content) > 50 else f"  {role}: {msg.content}")
            continue

        if user_input.lower() == 'clear':
            conversation_history.clear()
            print("（对话历史已清空）")
            continue

        # 发送消息
        request = ChatRequest(
            message=user_input,
            scene=scene,
            conversation_history=conversation_history,
        )

        try:
            response = await pro_service.chat(world_id, character_id, request)

            if response.tool_called:
                print(f"\n   🔍 [调用记忆: {response.recall_query}]")

            print(f"\n🎭 {profile.name}: {response.response}")

            # 更新对话历史
            conversation_history.append(ChatMessage(role="user", content=user_input))
            conversation_history.append(ChatMessage(role="assistant", content=response.response))

            # 限制历史长度
            if len(conversation_history) > 20:
                conversation_history = conversation_history[-20:]

        except Exception as e:
            print(f"\n❌ 错误: {e}")


async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    command = sys.argv[1]

    if command == "chat":
        if len(sys.argv) < 5:
            print("用法: python -m app.tools.pro_chat_cli chat <world_id> <character_id> <message>")
            return
        await single_chat(sys.argv[2], sys.argv[3], sys.argv[4])

    elif command == "interactive":
        if len(sys.argv) < 4:
            print("用法: python -m app.tools.pro_chat_cli interactive <world_id> <character_id>")
            return
        await interactive_chat(sys.argv[2], sys.argv[3])

    else:
        print(f"未知命令: {command}")
        print(__doc__)


if __name__ == "__main__":
    asyncio.run(main())
