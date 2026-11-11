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
    "x": {"type": "number", "description": "横坐标（默认按最近一次截图的图片像素）"},
    "y": {"type": "number", "description": "纵坐标"},
    "coordinate_mode": {
        "type": "string",
        "enum": ["auto", "image", "screen", "normalized"],
        "description": "auto=有有效截图时按图片像素，无图时兼容屏幕像素；image=严格图片像素；screen=显式屏幕绝对像素；normalized=0~1。旧图/越界拒绝。",
        "default": "auto",
    },
    "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
    "frame_id": {"type":"string","description":"screenshot 返回的帧编号，绑定本次定位/输入；输入后需重新截图；不可与 screen 模式混用"},
    "expected_pid": {"type":"integer","minimum":1,"description":"list_windows 返回的目标 PID，配合 handle 防止窗口身份变化"},
    "clicks": {"type": "integer", "minimum": 1, "maximum": 3, "default": 1,
               "description": "点击次数，2 表示双击"},
    "modifiers": {"type": "string", "description": "点击时按住的修饰键，如 ctrl、shift+ctrl"},
    "duration": {"type": "number", "description": "动作持续时间（秒）"},
    "amount": {"type": "integer", "description": "滚动格数，正数向上/向右，负数向下/向左"},
    "horizontal": {"type": "boolean", "default": False},
    "text": {"type": "string"},
    "press_enter": {"type": "boolean", "default": False},
    "use_clipboard": {"type": "string", "enum": ["always", "auto", "never"], "default": "always",
                      "description": "文本输入通道。默认 always 走剪贴板（不经过输入法，逐字保真）；"
                                     "auto 对纯 ASCII 走逐字输入，可能被中文输入法把半角标点转成全角；"
                                     "never 强制逐字输入。"},
    "keys": {"type": "string", "description": '按键或组合键，如 "enter"、"ctrl+shift+s"、"win+r"'},
    "repeat": {"type": "integer", "minimum": 1, "maximum": 50, "default": 1},
    "hold_ms": {"type": "integer", "minimum": 0, "default": 0},
    "title": {"type": "string", "description": "窗口标题（支持模糊匹配）"},
    "handle": {"type": "integer", "description": "窗口句柄（HWND）"},
    "action": {
        "type": "string",
        "enum": ["maximize", "minimize", "restore", "close", "focus",
                 "topmost", "untopmost", "move", "resize"],
    },
    "path": {"type": "string", "description": "可执行文件、文档或 URL"},
    "args": {"type": "string"},
    "wait": {"type": "number", "default": 0.8, "description": "启动后等待秒数"},
    "command": {"type": "string"},
    "timeout": {"type": "number", "default": 15.0},
    "cwd": {"type": "string"},
    "ms": {"type": "integer", "minimum": 0, "maximum": 30000, "default": 500},
    "reason": {"type": "string"},
    "monitor": {"type": "string", "default": "primary",
                "description": '"primary" 主屏 / "all" 全部拼接 / 显示器序号'},
    "region": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4,
               "description": "裁剪区域 [x, y, width, height]（屏幕绝对坐标）"},
    "scale": {"type": "number", "default": 0.0, "description": "缩放比，0=自动"},
    "grid": {"type": "boolean", "description": "是否叠加坐标网格"},
    "include_hidden": {"type": "boolean", "default": False},
    "limit": {"type": "integer", "default": 50},
    "width": {"type": "integer"},
    "height": {"type": "integer"},
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


def tool_specs() -> list[dict[str, Any]]:
    """返回 OpenAI tools 数组（name / description / parameters）。"""
    return [
        {
            "name": "screen_info",
            "description": "获取屏幕/会话信息：显示器、分辨率、DPI 缩放、输入后端状态、活动窗口、安全策略。开始操作或怀疑坐标偏移时先调用。",
            "parameters": _params(),
        },
        {
            "name": "screenshot",
            "description": "截屏并返回带坐标网格的图片及换算元数据。坐标单位为图片像素，可直接传给 click。",
            "parameters": _params("monitor", "region", "scale", "grid"),
        },
        {
            "name": "click",
            "description": "在指定位置点击鼠标。坐标为最近一次截图的图片像素（默认）。",
            "parameters": _params("x", "y", "button", "clicks", "coordinate_mode", "modifiers", "frame_id",
                                  required=["x", "y"]),
        },
        {
            "name": "move_mouse",
            "description": "移动鼠标到指定位置（不点击），可触发悬停菜单。",
            "parameters": _params("x", "y", "coordinate_mode", "duration", "frame_id", required=["x", "y"]),
        },
        {
            "name": "drag",
            "description": "按住鼠标从起点拖到终点，用于拖拽文件、框选文字、调整窗口。",
            "parameters": _params("x1", "y1", "x2", "y2", "coordinate_mode", "duration", "button", "frame_id",
                                  required=["x1", "y1", "x2", "y2"]),
        },
        {
            "name": "scroll",
            "description": "滚动鼠标滚轮，正数向上/向右。",
            "parameters": _params("amount", "x", "y", "coordinate_mode", "horizontal", "frame_id",
                                  required=["amount"]),
        },
        {
            "name": "type_text",
            "description": "输入文本，支持中文（内部走剪贴板粘贴）。",
            "parameters": _params("text", "press_enter", "use_clipboard", "frame_id", required=["text"]),
        },
        {
            "name": "press_key",
            "description": '按键或组合键，如 "enter"、"ctrl+c"、"alt+tab"、"win+r"。',
            "parameters": _params("keys", "repeat", "hold_ms", "frame_id", required=["keys"]),
        },
        {
            "name": "clipboard_get",
            "description": "读取剪贴板文本。",
            "parameters": _params(),
        },
        {
            "name": "clipboard_set",
            "description": "写入剪贴板文本。",
            "parameters": _params("text", required=["text"]),
        },
        {
            "name": "list_windows",
            "description": "列出顶层窗口（标题、进程、句柄、位置尺寸、是否前台）。",
            "parameters": _params("include_hidden", "limit"),
        },
        {
            "name": "activate_window",
            "description": "把窗口切到前台，比点击任务栏更可靠。",
            "parameters": _window_params("title", "handle", "expected_pid", "timeout"),
        },
        {
            "name": "window_action",
            "description": "窗口操作：最大化/最小化/还原/关闭/置顶/移动/缩放。",
            "parameters": _window_params("action", "title", "handle", "x", "y", "width", "height", "expected_pid", "timeout",
                                  required=["action"]),
        },
        {
            "name": "launch_app",
            "description": "启动程序或打开文档/URL。",
            "parameters": _params("path", "args", "wait", required=["path"]),
        },
        {
            "name": "run_command",
            "description": "执行命令行（默认禁用，需 allow_shell=true）。返回 stdout/stderr/退出码。",
            "parameters": _params("command", "timeout", "cwd", required=["command"]),
        },
        {
            "name": "wait",
            "description": "等待指定毫秒，用于等界面加载。",
            "parameters": _params("ms"),
        },
        {
            "name": "stop",
            "description": "紧急停止：锁定所有输入类工具，直到调用 resume。",
            "parameters": _params("reason"),
        },
        {
            "name": "resume",
            "description": "解除紧急停止。",
            "parameters": _params(),
        },
    ]


def openai_tools() -> list[dict[str, Any]]:
    """OpenAI chat.completions 的 tools 参数格式。"""
    return [{"type": "function", "function": spec} for spec in tool_specs()]


def dumps(pretty: bool = True) -> str:
    return json.dumps(openai_tools(), ensure_ascii=False, indent=2 if pretty else None)


def names() -> list[str]:
    return [spec["name"] for spec in tool_specs()]
