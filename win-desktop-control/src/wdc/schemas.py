"""OpenAI / 通用 function-calling schema 导出。

给不使用 MCP 的客户端（例如直接调 OpenAI、DeepSeek、Qwen 的 chat completions，
或自研 Agent 框架）使用。与 MCP 工具集保持同名同参，tests/test_schemas.py 会校验一致性。

生成方式：
    wdc tools --format openai > tools.json
"""

from __future__ import annotations

import json
from typing import Any

_PARAM = {
    "x": {"description": 'Horizontal image pixel unless coordinate_mode says otherwise.', "type": "number"},
    "y": {"description": 'Vertical coordinate.', "type": "number"},
    "coordinate_mode": {"description": 'auto: image with frame, screen without; image: strict; screen: absolute pixels; normalized: 0-1.',
        "type": "string",
        "enum": ["auto", "image", "screen", "normalized"],
        "default": "auto",
    },
    "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
    "frame_id": {"description": 'ID from a fresh screenshot. Expires after input; incompatible with screen mode.', "type":"string"},
    "expected_pid": {"description": 'PID from list_windows; use with handle.', "type":"integer","minimum":1},
    "clicks": {"description": '2 for double-click.', "type": "integer", "minimum": 1, "maximum": 3, "default": 1},
    "modifiers": {"description": 'Held keys, e.g. ctrl+shift.', "type": "string"},
    "duration": {"description": 'Seconds.', "type": "number"},
    "amount": {"description": 'Wheel steps; positive up/right, negative down/left.', "type": "integer"},
    "horizontal": {"type": "boolean", "default": False},
    "text": {"type": "string"},
    "press_enter": {"type": "boolean", "default": False},
    "use_clipboard": {"description": 'always preserves exact text; auto/never can be affected by IME.', "type": "string", "enum": ["always", "auto", "never"], "default": "always"},
    "keys": {"description": 'Key or chord such as enter or ctrl+shift+s.', "type": "string"},
    "repeat": {"type": "integer", "minimum": 1, "maximum": 50, "default": 1},
    "hold_ms": {"type": "integer", "minimum": 0, "default": 0},
    "title": {"description": 'Window title; ambiguous matches fail.', "type": "string"},
    "handle": {"description": 'Window handle (HWND).', "type": "integer"},
    "action": {
        "type": "string",
        "enum": ["maximize", "minimize", "restore", "close", "focus",
                 "topmost", "untopmost", "move", "resize"],
    },
    "path": {"description": 'Executable, document, or URL.', "type": "string"},
    "args": {"type": "string"},
    "wait": {"description": 'Seconds after launch.', "type": "number", "default": 0.8},
    "command": {"type": "string"},
    "timeout": {"type": "number", "default": 15.0},
    "cwd": {"type": "string"},
    "ms": {"type": "integer", "minimum": 0, "maximum": 30000, "default": 500},
    "reason": {"type": "string"},
    "monitor": {"description": 'primary, all, or a monitor index.', "type": "string", "default": "primary"},
    "region": {"description": 'Absolute screen pixels [x,y,width,height].', "type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4},
    "scale": {"description": '0 auto, 1 native resolution.', "type": "number", "default": 0.0},
    "grid": {"description": 'Draw a coordinate grid.', "type": "boolean"},
    "background": {"description": 'Use the window provider without activating. Background images cannot target input.', "type": "boolean", "default": False},
    "include_hidden": {"type": "boolean", "default": False},
    "limit": {"type": "integer", "default": 50},
    "width": {"type": "integer"},
    "height": {"type": "integer"},
    "name": {"description": 'Exact control name or text.', "type":"string"},
    "control_type": {"description": 'UIA type, e.g. ButtonControl.', "type":"string"},
    "control_id": {"description": 'Control ID from inspect_controls.', "type":"string"},
    "operation": {"type":"string","enum":["invoke","set_value","select","check","uncheck"]},
    "detail": {"type":"string","enum":["compact","full"],"default":"compact"},
    "query": {"type":"string","description":"Exact text on one OCR line; omit to list words.","maxLength":160},
    "crop": {"type":"array","items":{"type":"integer"},"minItems":4,"maxItems":4,
             "description":"Window-relative physical pixels [x,y,width,height]. Use scale=1 for fine detail."},
    "timeout_ms": {"type":"integer","minimum":0,"maximum":10000,"default":3000},
    "condition": {"type":"object","required":["kind"],"additionalProperties":False,
                  "properties":{
                      "kind":{"type":"string","enum":["window_visible","control_enabled"]},
                      "title":{"type":"string","description":"Exact title for window_visible; use instead of handle/PID."},
                      "handle":{"type":"integer","minimum":1},
                      "expected_pid":{"type":"integer","minimum":1},
                      "name":{"type":"string","description":"Exact control name for control_enabled."},
                      "control_type":{"type":"string"},
                  }},
    "language": {"type":"string","enum":["en-US","zh-CN","zh-TW"],"default":"en-US"},
    "steps": {"type":"array","minItems":1,"maxItems":6,"items":{
        "type":"object","required":["action","handle","expected_pid"],"additionalProperties":False,
        "properties":{
            "action":{"type":"string","enum":["wait_visible","focus","confirm_foreground","maximize","minimize","restore","move","resize","topmost","untopmost"]},
            "timeout_ms":{"type":"integer","minimum":0,"maximum":10000,
                          "description":"Only for wait_visible. Default 3000."},
            **{key:{"type":"integer"} for key in ("handle","expected_pid","x","y","width","height")},
        },
    }},
    "x1": {"type": "number"}, "y1": {"type": "number"},
    "x2": {"type": "number"}, "y2": {"type": "number"},
}


def _params(*names: str, required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {n: dict(_PARAM[n]) for n in names},
        "required": required or [],
        "additionalProperties": False,
    }


def _window_params(*names: str, required: list[str] | None = None) -> dict[str, Any]:
    params=_params(*names,required=required)
    params['properties']['timeout'].update(default=2.0,minimum=0,maximum=5)
    return params


def _query_params(*names: str, required: list[str], default_limit: int, max_limit: int) -> dict[str, Any]:
    params = _params(*names, "limit", required=required)
    params["properties"]["limit"].update(default=default_limit,minimum=1,maximum=max_limit)
    return params


def tool_specs() -> list[dict[str, Any]]:
    """返回 OpenAI tools 数组（name / description / parameters）。"""
    return [
        {
            "name": "screen_info",
            "description": 'Read resolution, DPI, foreground window and input state.',
            "parameters": _params(),
        },
        {
            "name": "screenshot",
            "description": 'Capture visual context or verify an outcome. Crop known regions; use native-size crops for fine detail.',
            "parameters": _params("monitor", "region", "scale", "grid"),
        },
        {
            "name": "click",
            "description": 'Click a target using image pixels and a fresh frame_id. Reobserve before retrying.',
            "parameters": _params("x", "y", "button", "clicks", "coordinate_mode", "modifiers", "frame_id",
                                  required=["x", "y"]),
        },
        {
            "name": "move_mouse",
            "description": 'Move or hover the pointer without clicking.',
            "parameters": _params("x", "y", "coordinate_mode", "duration", "frame_id", required=["x", "y"]),
        },
        {
            "name": "drag",
            "description": 'Drag between two points in the same coordinate space and frame.',
            "parameters": _params("x1", "y1", "x2", "y2", "coordinate_mode", "duration", "button", "frame_id",
                                  required=["x1", "y1", "x2", "y2"]),
        },
        {
            "name": "scroll",
            "description": 'Scroll a page or control.',
            "parameters": _params("amount", "x", "y", "coordinate_mode", "horizontal", "frame_id",
                                  required=["amount"]),
        },
        {
            "name": "type_text",
            "description": 'Enter exact text, including CJK. Verify the field; clipboard contents do not prove entry.',
            "parameters": _params("text", "press_enter", "use_clipboard", "frame_id", required=["text"]),
        },
        {
            "name": "press_key",
            "description": 'Send a key or chord. Verify the resulting app state.',
            "parameters": _params("keys", "repeat", "hold_ms", "frame_id", required=["keys"]),
        },
        {
            "name": "clipboard_get",
            "description": 'Read clipboard text.',
            "parameters": _params(),
        },
        {
            "name": "clipboard_set",
            "description": 'Set clipboard text.',
            "parameters": _params("text", required=["text"]),
        },
        {
            "name": "list_windows",
            "description": 'Find top-level windows and their handle/PID; filter by title when known.',
            "parameters": _params("include_hidden", "limit", "title", "detail"),
        },
        {
            "name": "activate_window",
            "description": 'Bring a window to the foreground and verify focus.',
            "parameters": _window_params("title", "handle", "expected_pid", "timeout"),
        },
        {
            "name": "window_action",
            "description": 'Change a window state and verify the outcome.',
            "parameters": _window_params("action", "title", "handle", "x", "y", "width", "height", "expected_pid", "timeout",
                                  required=["action"]),
        },
        {
            "name": "launch_app",
            "description": 'Launch a program or open a document or URL.',
            "parameters": _params("path", "args", "wait", required=["path"]),
        },
        {
            "name": "run_command",
            "description": 'Run a shell command if explicitly enabled.',
            "parameters": _params("command", "timeout", "cwd", required=["command"]),
        },
        {
            "name": "wait",
            "description": 'Wait for a window or UIA control condition; use ms for a fixed delay.',
            "parameters": _params("ms", "condition", "timeout_ms"),
        },
        {
            "name": "stop",
            "description": 'Stop and latch all desktop input immediately.',
            "parameters": _params("reason"),
        },
        {
            "name": "resume",
            "description": 'Resume new desktop input after a stop.',
            "parameters": _params(),
        },
        {"name":"window_screenshot","description":'Capture one identified window; prefer over full desktop capture when the target is known.',
         "parameters":_params("handle","expected_pid","scale","background","crop",required=["handle","expected_pid"])},
        {"name":"inspect_controls","description":'Read UIA controls in an identified window. Use exact name/type when available.',
         "parameters":_query_params("handle","expected_pid","name","control_type","background","detail",
                                    required=["handle","expected_pid"],default_limit=40,max_limit=80)},
        {"name":"control_action","description":'Invoke, set, or select an identified UIA control. Invoke may need independent verification.',
         "parameters":_params("handle","expected_pid","control_id","operation","text","background",required=["handle","expected_pid","control_id","operation"])},
        {"name":"recognize_text","description":'Read text from a valid screenshot with local OCR. Use query if you know the text.',
         "parameters":_query_params("frame_id","language","query","detail",
                                    required=["frame_id"],default_limit=160,max_limit=160)},
        {"name":"locate_target","description":'Find one UIA target or use OCR with its matching window screenshot. Observation only.',
         "parameters":_params("handle","expected_pid","name","frame_id","language","background",required=["handle","expected_pid","name"])},
        {"name":"batch_windows","description":'Run verified actions on one window, stopping at the first failed step.',
         "parameters":_params("steps",required=["steps"])},
    ]


def openai_tools() -> list[dict[str, Any]]:
    """OpenAI chat.completions 的 tools 参数格式。"""
    return [{"type": "function", "function": spec} for spec in tool_specs()]


def dumps(pretty: bool = True) -> str:
    return json.dumps(openai_tools(), ensure_ascii=False, indent=2 if pretty else None)


def names() -> list[str]:
    return [spec["name"] for spec in tool_specs()]
