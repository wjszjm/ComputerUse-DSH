from __future__ import annotations

import asyncio
import json

import pytest

from wdc.config import Config
from wdc.mcp_server import INSTRUCTIONS, build_server


@pytest.fixture(scope="module")
def server():
    return build_server(Config(audit_log=False))


def _tools(server):
    async def collect():
        return await server.list_tools()

    return asyncio.run(collect())


def test_server_builds_with_instructions(server):
    assert server.name
    assert len(INSTRUCTIONS) > 200
    assert "screenshot" in INSTRUCTIONS


def test_tool_count_and_names(server):
    tools = _tools(server)
    names = {t.name for t in tools}
    expected = {
        "screen_info", "screenshot", "click", "move_mouse", "drag", "scroll",
        "type_text", "press_key", "clipboard_get", "clipboard_set",
        "list_windows", "activate_window", "window_action", "launch_app",
        "run_command", "wait", "stop", "resume",
        "window_screenshot", "inspect_controls", "control_action", "recognize_text", "locate_target", "batch_windows",
    }
    assert names == expected


def test_tools_have_schemas(server):
    for tool in _tools(server):
        assert tool.description
        # MCP 1.x 用 camelCase，2.x 的 pydantic 模型用 snake_case
        schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)
        assert schema and schema["type"] == "object"


def test_call_screen_info(server, gui):
    result = asyncio.run(server.call_tool("screen_info", {}))
    # 1.x 返回 (content_list, structured)，2.x 返回 CallToolResult
    if isinstance(result, tuple):
        blocks = result[0]
    else:
        blocks = getattr(result, "content", result)
    assert blocks
    assert blocks[0].type == "text"
    assert json.loads(blocks[0].text)["ok"] is True


def test_call_unknown_tool(server):
    with pytest.raises(Exception):
        asyncio.run(server.call_tool("does_not_exist", {}))
