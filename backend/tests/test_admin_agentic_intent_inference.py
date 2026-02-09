from app.models.admin_protocol import FlashOperation, FlashResponse, IntentType
from app.services.admin.admin_coordinator import AdminCoordinator


def _result(op: FlashOperation) -> FlashResponse:
    return FlashResponse(success=True, operation=op, result={})


def test_infer_agentic_intent_prefers_player_input_for_team_action():
    coordinator = AdminCoordinator.__new__(AdminCoordinator)
    intent = AdminCoordinator._infer_agentic_intent_type(
        coordinator,
        player_input="我邀请见习圣女加入队伍",
        flash_results=[_result(FlashOperation.GET_STATUS)],
    )
    assert intent == IntentType.TEAM_INTERACTION


def test_infer_agentic_intent_prefers_player_input_for_npc_dialogue():
    coordinator = AdminCoordinator.__new__(AdminCoordinator)
    intent = AdminCoordinator._infer_agentic_intent_type(
        coordinator,
        player_input="我想和见习圣女聊聊",
        flash_results=[_result(FlashOperation.GET_PROGRESS)],
    )
    assert intent == IntentType.NPC_INTERACTION
