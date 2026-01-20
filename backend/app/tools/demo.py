"""
工具调用系统演示脚本

运行方式：
    cd backend
    python -m app.tools.demo
"""
import asyncio
import json
from app.tools import ToolService, ToolExecutor, ToolName


# 自定义工具处理器示例
class CustomExecutor(ToolExecutor):
    """自定义执行器示例 - 可以对接实际数据源"""
    
    def __init__(self):
        super().__init__()
        # 覆盖默认处理器
        self.register(ToolName.SEARCH_MEMORY, self._custom_search)
    
    def _custom_search(self, keywords: list, search_type: str = "all", limit: int = 5):
        """自定义搜索实现"""
        print(f"  [Custom Search] keywords={keywords}, type={search_type}")
        return {
            "results": [
                {
                    "type": "artifact",
                    "topic_id": "thread_python_001",
                    "title": f"Python {keywords[0]} 完整指南",
                    "snippet": f"详细讲解了 {', '.join(keywords)} 的使用方法和最佳实践...",
                    "relevance": 0.92
                }
            ],
            "total": 1,
            "note": "这是自定义搜索结果"
        }


async def demo_basic():
    """基础演示 - 使用默认执行器"""
    print("\n" + "="*60)
    print("演示 1: 基础工具调用")
    print("="*60)
    
    service = ToolService()
    
    result = await service.run(
        user_message="帮我搜索一下之前讨论过的 Python 装饰器内容",
        system_prompt="你是一个智能助手，可以使用工具来搜索用户的记忆和知识库。"
    )
    
    print(f"\n📝 最终响应:\n{result.response}")
    print(f"\n🔧 工具调用次数: {len(result.tool_calls)}")
    for tc in result.tool_calls:
        print(f"  - {tc.name}({json.dumps(tc.args, ensure_ascii=False)})")
        if tc.result:
            print(f"    结果: {'成功' if tc.result.success else '失败'}")
    print(f"\n🧠 思考: {result.thinking.summary[:100]}..." if result.thinking.summary else "")
    print(f"📊 总轮次: {result.total_rounds}")


async def demo_custom_executor():
    """演示 - 使用自定义执行器"""
    print("\n" + "="*60)
    print("演示 2: 自定义工具执行器")
    print("="*60)
    
    custom_executor = CustomExecutor()
    service = ToolService(executor=custom_executor)
    
    result = await service.run(
        user_message="搜索关于函数式编程的讨论",
        system_prompt="你是一个智能助手，请使用 search_memory 工具帮用户查找信息。"
    )
    
    print(f"\n📝 最终响应:\n{result.response}")
    print(f"\n🔧 工具调用: {[tc.name for tc in result.tool_calls]}")


async def demo_multiple_tools():
    """演示 - 多工具组合"""
    print("\n" + "="*60)
    print("演示 3: 多工具组合调用")
    print("="*60)
    
    service = ToolService()
    
    result = await service.run(
        user_message="先列出所有主题，然后获取第一个主题的详细内容",
        system_prompt="""你是一个智能助手，可以使用以下工具：
- list_topics: 列出所有主题
- get_artifact: 获取主题详细内容
请先调用 list_topics，根据结果再调用 get_artifact。"""
    )
    
    print(f"\n📝 最终响应:\n{result.response}")
    print(f"\n🔧 工具调用链:")
    for i, tc in enumerate(result.tool_calls, 1):
        print(f"  {i}. {tc.name}")
        print(f"     参数: {json.dumps(tc.args, ensure_ascii=False)}")
    print(f"\n📊 总轮次: {result.total_rounds}")


async def demo_stream():
    """演示 - 流式输出"""
    print("\n" + "="*60)
    print("演示 4: 流式工具调用")
    print("="*60)
    
    service = ToolService()
    
    print("\n🔄 流式输出:")
    async for event in service.run_stream(
        user_message="搜索 Python 相关的内容",
        system_prompt="你是一个智能助手，请使用工具帮助用户。"
    ):
        event_type = event.get("type")
        if event_type == "thought":
            print(f"💭 [思考] {event['text'][:50]}...")
        elif event_type == "answer":
            print(f"📝 [回答] {event['text']}", end="")
        elif event_type == "tool_call":
            print(f"\n🔧 [调用工具] {event['name']}({json.dumps(event['args'], ensure_ascii=False)})")
        elif event_type == "tool_result":
            status = "✅" if event['success'] else "❌"
            print(f"   {status} [结果] {str(event['data'])[:80]}...")
        elif event_type == "done":
            print(f"\n\n✅ 完成，共 {event['total_rounds']} 轮")


async def demo_no_tool_needed():
    """演示 - 无需工具的情况"""
    print("\n" + "="*60)
    print("演示 5: 无需工具调用")
    print("="*60)
    
    service = ToolService()
    
    result = await service.run(
        user_message="你好，请做个自我介绍",
        system_prompt="你是一个智能助手。如果用户只是闲聊，直接回复即可，不需要使用工具。"
    )
    
    print(f"\n📝 响应:\n{result.response}")
    print(f"\n🔧 工具调用次数: {len(result.tool_calls)} (预期为 0)")


async def main():
    """运行所有演示"""
    print("\n🚀 Gemini 3 工具调用系统演示")
    print("=" * 60)
    
    try:
        await demo_basic()
        await demo_custom_executor()
        await demo_multiple_tools()
        await demo_stream()
        await demo_no_tool_needed()
        
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "="*60)
    print("✅ 演示完成")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())
