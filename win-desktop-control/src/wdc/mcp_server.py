"""MCP Server：把 ComputerUse 的能力暴露成标准 MCP 工具（stdio 传输）。

客户端配置示例（Claude Desktop / Cursor / Cherry Studio 等）：
    {
      "mcpServers": {
        "win-desktop-control": {
          "command": "D:\\\\wdc\\\\.venv\\\\Scripts\\\\python.exe",
          "args": ["-m", "wdc", "serve"]
        }
      }
    }
"""

from __future__ import annotations

import sys
import re
from collections.abc import Mapping
from threading import Event
from typing import Any, Callable, List, Optional

import anyio

from . import __version__
from .config import Config, load_config
from .mcp_compat import ToolRegistrar, build_annotations, load_server_class
from .tools import ComputerUse, ToolResult, observation_scope
from .mcp_compat import ToolContext
from . import pointer_events

INSTRUCTIONS = """Operate the user's Windows desktop. Prefer an identified window and semantic controls when reliable.
For visual targeting, pass image pixels and a fresh frame_id; the service maps coordinates. Input, changed window/layout, and frames older than 30 seconds require a new observation. Never bypass a stale frame with screen mode. Background frames are observation-only.
In DSH, use the current session's screenshot for typing and shortcuts. Reinspect controls after input.
Bind windows by handle and PID and resolve ambiguous matches before acting. A sent or not_verified result requires state readback or a fresh screenshot. Observe before retrying an uncertain action.
Follow DSH approval and confirm irreversible actions with the user. Do not enter credentials or interact with lock or UAC screens. Stop on secure_desktop, emergency_stop, or read_only; do not retry."""


def _content(mcp_types: Any, result: ToolResult) -> List[Any]:
    blocks: List[Any] = [mcp_types.TextContent(type="text", text=result.compact_text())]
    if result.image:
        blocks.append(
            mcp_types.ImageContent(type="image", data=result.image_b64(), mimeType=result.mime)
        )
    return blocks


def build_server(config: Optional[Config] = None) -> Any:
    cfg = config or load_config()
    app = ComputerUse(cfg,isolate_uia=True)

    try:
        from mcp import types as mcp_types
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"MCP SDK 不可用：{exc}") from exc

    server_cls, major = load_server_class()
    try:
        server = server_cls(name=cfg.server_name, instructions=INSTRUCTIONS, version=__version__)
    except TypeError:  # 部分 1.x 版本没有 version 参数
        server = server_cls(name=cfg.server_name, instructions=INSTRUCTIONS)
    reg = ToolRegistrar(server, major)

    def out(result: ToolResult) -> Any:
        return mcp_types.CallToolResult(content=_content(mcp_types, result), isError=not result.ok)

    def request_meta(ctx,key):
        try:
            meta = getattr(ctx.request_context,"meta",None) if ctx is not None else None
            return meta.get(key) if isinstance(meta,Mapping) else getattr(meta,key,None)
        except (ValueError,AttributeError):
            return None

    def scoped_out(ctx, invoke, *, pointer=False):
        try:
            request_id = ctx.request_id if ctx is not None else None
        except (ValueError, AttributeError):
            request_id = None  # Direct SDK calls have no transport request.
        scope = request_meta(ctx,"wdcScope")
        token = observation_scope.set(scope if isinstance(scope,str) and re.fullmatch(r"[0-9a-f-]{36}",scope) else None)
        try:
            if pointer:
                with pointer_events.operation(request_id):
                    return out(invoke())
            return out(invoke())
        finally:
            observation_scope.reset(token)

    async def read_out(ctx, invoke):
        cancel = Event()
        try:
            return await anyio.to_thread.run_sync(
                lambda: scoped_out(ctx,lambda: invoke(cancel)),abandon_on_cancel=True,
            )
        finally:
            cancel.set()

    # ------------------------------------------------------------------ #
    # 观测
    # ------------------------------------------------------------------ #
    def screen_info(ctx: ToolContext = None) -> List[Any]:
        """获取屏幕与会话信息：显示器列表、分辨率、DPI 缩放、输入后端是否可用、
        当前活动窗口、安全策略状态、最近一次截图的坐标换算参数。

        在开始操作、或怀疑坐标偏移（高 DPI 缩放、多显示器）时先调用它。
        """
        return scoped_out(ctx,app.screen_info)

    async def screenshot(monitor: str = "primary", region: Optional[List[int]] = None,
                         scale: float = 0.0, grid: Optional[bool] = None, ctx: ToolContext = None) -> List[Any]:
        """截取屏幕并返回图片（附带坐标换算元数据）。

        Args:
            monitor: "primary" 主屏 / "all" 全部显示器拼接 / 显示器序号 0..N。
            region: 可选裁剪区域 [x, y, width, height]（屏幕绝对坐标）。
            scale: 缩放比，0 表示按 max_image_edge 自动缩放；1 表示原尺寸。
            grid: 是否叠加坐标网格，None 表示用配置默认值（默认开启）。
        """
        return await read_out(ctx,lambda cancel: app.screenshot(monitor=monitor,region=region,scale=scale,grid=grid))

    # ------------------------------------------------------------------ #
    # 鼠标
    # ------------------------------------------------------------------ #
    def click(x: float, y: float, button: str = "left", clicks: int = 1,
              coordinate_mode: str = "auto", modifiers: str = "", frame_id: Optional[str] = None, ctx: ToolContext = None) -> List[Any]:
        """在指定位置点击鼠标。

        Args:
            x: 横坐标（默认按最近一次截图的图片像素解释）。
            y: 纵坐标。
            button: left / right / middle。
            clicks: 点击次数 1-3，2 表示双击。
            coordinate_mode: auto（默认）/ image / screen / normalized。
            modifiers: 点击时按住的修饰键，如 "ctrl"、"shift+ctrl"。
        """
        return scoped_out(ctx, lambda: app.click(x, y, button=button, clicks=clicks,
                            coordinate_mode=coordinate_mode, modifiers=modifiers, frame_id=frame_id),pointer=True)

    def move_mouse(x: float, y: float, coordinate_mode: str = "auto",
                   duration: Optional[float] = None, frame_id: Optional[str] = None, ctx: ToolContext = None) -> List[Any]:
        """把鼠标移动到指定位置（不点击），可用于触发悬停菜单。"""
        return scoped_out(ctx, lambda: app.move(x, y, coordinate_mode=coordinate_mode, duration=duration,frame_id=frame_id),pointer=True)

    def drag(x1: float, y1: float, x2: float, y2: float, coordinate_mode: str = "auto",
             duration: float = 0.5, button: str = "left", frame_id: Optional[str] = None, ctx: ToolContext = None) -> List[Any]:
        """按住鼠标从 (x1, y1) 拖到 (x2, y2)，用于拖拽文件、框选文字、调整窗口。"""
        return scoped_out(ctx, lambda: app.drag(x1, y1, x2, y2, coordinate_mode=coordinate_mode,
                            duration=duration, button=button,frame_id=frame_id),pointer=True)

    def scroll(amount: int, x: Optional[float] = None, y: Optional[float] = None,
               coordinate_mode: str = "auto", horizontal: bool = False, frame_id: Optional[str] = None, ctx: ToolContext = None) -> List[Any]:
        """滚动鼠标滚轮。amount 正数向上/向右，负数向下/向左，1 约等于 1 格。

        不传 x/y 时在当前鼠标位置滚动。
        """
        return scoped_out(ctx, lambda: app.scroll(amount, x=x, y=y, coordinate_mode=coordinate_mode,
                              horizontal=horizontal,frame_id=frame_id),pointer=True)

    # ------------------------------------------------------------------ #
    # 键盘
    # ------------------------------------------------------------------ #
    def type_text(text: str, press_enter: bool = False,
                  use_clipboard: str = "always", frame_id: Optional[str] = None, ctx: ToolContext = None) -> List[Any]:
        """输入文本（支持中文/表情，默认走剪贴板粘贴）。

        Args:
            text: 要输入的文本。
            press_enter: 输入完成后是否按回车。
            use_clipboard: always（默认）/ auto / never。默认走剪贴板，因为逐字输入
                会经过输入法：中文输入法激活时会把半角标点转成全角（"7,006,652" 变成
                "7，006，652"），而工具仍返回 ok、字符数也正确，内容却已经错了。
                剪贴板粘贴不经过输入法，逐字保真。确需逐字输入时显式传 never。
        """
        return scoped_out(ctx,lambda: app.type_text(text,press_enter=press_enter,use_clipboard=use_clipboard,frame_id=frame_id))

    def press_key(keys: str, repeat: int = 1, hold_ms: int = 0, frame_id: Optional[str] = None,
                  ctx: ToolContext = None) -> List[Any]:
        """按键或组合键，例如 "enter"、"tab"、"ctrl+c"、"alt+tab"、"win+r"，"f5"。

        repeat 为重复次数；hold_ms > 0 时改为按住指定毫秒再松开（用于长按方向键）。
        """
        return scoped_out(ctx,lambda: app.press_key(keys,repeat=repeat,hold_ms=hold_ms,frame_id=frame_id))

    def clipboard_get() -> List[Any]:
        """读取剪贴板文本（用于从界面批量取数据，比截图 OCR 可靠）。"""
        return out(app.clipboard_get())

    def clipboard_set(text: str) -> List[Any]:
        """写入剪贴板文本。"""
        return out(app.clipboard_set(text))

    # ------------------------------------------------------------------ #
    # 窗口
    # ------------------------------------------------------------------ #
    def list_windows(include_hidden: bool = False, limit: int = 50,
                     title: str = "", detail: str = "compact") -> List[Any]:
        """列出当前所有顶层窗口（标题、进程名、句柄、位置尺寸、是否前台/最小化）。"""
        return out(app.list_windows(include_hidden,limit,title,detail))

    def activate_window(title: Optional[str] = None, handle: Optional[int] = None,
                        expected_pid: Optional[int] = None, timeout: float = 2.0) -> List[Any]:
        """把窗口切到前台（支持标题模糊匹配或窗口句柄）。

        比点击任务栏更稳，适合在操作某个应用前调用。
        """
        return out(app.activate_window(title=title, handle=handle,expected_pid=expected_pid,timeout=timeout))

    def window_action(action: str, title: Optional[str] = None, handle: Optional[int] = None,
                      x: Optional[int] = None, y: Optional[int] = None,
                      width: Optional[int] = None, height: Optional[int] = None,
                      expected_pid: Optional[int] = None, timeout: float = 2.0) -> List[Any]:
        """对窗口执行操作。

        action: maximize / minimize / restore / close / focus / topmost / untopmost / move / resize
        move/resize 时可配合 x, y, width, height（屏幕绝对像素）。
        """
        return out(app.window_action(action, title=title, handle=handle,
                                     x=x, y=y, width=width, height=height,expected_pid=expected_pid,timeout=timeout))

    def launch_app(path: str, args: str = "", wait: float = 0.8) -> List[Any]:
        """启动程序或打开文档/URL，例如 "notepad"、"calc"、"C:\\\\path\\\\app.exe"。

        wait 为启动后等待秒数，便于紧接着截图。
        """
        return out(app.launch_app(path, args=args, wait=wait))

    def run_command(command: str, timeout: float = 15.0,
                    cwd: Optional[str] = None) -> List[Any]:
        """执行命令行（默认禁用，需在 config.json 里 allow_shell=true）。

        适合批量文件操作、查询系统信息；返回 stdout/stderr/exit code。
        """
        return out(app.run_command(command, timeout=timeout, cwd=cwd))

    # ------------------------------------------------------------------ #
    # 控制
    # ------------------------------------------------------------------ #
    async def wait(ms: int = 500, condition: Optional[dict] = None, timeout_ms: int = 3000,
                   ctx: ToolContext = None) -> List[Any]:
        if condition is None:
            delay = max(0,min(ms,30000))
            if delay:
                await anyio.sleep(delay/1000)
            return out(ToolResult(True,"wait",{"waited_ms":delay}))
        return await read_out(ctx,lambda cancel: app.wait(ms,condition,timeout_ms,cancel))

    def stop(reason: str = "agent requested") -> List[Any]:
        """紧急停止：立刻锁定所有输入类工具，直到调用 resume。

        当发现操作可能造成破坏、或用户要求立刻停手时调用。
        """
        return out(app.emergency_stop(reason))

    def resume() -> List[Any]:
        """解除紧急停止状态。"""
        return out(app.resume())

    async def window_screenshot(handle: int, expected_pid: int, scale: float = 0.0,
                                background: bool = False, crop: Optional[List[int]] = None, ctx: ToolContext = None):
        return await read_out(ctx,lambda cancel: app.window_screenshot(handle,expected_pid,scale,background,crop,cancel))

    async def inspect_controls(handle: int, expected_pid: int, name: str = "", control_type: str = "",
                               limit: int = 40, background: bool = False, detail: str = "compact", ctx: ToolContext = None):
        return await read_out(ctx,lambda cancel: app.inspect_controls(handle,expected_pid,name,control_type,limit,background,detail,cancel))

    async def control_action(handle: int, expected_pid: int, control_id: str, operation: str,
                             text: str = "", background: bool = False, ctx: ToolContext = None):
        identity = request_meta(ctx,"wdcControl")
        if request_meta(ctx,"wdcScope") and not isinstance(identity,dict):
            return out(ToolResult(False,"control_action",error="控件引用缺少最近观察。",code="stale_control"))
        return await anyio.to_thread.run_sync(
            lambda: out(app.control_action(handle,expected_pid,control_id,operation,text,background,identity)),
        )

    async def recognize_text(frame_id: str, language: str = "en-US", query: str = "",
                             detail: str = "compact", limit: int = 160, ctx: ToolContext = None):
        return await read_out(ctx,lambda cancel: app.recognize_text(frame_id,language,query,detail,limit))

    async def locate_target(handle: int, expected_pid: int, name: str, frame_id: Optional[str] = None,
                            language: str = "en-US", background: bool = False, ctx: ToolContext = None):
        return await read_out(ctx,lambda cancel: app.locate_target(handle,expected_pid,name,frame_id,language,background,cancel))

    def batch_windows(steps: List[dict]):
        return out(app.batch_windows(steps))

    # ------------------------------------------------------------------ #
    # 注册
    # ------------------------------------------------------------------ #
    readonly = build_annotations(readOnlyHint=True, destructiveHint=False,
                                 idempotentHint=True, openWorldHint=True)
    local_read = build_annotations(readOnlyHint=True, destructiveHint=False,
                                   idempotentHint=True, openWorldHint=False)
    mutating = build_annotations(readOnlyHint=False, destructiveHint=False,
                                 idempotentHint=False, openWorldHint=True)
    destructive = build_annotations(readOnlyHint=False, destructiveHint=True,
                                    idempotentHint=False, openWorldHint=True)

    tools: list[tuple[Callable[..., Any], str, str, Any]] = [
        (screen_info, "screen_info", screen_info.__doc__ or "", local_read),
        (screenshot, "screenshot", screenshot.__doc__ or "", readonly),
        (click, "click", click.__doc__ or "", mutating),
        (move_mouse, "move_mouse", move_mouse.__doc__ or "", mutating),
        (drag, "drag", drag.__doc__ or "", mutating),
        (scroll, "scroll", scroll.__doc__ or "", mutating),
        (type_text, "type_text", type_text.__doc__ or "", mutating),
        (press_key, "press_key", press_key.__doc__ or "", mutating),
        (clipboard_get, "clipboard_get", clipboard_get.__doc__ or "", local_read),
        (clipboard_set, "clipboard_set", clipboard_set.__doc__ or "", mutating),
        (list_windows, "list_windows", list_windows.__doc__ or "", local_read),
        (activate_window, "activate_window", activate_window.__doc__ or "", mutating),
        (window_action, "window_action", window_action.__doc__ or "", destructive),
        (launch_app, "launch_app", launch_app.__doc__ or "", destructive),
        (run_command, "run_command", run_command.__doc__ or "", destructive),
        (wait, "wait", wait.__doc__ or "", local_read),
        (stop, "stop", stop.__doc__ or "", mutating),
        (resume, "resume", resume.__doc__ or "", mutating),
    ]

    from .schemas import tool_specs
    descriptions = {spec["name"]: spec["description"] for spec in tool_specs()}
    for fn, name, _description, annos in tools:
        reg.add(fn, name, descriptions[name], annos)
    for fn, annotations in [(window_screenshot,readonly),(inspect_controls,readonly),(control_action,mutating),
                            (recognize_text,readonly),(locate_target,readonly),(batch_windows,mutating)]:
        reg.add(fn,fn.__name__,descriptions[fn.__name__],annotations)

    return server


def run_stdio(config: Optional[Config] = None) -> None:
    server = build_server(config)
    server.run(transport="stdio")


def main() -> None:  # pragma: no cover
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass
    run_stdio()


if __name__ == "__main__":  # pragma: no cover
    main()
