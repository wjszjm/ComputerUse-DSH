"""MCP SDK 版本兼容层。

MCP Python SDK 在 2.0 把 FastMCP 重命名为 MCPServer 并搬到 mcp.server.mcpserver，
1.x 则是 mcp.server.fastmcp.FastMCP。这里做运行时探测，两代都能跑。
"""

from __future__ import annotations

from typing import Any, Callable, Tuple

try:
    from mcp.server.mcpserver import Context as ToolContext
except ImportError:
    from mcp.server.fastmcp import Context as ToolContext


class McpCompatError(RuntimeError):
    pass


def load_server_class() -> Tuple[type, int]:
    """返回 (server 类, 主版本号)。"""
    try:  # MCP SDK >= 2.0
        from mcp.server.mcpserver import MCPServer  # type: ignore

        return MCPServer, 2
    except Exception:
        pass
    try:  # MCP SDK 1.x
        from mcp.server.fastmcp import FastMCP  # type: ignore

        return FastMCP, 1
    except Exception as exc:  # pragma: no cover - 取决于安装环境
        raise McpCompatError(
            "未找到可用的 MCP Python SDK。请执行：pip install \"mcp>=1.2.0\""
        ) from exc


class ToolRegistrar:
    """抹平两代 SDK 的 tool() 参数差异。"""

    def __init__(self, server: Any, major: int) -> None:
        self.server = server
        self.major = major

    def add(self, fn: Callable[..., Any], name: str, description: str,
            annotations: Any = None) -> None:
        kwargs: dict[str, Any] = {"name": name, "description": description}
        if annotations is not None:
            kwargs["annotations"] = annotations
        if self.major >= 2:
            # 工具返回显式 CallToolResult，保留业务错误标记，不再包结构化输出。
            kwargs["structured_output"] = False
        self.server.tool(**kwargs)(fn)


def build_annotations(**kwargs: Any) -> Any:
    """构造 ToolAnnotations，字段名两代一致；构造失败时降级为 None。"""
    try:
        from mcp.types import ToolAnnotations

        return ToolAnnotations(**kwargs)
    except Exception:
        return None
