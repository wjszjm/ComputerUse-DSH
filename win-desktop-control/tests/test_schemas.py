from __future__ import annotations


from wdc import schemas
from wdc.config import Config


def test_schema_names_unique():
    names = schemas.names()
    assert len(names) == len(set(names))
    assert "screenshot" in names and "click" in names


def test_every_schema_has_parameters():
    for spec in schemas.tool_specs():
        assert spec["description"], spec["name"]
        params = spec["parameters"]
        assert params["type"] == "object"
        assert isinstance(params["properties"], dict)


def test_required_fields_present():
    click = next(s for s in schemas.tool_specs() if s["name"] == "click")
    assert click["parameters"]["required"] == ["x", "y"]
    assert set(click["parameters"]["properties"]) >= {"x", "y", "button", "coordinate_mode"}


def test_openai_wrapper_shape():
    tools = schemas.openai_tools()
    assert tools[0]["type"] == "function"
    assert "name" in tools[0]["function"]


def test_schema_matches_mcp_tools():
    """OpenAI schema 与 MCP 注册的工具必须同名，否则两套客户端行为会漂移。"""
    from wdc.mcp_server import build_server

    server = build_server(Config(audit_log=False))
    import asyncio

    async def collect():
        return [t.name for t in await server.list_tools()]

    mcp_names = set(asyncio.run(collect()))
    assert mcp_names == set(schemas.names())


def test_lazy_catalog_matches_source_and_runtime_contract():
    """Registration must not start a process; its bundled definitions must not drift."""
    import asyncio
    import runpy
    from pathlib import Path
    from wdc.mcp_server import build_server

    root = Path(__file__).resolve().parents[2]
    generator = runpy.run_path(str(root / 'scripts/build-tool-catalog.py'))
    assert (root / 'dsh-wdc-computer-use/lib/tool-catalog.js').read_text(encoding='utf-8') == generator['render']()
    server = build_server(Config(audit_log=False))
    actual = {t.name: t.input_schema for t in asyncio.run(server.list_tools())}
    expected = generator['catalog']()['tools']
    assert set(actual) == {t['name'] for t in expected}
    for tool in expected:
        schema = tool['inputSchema']
        assert set(schema['properties']) == set(actual[tool['name']]['properties']), tool['name']
        assert set(schema.get('required', [])) == set(actual[tool['name']].get('required', [])), tool['name']
