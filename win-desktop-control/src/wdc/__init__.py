"""Win Desktop Control —— 面向 LLM Agent 的 Windows 桌面操控插件。

设计要点：
1. 核心逻辑不依赖 pyautogui 的导入期副作用（延迟导入），任何平台都能 import，
   便于在 CI / Linux 上做单元测试。
2. 所有屏幕坐标以「虚拟桌面绝对像素」为唯一真值（DPI 感知开启后即为物理像素）。
3. 暴露三种形态：MCP Server（stdio）、OpenAI function-calling schema、CLI。
"""

from __future__ import annotations

__version__ = "2.2.0"
__all__ = ["__version__", "main"]

PROTOCOL_VERSION = "2024-11-05"


def main() -> int:  # pragma: no cover - 转发到 CLI
    from .cli import main as _main

    return _main()
