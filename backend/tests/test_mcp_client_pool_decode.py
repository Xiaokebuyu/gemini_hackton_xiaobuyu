from types import SimpleNamespace

from app.services.mcp_client_pool import MCPClientPool


def test_decode_tool_result_unwraps_wrapped_json_result():
    pool = MCPClientPool()
    fake_result = SimpleNamespace(
        structuredContent={"result": '{"success": true, "value": 42}'},
        content=[],
    )

    decoded = pool._decode_tool_result(fake_result)

    assert decoded == {"success": True, "value": 42}


def test_decode_tool_result_keeps_normal_structured_content():
    pool = MCPClientPool()
    fake_result = SimpleNamespace(
        structuredContent={"success": True, "message": "ok"},
        content=[],
    )

    decoded = pool._decode_tool_result(fake_result)

    assert decoded == {"success": True, "message": "ok"}


def test_decode_tool_result_parses_text_json():
    pool = MCPClientPool()
    fake_result = SimpleNamespace(
        structuredContent=None,
        content=[SimpleNamespace(text='{"foo": "bar"}')],
    )

    decoded = pool._decode_tool_result(fake_result)

    assert decoded == {"foo": "bar"}
