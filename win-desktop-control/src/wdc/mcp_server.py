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
from typing import Any, Callable, List, Optional

from . import __version__
from .config import Config, load_config
from .mcp_compat import ToolRegistrar, build_annotations, load_server_class
from .tools import ComputerUse, ToolResult
from .mcp_compat import ToolContext
from . import pointer_events

INSTRUCTIONS = """你是运行在用户 Windows 桌面上的电脑操控代理，工作方式与 Computer Use 一致。

【标准循环】
1. 调用 screenshot() 看当前屏幕。返回的图片带红色坐标网格与刻度，坐标单位是「图片像素」。
2. 在图中定位目标，调用 click(x, y)、type_text(text)、press_key(keys) 等执行动作。
3. 再调用 screenshot() 确认结果，然后进行下一步。

【坐标规则（重要）】
- click/drag/scroll 的坐标默认按「最近一次 screenshot 的图片像素」解释，服务端会按缩放比自动换算成屏幕绝对坐标。
- 截图可能被等比缩小（最长边受 max_image_edge 限制），所以图片坐标 ≠ 屏幕坐标，务必依赖默认的换算而不是自己乘系数。
- 如果确实要传屏幕绝对坐标，显式设置 coordinate_mode="screen"。
- 如果跳过截图直接操作，coordinate_mode="screen" 更安全。
- screenshot 返回 frame_id；图像定位操作建议携带它，绑定所观察的帧。image 模式无帧/越界、旧图超过 30 秒、输入后旧图或窗口/布局变化都会拒绝，请重新截图，不要改用 screen 绕过。
- 操作返回 verification.status=not_verified 只表示输入调用完成，不能据此宣布目标成功。使用新截图/目标状态读回复核；clipboard_get 不能证明目标输入框内容。
- 窗口标题有多个同级候选会拒绝；先 list_windows，使用 handle 和 expected_pid 绑定身份。

【操作建议】
- 点击前先确认窗口已激活：activate_window(title=...) 比盲点任务栏更可靠。
- 输入中文/长文本用 type_text（内部走剪贴板粘贴），不要用 press_key 逐字敲。
- 快捷键写成 "ctrl+shift+s" 这种形式；单独按键写 "enter" / "f5" / "esc"。
- 滚动量 amount 正数向上、负数向下，1 约等于 1 格滚轮。
- 需要等待界面加载时用 wait(ms)，不要连续空转截图。

【安全规则】
- 删除文件、清空回收站、发送消息、支付、关闭未保存文档等不可逆操作，先向用户说明并等待确认。
- 不要输入密码、验证码等敏感凭据；不要尝试操作锁屏或 UAC 提权界面。
- 返回 error_code=secure_desktop / emergency_stop / read_only 时立即停止，把原因告诉用户，不要重试。
- 同一个点击连续 2 次没反应，就重新 screenshot 分析，不要继续猜坐标。
"""


def _content(mcp_types: Any, result: ToolResult) -> List[Any]:
    blocks: List[Any] = [mcp_types.TextContent(type="text", text=result.text())]
    if result.image:
        blocks.append(
            mcp_types.ImageContent(type="image", data=result.image_b64(), mimeType=result.mime)
        )
    return blocks


def build_server(config: Optional[Config] = None) -> Any:
    cfg = config or load_config()
    app = ComputerUse(cfg)

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

    def pointer_out(ctx, invoke):
        try:
            request_id = ctx.request_id if ctx is not None else None
        except (ValueError, AttributeError):
            request_id = None  # Direct SDK calls have no transport request.
        with pointer_events.operation(request_id):
            return out(invoke())

    # ------------------------------------------------------------------ #
    # 观测
    # ------------------------------------------------------------------ #
    def screen_info() -> List[Any]:
        """获取屏幕与会话信息：显示器列表、分辨率、DPI 缩放、输入后端是否可用、
        当前活动窗口、安全策略状态、最近一次截图的坐标换算参数。

        在开始操作、或怀疑坐标偏移（高 DPI 缩放、多显示器）时先调用它。
        """
        return out(app.screen_info())

    def screenshot(monitor: str = "primary", region: Optional[List[int]] = None,
                   scale: float = 0.0, grid: Optional[bool] = None) -> List[Any]:
        """截取屏幕并返回图片（附带坐标换算元数据）。

        Args:
            monitor: "primary" 主屏 / "all" 全部显示器拼接 / 显示器序号 0..N。
            region: 可选裁剪区域 [x, y, width, height]（屏幕绝对坐标）。
            scale: 缩放比，0 表示按 max_image_edge 自动缩放；1 表示原尺寸。
            grid: 是否叠加坐标网格，None 表示用配置默认值（默认开启）。
        """
        return out(app.screenshot(monitor=monitor, region=region, scale=scale, grid=grid))

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
        return pointer_out(ctx, lambda: app.click(x, y, button=button, clicks=clicks,
                             coordinate_mode=coordinate_mode, modifiers=modifiers, frame_id=frame_id))

    def move_mouse(x: float, y: float, coordinate_mode: str = "auto",
                   duration: Optional[float] = None, frame_id: Optional[str] = None, ctx: ToolContext = None) -> List[Any]:
        """把鼠标移动到指定位置（不点击），可用于触发悬停菜单。"""
        return pointer_out(ctx, lambda: app.move(x, y, coordinate_mode=coordinate_mode, duration=duration,frame_id=frame_id))

    def drag(x1: float, y1: float, x2: float, y2: float, coordinate_mode: str = "auto",
             duration: float = 0.5, button: str = "left", frame_id: Optional[str] = None, ctx: ToolContext = None) -> List[Any]:
        """按住鼠标从 (x1, y1) 拖到 (x2, y2)，用于拖拽文件、框选文字、调整窗口。"""
        return pointer_out(ctx, lambda: app.drag(x1, y1, x2, y2, coordinate_mode=coordinate_mode,
                            duration=duration, button=button,frame_id=frame_id))

    def scroll(amount: int, x: Optional[float] = None, y: Optional[float] = None,
               coordinate_mode: str = "auto", horizontal: bool = False, frame_id: Optional[str] = None, ctx: ToolContext = None) -> List[Any]:
        """滚动鼠标滚轮。amount 正数向上/向右，负数向下/向左，1 约等于 1 格。

        不传 x/y 时在当前鼠标位置滚动。
        """
        return pointer_out(ctx, lambda: app.scroll(amount, x=x, y=y, coordinate_mode=coordinate_mode,
                              horizontal=horizontal,frame_id=frame_id))

    # ------------------------------------------------------------------ #
    # 键盘
    # ------------------------------------------------------------------ #
    def type_text(text: str, press_enter: bool = False,
                  use_clipboard: str = "always", frame_id: Optional[str] = None) -> List[Any]:
        """输入文本（支持中文/表情，默认走剪贴板粘贴）。

        Args:
            text: 要输入的文本。
            press_enter: 输入完成后是否按回车。
            use_clipboard: always（默认）/ auto / never。默认走剪贴板，因为逐字输入
                会经过输入法：中文输入法激活时会把半角标点转成全角（"7,006,652" 变成
                "7，006，652"），而工具仍返回 ok、字符数也正确，内容却已经错了。
                剪贴板粘贴不经过输入法，逐字保真。确需逐字输入时显式传 never。
        """
        return out(app.type_text(text, press_enter=press_enter, use_clipboard=use_clipboard,frame_id=frame_id))

    def press_key(keys: str, repeat: int = 1, hold_ms: int = 0, frame_id: Optional[str] = None) -> List[Any]:
        """按键或组合键，例如 "enter"、"tab"、"ctrl+c"、"alt+tab"、"win+r"，"f5"。

        repeat 为重复次数；hold_ms > 0 时改为按住指定毫秒再松开（用于长按方向键）。
        """
        return out(app.press_key(keys, repeat=repeat, hold_ms=hold_ms,frame_id=frame_id))

    def clipboard_get() -> List[Any]:
        """读取剪贴板文本（用于从界面批量取数据，比截图 OCR 可靠）。"""
        return out(app.clipboard_get())

    def clipboard_set(text: str) -> List[Any]:
        """写入剪贴板文本。"""
        return out(app.clipboard_set(text))

    # ------------------------------------------------------------------ #
    # 窗口
    # ------------------------------------------------------------------ #
    def list_windows(include_hidden: bool = False, limit: int = 50) -> List[Any]:
        """列出当前所有顶层窗口（标题、进程名、句柄、位置尺寸、是否前台/最小化）。"""
        return out(app.list_windows(include_hidden=include_hidden, limit=limit))

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
    def wait(ms: int = 500) -> List[Any]:
        """等待指定毫秒（0-30000），用于等界面加载/动画结束。"""
        return out(app.wait(ms))

    def stop(reason: str = "agent requested") -> List[Any]:
        """紧急停止：立刻锁定所有输入类工具，直到调用 resume。

        当发现操作可能造成破坏、或用户要求立刻停手时调用。
        """
        return out(app.emergency_stop(reason))

    def resume() -> List[Any]:
        """解除紧急停止状态。"""
        return out(app.resume())

    def window_screenshot(handle: int, expected_pid: int, scale: float = 0.0, background: bool = False):
        return out(app.window_screenshot(handle,expected_pid,scale,background))

    def inspect_controls(handle: int, expected_pid: int, name: str = "", control_type: str = "", limit: int = 40, background: bool = False):
        return out(app.inspect_controls(handle,expected_pid,name,control_type,limit,background))

    def control_action(handle: int, expected_pid: int, control_id: str, operation: str, text: str = "", background: bool = False):
        return out(app.control_action(handle,expected_pid,control_id,operation,text,background))

    def recognize_text(frame_id: str, language: str = "en-US"):
        return out(app.recognize_text(frame_id,language))

    def locate_target(handle: int, expected_pid: int, name: str, frame_id: Optional[str] = None, language: str = "en-US", background: bool = False):
        return out(app.locate_target(handle,expected_pid,name,frame_id,language,background))

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

    for fn, name, description, annos in tools:
        reg.add(fn, name, description.strip(), annos)
    from .schemas import tool_specs
    descriptions={spec["name"]:spec["description"] for spec in tool_specs()}
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
