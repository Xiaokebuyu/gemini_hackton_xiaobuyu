"""party/ — 队伍子系统：队伍生命周期 + 成员管理。"""
# PartyService 依赖已删除的 PartyStore，顶层 import 会断裂。
# 调用方直接 from app.world.party.party_service import PartyService
