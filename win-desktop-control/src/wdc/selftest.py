"""兼容性自检：一次性验证「系统 / 依赖 / 权限 / 截屏 / 输入 / MCP 协议」是否可用。

用法：
    wdc selftest --safe      # 只读探测，不动鼠标键盘（安装脚本默认）
    wdc selftest             # 完整体检，会短暂移动鼠标 + 读写剪贴板
    wdc selftest --json report.json --markdown report.md
"""

from __future__ import annotations

import json
import locale
import os
import platform
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable

from . import __version__, capture, input_ctl, platform_win, windows
from .config import Config, load_config

STATUS_ORDER = {"fail": 0, "warn": 1, "pass": 2, "skip": 3}
STATUS_LABEL = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "skip": "SKIP"}


@dataclass
class Check:
    id: str
    title: str
    status: str
    detail: str
    hint: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ok(cid: str, title: str, detail: str, **data: Any) -> Check:
    return Check(cid, title, "pass", detail, "", data)


def _warn(cid: str, title: str, detail: str, hint: str = "", **data: Any) -> Check:
    return Check(cid, title, "warn", detail, hint, data)


def _fail(cid: str, title: str, detail: str, hint: str = "", **data: Any) -> Check:
    return Check(cid, title, "fail", detail, hint, data)


def _skip(cid: str, title: str, detail: str) -> Check:
    return Check(cid, title, "skip", detail)


# --------------------------------------------------------------------------- #
# 单项检查
# --------------------------------------------------------------------------- #
def check_python() -> Check:
    version = sys.version_info
    text = platform.python_version()
    if version < (3, 9):
        return _fail("python", "Python 版本", f"{text} 过低", "请安装 Python 3.9 - 3.13")
    if version >= (3, 14):
        return _warn("python", "Python 版本", f"{text} 较新，第三方依赖可能尚未适配",
                     "建议使用 Python 3.11 / 3.12")
    return _ok("python", "Python 版本", f"{text} ({platform.architecture()[0]})",
               executable=sys.executable, bits=platform.architecture()[0])


def check_os() -> Check:
    if not platform_win.IS_WINDOWS:
        return _warn("os", "操作系统",
                     f"{platform.system()} {platform.release()} —— 非 Windows，"
                     "仅查询类工具可用，输入/窗口控制不可用",
                     "请在 Windows 10 1809+ 或 Windows 11 上运行")
    release = platform.release()
    build = platform.version().split(".")[-1]
    try:
        build_num = int(build)
    except ValueError:
        build_num = 0
    if build_num and build_num < 17763:
        return _fail("os", "操作系统", f"Windows {release} build {build} 过低",
                     "需要 Windows 10 1809 (build 17763) 及以上")
    name = "Windows 11" if build_num >= 22000 else f"Windows {release}"
    return _ok("os", "操作系统", f"{name} build {build} ({platform.machine()})",
               release=release, build=build_num)


def check_dependencies() -> Check:
    import importlib.metadata as md

    required = {
        "mss": "9.0.1",
        "Pillow": "10.0.0",
        "pyautogui": "0.9.54",
        "pyperclip": "1.8.2",
        "mcp": "1.2.0",
    }
    found: dict[str, str] = {}
    missing: list[str] = []
    for name in required:
        try:
            found[name] = md.version(name)
        except Exception:
            missing.append(name)

    # 逐一 import，捕获二进制层面的加载失败（比版本号更能反映真实可用性）
    import_errors: dict[str, str] = {}
    for module in ("mss", "PIL", "pyautogui", "pyperclip", "mcp"):
        try:
            __import__(module)
        except Exception as exc:
            import_errors[module] = f"{type(exc).__name__}: {exc}"

    if missing:
        return _fail("deps", "依赖包", f"缺少：{', '.join(missing)}",
                     "在项目目录执行 install.bat，或 pip install -r requirements.txt",
                     versions=found)
    if import_errors:
        return _fail("deps", "依赖包", "；".join(f"{k} 导入失败" for k in import_errors),
                     str(import_errors), versions=found, errors=import_errors)
    return _ok("deps", "依赖包", " · ".join(f"{k} {v}" for k, v in found.items()), versions=found)


def check_dpi() -> Check:
    mode = platform_win.setup_dpi_awareness()
    scale = platform_win.get_dpi_scale()
    if not platform_win.IS_WINDOWS:
        return _warn("dpi", "DPI 感知", f"非 Windows（mode={mode}）", "在 Windows 上运行才能验证")
    if mode == "none":
        return _fail("dpi", "DPI 感知", "未能开启 DPI 感知，高缩放屏幕上点击会偏移",
                     "以普通权限重新运行；若仍失败请反馈系统版本")
    text = f"{mode}，缩放 {int(scale * 100)}%"
    if mode == "system" and scale > 1.0:
        return _warn("dpi", "DPI 感知", text, "多显示器不同缩放时建议升级到 Windows 10 1703+")
    return _ok("dpi", "DPI 感知", text, mode=mode, scale=scale)


def check_monitors() -> Check:
    try:
        monitors = capture.list_monitors()
    except Exception as exc:
        return _fail("monitors", "显示器枚举", f"{type(exc).__name__}: {exc}",
                     "确认未处于锁屏状态；远程桌面请使用控制台会话")
    screens = [m for m in monitors if m.index >= 1]
    if not screens:
        return _fail("monitors", "显示器枚举", "未检测到任何显示器", "检查显卡驱动 / 显示输出")
    primary = next((m for m in screens if m.primary), screens[0])
    return _ok("monitors", "显示器枚举",
               f"{len(screens)} 台，主屏 {primary.width}x{primary.height}",
               monitors=[m.to_dict() for m in monitors])


def check_capture() -> Check:
    try:
        started = time.perf_counter()
        frame = capture.capture(monitor="primary", scale=0.0, grid=False, max_edge=1600)
        elapsed = (time.perf_counter() - started) * 1000
        data, mime = capture.encode(frame, "png")
    except Exception as exc:
        return _fail("capture", "截屏", f"{type(exc).__name__}: {exc}",
                     "锁屏 / UAC 安全桌面会导致截屏失败；请解锁后重试")
    # 全黑检测：采样中心区域，判断是否整屏纯色（常见于 RDP 断连、显卡驱动异常）
    crop = frame.image.crop(
        (frame.image.width // 3, frame.image.height // 3,
         frame.image.width * 2 // 3, frame.image.height * 2 // 3)
    )
    raw = crop.tobytes()
    step = 3 * max(1, len(raw) // (3 * 800))
    unique = len({raw[i:i + 3] for i in range(0, len(raw), step)})
    status_detail = (f"{frame.image.width}x{frame.image.height}，"
                     f"{len(data) / 1024:.0f} KB，{elapsed:.0f} ms")
    if unique <= 2:
        return _warn("capture", "截屏", status_detail + "，画面近似纯色",
                     "若当前是锁屏或远程桌面断开，属正常现象",
                     unique_colors=unique, mime=mime, ms=elapsed)
    return _ok("capture", "截屏", status_detail, unique_colors=unique, mime=mime, ms=elapsed)


def check_input_backend() -> Check:
    status = input_ctl.backend_status()
    if not status.available:
        return _fail("input", "输入后端", f"{status.backend}: {status.detail}",
                     "确认 pyautogui 安装完整；Linux 桌面需 DISPLAY 环境变量")
    try:
        backend = input_ctl.get_backend()
        size = backend.screen_size()
    except Exception as exc:
        return _fail("input", "输入后端", str(exc), "重装 pyautogui：pip install --force-reinstall pyautogui")
    screen = platform_win.get_virtual_screen()
    detail = f"{status.backend}，报告屏幕 {size[0]}x{size[1]}"
    if platform_win.IS_WINDOWS and (size[0], size[1]) != (screen.width, screen.height) and len(
        capture.list_monitors()
    ) <= 2:
        return _warn("input", "输入后端",
                     detail + f"，与系统虚拟桌面 {screen.width}x{screen.height} 不一致",
                     "通常是 DPI 感知未生效或多屏拼接，点击坐标可能偏移")
    return _ok("input", "输入后端", detail, backend=status.backend, size=list(size))


def check_mouse_move(config: Config, safe: bool) -> Check:
    if safe:
        return _skip("mouse", "鼠标移动", "--safe 模式跳过（不会移动鼠标）")
    if not config.allow_input:
        return _skip("mouse", "鼠标移动", "输入已禁用（read_only / allow_input=false）")
    try:
        backend = input_ctl.get_backend()
        origin = backend.position()
        target = (min(40, backend.screen_size()[0] - 1), min(40, backend.screen_size()[1] - 1))
        backend.move_to(target[0], target[1], duration=0.05)
        time.sleep(0.08)
        landed = backend.position()
        backend.move_to(origin[0], origin[1], duration=0.05)
    except Exception as exc:
        return _fail("mouse", "鼠标移动", f"{type(exc).__name__}: {exc}", "检查是否有安全软件拦截模拟输入")
    if abs(landed[0] - target[0]) <= 2 and abs(landed[1] - target[1]) <= 2:
        return _ok("mouse", "鼠标移动", f"移动到 {target} 校验通过", landed=list(landed))
    return _fail("mouse", "鼠标移动", f"目标 {target}，实际落点 {landed}",
                 "坐标偏移通常由 DPI 缩放引起，请查看 DPI 检查项",
                 target=list(target), landed=list(landed))


def check_keyboard(config: Config, safe: bool) -> Check:
    if safe:
        return _skip("keyboard", "键盘事件", "--safe 模式跳过")
    if not config.allow_input:
        return _skip("keyboard", "键盘事件", "输入已禁用")
    try:
        backend = input_ctl.get_backend()
        backend.key_down("shift")
        backend.key_up("shift")
    except Exception as exc:
        return _fail("keyboard", "键盘事件", f"{type(exc).__name__}: {exc}", "检查是否被安全软件拦截")
    return _ok("keyboard", "键盘事件", "Shift 按下/释放正常（未产生文本输入）")


def check_clipboard(safe: bool) -> Check:
    if safe:
        return _skip("clipboard", "剪贴板", "--safe 模式跳过（不会改写剪贴板）")
    try:
        previous = input_ctl.clipboard_get()
        marker = f"wdc-selftest-{int(time.time())}"
        input_ctl.clipboard_set(marker)
        readback = input_ctl.clipboard_get()
        if previous:
            input_ctl.clipboard_set(previous)
        if readback != marker:
            return _fail("clipboard", "剪贴板", f"回读不一致：{readback!r}",
                         "剪贴板被其他程序占用，稍后重试")
        return _ok("clipboard", "剪贴板", "读写正常，已恢复原内容")
    except Exception as exc:
        return _fail("clipboard", "剪贴板", f"{type(exc).__name__}: {exc}",
                     "pip install --force-reinstall pyperclip")


def check_windows_api() -> Check:
    if not platform_win.IS_WINDOWS:
        return _skip("winapi", "窗口 API", "非 Windows 平台")
    try:
        items = windows.list_windows(limit=200)
        fg = windows.get_foreground_window()
    except Exception as exc:
        return _fail("winapi", "窗口 API", f"{type(exc).__name__}: {exc}", "以普通用户权限重试")
    if not items and not fg:
        return _warn("winapi", "窗口 API", "枚举结果为空", "可能在无交互桌面（服务/计划任务）中运行")
    return _ok("winapi", "窗口 API", f"可枚举 {len(items)} 个窗口，前台：{(fg or {}).get('title', '无')}",
               sample=(fg or {}).get("process", ""))


def check_uia() -> Check:
    try:
        import uiautomation  # noqa: F401, PLC0415
    except Exception:
        return _skip("uia", "UI Automation（可选）",
                     "未安装 uiautomation，不影响基础操控；需要控件级操作可 pip install uiautomation")
    return _ok("uia", "UI Automation（可选）", "uiautomation 可用")


def check_safety(config: Config, guard_status: dict[str, Any]) -> Check:
    problems = []
    if config.read_only:
        problems.append("只读模式")
    if not config.audit_log:
        problems.append("审计日志已关闭")
    if config.allow_shell:
        problems.append("run_command 已开放")
    detail = "，".join(problems) if problems else "只读关闭、审计开启、shell 默认禁用"
    if "run_command 已开放" in problems:
        return _warn("safety", "安全策略", detail, "确认这是有意开启；run_command 可执行任意命令")
    return _ok("safety", "安全策略", detail, **guard_status)


def check_audit(config: Config) -> Check:
    if not config.audit_log:
        return _skip("audit", "审计日志", "已在配置中关闭")
    from .safety import AuditRecord, SafetyGuard

    guard = SafetyGuard(config)
    guard.audit(AuditRecord(action="selftest", ok=True, params={"version": __version__}))
    path = config.audit_path
    if guard.audit_failures:
        return _fail("audit", "审计日志", f"写入失败：{path}",
                     "检查目录权限，或在 config.json 里改 audit_path")
    exists = os.path.isfile(path)
    return _ok("audit", "审计日志", f"可写入 {path}" if exists else f"路径可用 {path}", path=path)


def check_locale() -> Check:
    enc = (sys.stdout.encoding or "").lower()
    pref = locale.getpreferredencoding(False)
    if platform_win.IS_WINDOWS and "utf" not in enc and "65001" not in enc:
        return _warn("locale", "编码环境", f"stdout={enc}，首选={pref}",
                     "中文可能显示为乱码；建议 set PYTHONUTF8=1 或在终端执行 chcp 65001")
    return _ok("locale", "编码环境", f"stdout={enc}，首选={pref}")


def check_mcp_sdk() -> Check:
    try:
        from .mcp_compat import load_server_class

        server_cls, major = load_server_class()
    except Exception as exc:
        return _fail("mcp_sdk", "MCP SDK", str(exc), 'pip install "mcp>=1.2.0"')
    import importlib.metadata as md

    try:
        version = md.version("mcp")
    except Exception:
        version = "unknown"
    return _ok("mcp_sdk", "MCP SDK", f"mcp {version}（API v{major}，{server_cls.__name__}）",
               sdk=version, api_major=major)


def check_mcp_tools(config: Config) -> Check:
    import asyncio

    try:
        from .mcp_server import build_server

        server = build_server(config)
    except Exception as exc:
        return _fail("mcp_build", "MCP 工具注册", f"{type(exc).__name__}: {exc}",
                     "把完整报错反馈给开发者")

    names: list[str] = []
    try:
        async def _collect() -> list[str]:
            tools = await server.list_tools()
            return [getattr(t, "name", str(t)) for t in tools]

        names = asyncio.run(_collect())
    except Exception:
        # 部分 SDK 版本把工具存在私有管理器里，退一步直接读
        manager = getattr(server, "_tool_manager", None)
        if manager is not None and hasattr(manager, "list_tools"):
            try:
                names = [t.name for t in manager.list_tools()]
            except Exception:
                names = []
    if not names:
        return _warn("mcp_build", "MCP 工具注册", "Server 构建成功，但工具列表为空",
                     "检查 MCP SDK 版本是否被裁剪")
    return _ok("mcp_build", "MCP 工具注册", f"已注册 {len(names)} 个工具：{', '.join(names[:6])}…",
               tools=names)


def check_mcp_protocol(config: Config, timeout: float = 40.0) -> Check:
    """真实启动一个 stdio 子进程，走完 initialize → tools/list → tools/call 握手。"""
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    cmd = [sys.executable, "-m", "wdc", "serve"]
    cfg_path = _config_path_or_default()
    if cfg_path and os.path.isfile(cfg_path):
        cmd += ["--config", cfg_path]
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", env=env,
            cwd=os.getcwd(),
        )
    except Exception as exc:
        return _fail("mcp_protocol", "MCP 协议握手", f"无法启动子进程：{exc}", "检查 python -m wdc 是否可用")

    lines: "queue.Queue[str]" = queue.Queue()
    stderr_buf: list[str] = []

    def _reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line)

    def _err_reader() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_buf.append(line)
            if len(stderr_buf) > 200:
                del stderr_buf[:100]

    threading.Thread(target=_reader, daemon=True).start()
    threading.Thread(target=_err_reader, daemon=True).start()

    def _send(payload: dict[str, Any]) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        proc.stdin.flush()

    def _wait_for(msg_id: int) -> dict[str, Any] | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                line = lines.get(timeout=0.5)
            except queue.Empty:
                if proc.poll() is not None:
                    return None
                continue
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == msg_id:
                return message
        return None

    try:
        _send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "wdc-selftest", "version": __version__},
            },
        })
        init = _wait_for(1)
        if not init:
            return _fail("mcp_protocol", "MCP 协议握手", "initialize 无响应（超时或进程退出）",
                         f"手动执行验证：{' '.join(cmd)}", stderr="".join(stderr_buf)[-800:])
        if "error" in init:
            return _fail("mcp_protocol", "MCP 协议握手", f"initialize 返回错误：{init['error']}")

        server_info = (init.get("result") or {}).get("serverInfo") or {}
        protocol = (init.get("result") or {}).get("protocolVersion", "")

        _send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        _send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listed = _wait_for(2)
        if not listed:
            return _fail("mcp_protocol", "MCP 协议握手", "tools/list 无响应")
        tools = (listed.get("result") or {}).get("tools") or []
        if not tools:
            return _fail("mcp_protocol", "MCP 协议握手", "tools/list 返回空列表")

        # 再验证一次工具调用：调用只读的 screen_info
        _send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
               "params": {"name": "screen_info", "arguments": {}}})
        called = _wait_for(3)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    if not called:
        return _warn("mcp_protocol", "MCP 协议握手",
                     f"握手成功（{len(tools)} 个工具），但 screen_info 调用无响应",
                     "在目标机上手动运行 wdc serve 观察报错")
    if "error" in called:
        return _warn("mcp_protocol", "MCP 协议握手",
                     f"握手成功，screen_info 返回错误：{called['error']}")

    return _ok(
        "mcp_protocol", "MCP 协议握手",
        f"stdio 握手成功：{server_info.get('name', '?')} v{server_info.get('version', '?')}，"
        f"协议 {protocol}，{len(tools)} 个工具，screen_info 可调用",
        tools=len(tools), protocol=protocol,
    )


def _config_path_or_default() -> str:
    from .config import user_config_dir

    for candidate in (os.path.join(os.getcwd(), "config.json"),
                      str(user_config_dir() / "config.json")):
        if os.path.isfile(candidate):
            return candidate
    return os.path.join(os.getcwd(), "config.json")


def check_permissions() -> Check:
    info = platform_win.get_session_info()
    notes = []
    if info["secure_desktop"]:
        notes.append("当前处于安全桌面（锁屏/UAC）")
    if info["remote_session"]:
        notes.append("远程桌面会话")
    if info["elevated"]:
        notes.append("管理员权限")
    detail = f"用户 {info['user'] or '未知'} @ {info['computer'] or '本机'}"
    if notes:
        detail += "，" + "、".join(notes)
    if info["secure_desktop"]:
        return _warn("perm", "运行环境", detail, "解锁桌面后再运行自检，否则截屏与输入都会失败")
    if info["remote_session"]:
        return _warn("perm", "运行环境", detail, "远程会话下部分全屏/游戏窗口无法截取，建议本地运行")
    return _ok("perm", "运行环境", detail, elevated=info["elevated"])


def check_disk() -> Check:
    from .config import user_config_dir

    target = user_config_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".wdc-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as exc:
        return _fail("disk", "数据目录", f"{target} 不可写：{exc}",
                     "检查用户目录权限或杀毒软件拦截")
    try:
        free_gb = shutil.disk_usage(str(target)).free / (1024 ** 3)
    except Exception:
        free_gb = -1.0
    return _ok("disk", "数据目录", f"{target}（剩余 {free_gb:.1f} GB）", path=str(target))


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #
def _guarded(step: Callable[[], Check], seconds: float, label: str) -> Check:
    """单项超时保护：即使某个系统调用挂住（剪贴板被占用、驱动无响应），
    整份自检也要能返回结果，而不是卡死。"""
    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["check"] = step()
        except Exception as exc:  # noqa: BLE001
            box["check"] = _fail("internal", label,
                                 f"检查本身异常：{type(exc).__name__}: {exc}")

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        return _fail("timeout", label, f"检查超过 {seconds:.0f} 秒未返回，已跳过",
                     "常见原因：剪贴板被其他程序占用、输入法/安全软件阻塞模拟输入。"
                     "先用 --safe 模式确认其它项正常")
    return box.get("check") or _fail("internal", label, "检查没有返回结果")


def run_all(config: Config | None = None, safe: bool = True,
            include_protocol: bool = True) -> list[Check]:
    cfg = config or load_config()
    checks: list[Check] = []
    steps: list[tuple[str, Callable[[], Check], float]] = [
        ("Python 版本", check_python, 10),
        ("操作系统", check_os, 10),
        ("依赖包", check_dependencies, 30),
        ("DPI 感知", check_dpi, 15),
        ("显示器枚举", check_monitors, 20),
        ("截屏", check_capture, 30),
        ("输入后端", check_input_backend, 30),
        ("鼠标移动", lambda: check_mouse_move(cfg, safe), 20),
        ("键盘事件", lambda: check_keyboard(cfg, safe), 20),
        ("剪贴板", lambda: check_clipboard(safe), 20),
        ("窗口 API", check_windows_api, 20),
        ("UI Automation", check_uia, 20),
        ("运行环境", check_permissions, 15),
        ("安全策略", lambda: check_safety(cfg, cfg_status(cfg)), 10),
        ("审计日志", lambda: check_audit(cfg), 15),
        ("编码环境", check_locale, 10),
        ("数据目录", check_disk, 15),
        ("MCP SDK", check_mcp_sdk, 20),
        ("MCP 工具注册", lambda: check_mcp_tools(cfg), 30),
    ]
    for label, step, timeout in steps:
        checks.append(_guarded(step, timeout, label))
    if include_protocol:
        checks.append(_guarded(lambda: check_mcp_protocol(cfg), 75, "MCP 协议握手"))
    return checks


def cfg_status(cfg: Config) -> dict[str, Any]:
    return {
        "read_only": cfg.read_only,
        "allow_input": cfg.allow_input,
        "allow_shell": cfg.allow_shell,
        "allow_launch": cfg.allow_launch,
        "audit_log": cfg.audit_log,
    }


def summarize(checks: Iterable[Check]) -> dict[str, int]:
    counts = {"pass": 0, "warn": 0, "fail": 0, "skip": 0}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    counts["total"] = sum(counts[k] for k in ("pass", "warn", "fail", "skip"))
    return counts


def format_text(checks: list[Check], counts: dict[str, int]) -> str:
    import unicodedata

    def width(text: str) -> int:
        return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)

    def pad(text: str, target: int) -> str:
        return text + " " * max(0, target - width(text))

    lines = [
        "",
        "=" * 78,
        f" Win Desktop Control 兼容性自检  v{__version__}",
        f" 平台 {platform.system()} {platform.release()} · Python {platform.python_version()}"
        f" · {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 78,
    ]
    for check in checks:
        lines.append(f"[{STATUS_LABEL[check.status]}] {pad(check.title, 20)} {check.detail}")
        if check.hint and check.status in {"fail", "warn"}:
            lines.append(f"       └─ 建议：{check.hint}")
    lines.append("-" * 78)
    lines.append(
        f" 合计 {counts['total']} 项：通过 {counts['pass']} · 警告 {counts['warn']} · "
        f"失败 {counts['fail']} · 跳过 {counts['skip']}"
    )
    if counts["fail"]:
        lines.append(" 结论：存在失败项，请按上面的建议修复后重新自检。")
    elif counts["warn"]:
        lines.append(" 结论：核心能力可用，警告项不影响基本使用，建议按提示优化。")
    else:
        lines.append(" 结论：全部通过，可以直接在 MCP 客户端中使用。")
    lines.append("=" * 78)
    lines.append("")
    return "\n".join(lines)


def format_markdown(checks: list[Check], counts: dict[str, int]) -> str:
    rows = ["| 状态 | 检查项 | 结果 | 建议 |", "| --- | --- | --- | --- |"]
    badge = {"pass": "通过", "warn": "警告", "fail": "失败", "skip": "跳过"}
    for check in checks:
        title = check.title.replace("|", "\\|")
        detail = check.detail.replace("|", "\\|")
        hint = (check.hint or "").replace("|", "\\|")
        rows.append(f"| {badge[check.status]} | {title} | {detail} | {hint} |")
    header = [
        "# 兼容性自检报告",
        "",
        f"- 版本：v{__version__}",
        f"- 平台：{platform.system()} {platform.release()} / Python {platform.python_version()}",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 结果：通过 {counts['pass']} · 警告 {counts['warn']} · 失败 {counts['fail']} · 跳过 {counts['skip']}",
        "",
    ]
    return "\n".join(header + rows) + "\n"


def write_reports(checks: list[Check], counts: dict[str, int],
                  json_path: str | None, markdown_path: str | None) -> None:
    if json_path:
        payload = {
            "version": __version__,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "python": platform.python_version(),
                "machine": platform.machine(),
            },
            "counts": counts,
            "checks": [c.to_dict() for c in checks],
        }
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    if markdown_path:
        with open(markdown_path, "w", encoding="utf-8") as fh:
            fh.write(format_markdown(checks, counts))
