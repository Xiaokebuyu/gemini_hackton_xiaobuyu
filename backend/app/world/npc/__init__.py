"""npc/ — NPC 机械子系统：实例池、上下文窗口、相关度排序、队友可见性。"""
from app.world.npc.context_window import ContextWindow, count_tokens
from app.world.npc.instance_manager import InstanceManager, NPCInstance
from app.world.npc.reactor import NPCReactor, MAX_REACTIONS_PER_ROUND
from app.world.npc.visibility import TeammateVisibilityManager
