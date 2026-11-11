"""窗口管理：枚举、查找、置前、最大化/最小化/还原/关闭/移动缩放。

全部基于 ctypes 直接调用 user32 / dwmapi / kernel32，不依赖 pywin32，
避免额外二进制依赖导致安装失败。非 Windows 平台返回明确的 unsupported 结果。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import math
import time
from dataclasses import asdict, dataclass
from typing import Any


IS_WINDOWS = sys.platform == "win32"

user32 = ctypes.windll.user32 if IS_WINDOWS else None
kernel32 = ctypes.windll.kernel32 if IS_WINDOWS else None
dwmapi = ctypes.windll.dwmapi if IS_WINDOWS else None

# Pointer-sized bindings prevent 64-bit HWND / process handle truncation.
if IS_WINDOWS:
    for library,name,result,args in [
        (user32,'GetForegroundWindow',wt.HWND,[]),
        (user32,'IsWindow',wt.BOOL,[wt.HWND]),
        (user32,'GetWindowThreadProcessId',wt.DWORD,[wt.HWND,ctypes.POINTER(wt.DWORD)]),
        (user32,'GetWindowTextLengthW',ctypes.c_int,[wt.HWND]),
        (user32,'GetWindowTextW',ctypes.c_int,[wt.HWND,wt.LPWSTR,ctypes.c_int]),
        (user32,'GetClassNameW',ctypes.c_int,[wt.HWND,wt.LPWSTR,ctypes.c_int]),
        (user32,'GetWindowLongW',wt.LONG,[wt.HWND,ctypes.c_int]),
        (user32,'GetWindowRect',wt.BOOL,[wt.HWND,ctypes.POINTER(wt.RECT)]),
        (user32,'IsWindowVisible',wt.BOOL,[wt.HWND]),
        (user32,'IsIconic',wt.BOOL,[wt.HWND]),
        (user32,'IsZoomed',wt.BOOL,[wt.HWND]),
        (user32,'ShowWindow',wt.BOOL,[wt.HWND,ctypes.c_int]),
        (user32,'SetWindowPos',wt.BOOL,[wt.HWND,wt.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wt.UINT]),
        (user32,'PostMessageW',wt.BOOL,[wt.HWND,wt.UINT,wt.WPARAM,wt.LPARAM]),
        (user32,'SetForegroundWindow',wt.BOOL,[wt.HWND]),
        (user32,'BringWindowToTop',wt.BOOL,[wt.HWND]),
        (user32,'SetFocus',wt.HWND,[wt.HWND]),
        (kernel32,'OpenProcess',wt.HANDLE,[wt.DWORD,wt.BOOL,wt.DWORD]),
        (kernel32,'QueryFullProcessImageNameW',wt.BOOL,[wt.HANDLE,wt.DWORD,wt.LPWSTR,ctypes.POINTER(wt.DWORD)]),
        (kernel32,'CloseHandle',wt.BOOL,[wt.HANDLE]),
        (dwmapi,'DwmGetWindowAttribute',ctypes.c_long,[wt.HWND,wt.DWORD,ctypes.c_void_p,wt.DWORD]),
    ]:
        fn=getattr(library,name);fn.restype=result;fn.argtypes=args


class WindowError(ValueError):
    def __init__(self,message,code='window_not_found'):
        super().__init__(message);self.code=code


@dataclass
class WindowInfo:
    handle: int
    title: str
    class_name: str = ""
    pid: int = 0
    process: str = ""
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)  # left, top, right, bottom
    visible: bool = True
    minimized: bool = False
    maximized: bool = False
    foreground: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        left, top, right, bottom = self.rect
        d["rect"] = {"left": left, "top": top, "right": right, "bottom": bottom,
                     "width": max(0, right - left), "height": max(0, bottom - top)}
        d["handle_hex"] = hex(self.handle)
        return d


# --------------------------------------------------------------------------- #
# 底层工具
# --------------------------------------------------------------------------- #
def _process_name(pid: int) -> str:
    if not IS_WINDOWS or not pid:
        return ""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wt.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return os.path.basename(buffer.value)
        return ""
    except Exception:
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def _title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _rect(hwnd: int) -> tuple[int, int, int, int]:
    """真实可见边界：优先 DWM 扩展帧（去掉 Win10+ 的透明阴影边），失败回退 GetWindowRect。"""
    rect = wt.RECT()
    try:
        DWMWA_EXTENDED_FRAME_BOUNDS = 9
        hr = dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)
        )
        if hr == 0 and rect.right > rect.left and rect.bottom > rect.top:
            return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        pass
    return _window_rect(hwnd)


def _window_rect(hwnd):
    rect=wt.RECT()
    if not user32.GetWindowRect(hwnd,ctypes.byref(rect)):
        raise WindowError('无法读取窗口边界。','window_unavailable')
    return rect.left,rect.top,rect.right,rect.bottom


def _pid(hwnd):
    pid=wt.DWORD()
    if not user32.GetWindowThreadProcessId(hwnd,ctypes.byref(pid)) or not pid.value:
        raise WindowError('无法读取窗口 PID。','window_unavailable')
    return int(pid.value)


def _ensure_identity(hwnd,pid):
    if not user32.IsWindow(hwnd) or _pid(hwnd)!=pid:
        raise WindowError('窗口身份已变化，请重新列举窗口。','window_identity_changed')


def _timeout(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=5:
        raise WindowError('窗口验证 timeout 必须在 0~5 秒。','bad_argument')
    return float(value)


def _info(hwnd: int, foreground: int | None = None) -> WindowInfo:
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    style = user32.GetWindowLongW(hwnd, -16)  # GWL_STYLE
    WS_MINIMIZE = 0x20000000
    WS_MAXIMIZE = 0x01000000
    return WindowInfo(
        handle=int(hwnd),
        title=_title(hwnd),
        class_name=_class_name(hwnd),
        pid=int(pid.value),
        process=_process_name(int(pid.value)),
        rect=_rect(hwnd),
        visible=bool(user32.IsWindowVisible(hwnd)),
        minimized=bool(style & WS_MINIMIZE),
        maximized=bool(style & WS_MAXIMIZE),
        foreground=(hwnd == foreground),
    )


def _is_tool_window(hwnd: int) -> bool:
    ex = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
    WS_EX_TOOLWINDOW = 0x00000080
    return bool(ex & WS_EX_TOOLWINDOW)


# --------------------------------------------------------------------------- #
# 公开 API
# --------------------------------------------------------------------------- #
def list_windows(include_hidden: bool = False, limit: int = 80) -> list[dict[str, Any]]:
    if not IS_WINDOWS:
        return []
    foreground = user32.GetForegroundWindow()
    results: list[WindowInfo] = []

    CALLBACK = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def _callback(hwnd, _lparam):
        try:
            if not include_hidden and not user32.IsWindowVisible(hwnd):
                return True
            if not user32.GetWindowTextLengthW(hwnd):
                return True
            if _is_tool_window(hwnd):
                return True
            results.append(_info(hwnd, foreground))
        except Exception:
            pass
        return True

    user32.EnumWindows(CALLBACK(_callback), 0)
    # 前台窗口排最前，其余按标题长度/进程名排序更稳定
    results.sort(key=lambda w: (not w.foreground, w.minimized, w.title.lower()))
    return [w.to_dict() for w in results[: max(1, int(limit))]]


def get_foreground_window() -> dict[str, Any] | None:
    if not IS_WINDOWS:
        return None
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    return _info(hwnd, hwnd).to_dict()


def _match(title: str | None, handle: int | None, exact: bool = False) -> int | None:
    if handle is not None:
        if isinstance(handle,bool) or not isinstance(handle,int) or handle<=0:
            raise WindowError('handle 必须是正整数。','bad_argument')
        hwnd = int(handle)
        return hwnd if user32.IsWindow(hwnd) else None
    if not isinstance(title,str) or not title.strip():
        raise WindowError('请提供非空标题或窗口句柄。','bad_argument')
    needle = title.strip().lower()
    candidates = list_windows(include_hidden=True, limit=500)
    if len(candidates)>=500:raise WindowError('窗口列表不完整，请使用明确句柄。','window_lookup_incomplete')
    # At the best matching rank, require exactly one candidate.
    matchers=(
        lambda w: w["title"].lower() == needle,
        lambda w: w["title"].lower().startswith(needle),
        lambda w: needle in w["title"].lower(),
        lambda w: needle in w["process"].lower(),
    )
    for matcher in matchers[:1] if exact else matchers:
        matches=[window for window in candidates if matcher(window)]
        if len(matches)>1:raise WindowError('存在多个匹配窗口，请 list_windows 后指定 handle 和 expected_pid。','ambiguous_window')
        if matches:return int(matches[0]['handle'])
    return None


def find_window(title: str | None = None, handle: int | None = None) -> dict[str, Any] | None:
    if not IS_WINDOWS:
        return None
    hwnd = _match(title, handle)
    if hwnd is None:
        return None
    return _info(hwnd, user32.GetForegroundWindow()).to_dict()


def _force_foreground(hwnd: int) -> bool:
    """把窗口拉到前台。Windows 有前台锁定策略，这里用 AttachThreadInput 兜底。"""
    if user32.GetForegroundWindow() == hwnd:
        return True
    SW_RESTORE, SW_SHOW = 9, 5
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    else:
        user32.ShowWindow(hwnd, SW_SHOW)

    if not user32.SetForegroundWindow(hwnd):
        # 模拟一次 ALT 按键，解除前台锁定
        VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        user32.SetForegroundWindow(hwnd)

    if user32.GetForegroundWindow() == hwnd:
        return True

    # 线程输入队列挂接：把当前线程挂到前台线程上再抢焦点
    try:
        fg = user32.GetForegroundWindow()
        target_tid = user32.GetWindowThreadProcessId(hwnd, None)
        fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        cur_tid = kernel32.GetCurrentThreadId()
        attached: list[int] = []
        for tid in {fg_tid, target_tid}:
            if tid and tid != cur_tid and user32.AttachThreadInput(cur_tid, tid, True):
                attached.append(tid)
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
        finally:
            for tid in attached:
                user32.AttachThreadInput(cur_tid, tid, False)
    except Exception:
        pass
    return user32.GetForegroundWindow() == hwnd


def _target(title,handle,expected_pid):
    if expected_pid is not None and (isinstance(expected_pid,bool) or not isinstance(expected_pid,int) or expected_pid<=0):
        raise WindowError('expected_pid 必须是正整数。','bad_argument')
    hwnd=_match(title,handle)
    if hwnd is None:raise WindowError('未找到目标窗口。')
    pid=_pid(hwnd)
    if expected_pid is not None and expected_pid!=pid:
        raise WindowError('句柄对应的 PID 已变化。','window_identity_changed')
    return hwnd,pid


def _verify(hwnd,pid,predicate,timeout,closed=False):
    from . import input_ctl
    deadline=time.monotonic()+timeout
    while True:
        input_ctl.check_cancelled()
        exists=bool(user32.IsWindow(hwnd))
        same=exists and _pid(hwnd)==pid
        if closed and not same:return True
        if not same:raise WindowError('复核时窗口身份已变化。','window_identity_changed')
        if not closed and predicate():return True
        if time.monotonic()>=deadline:return False
        input_ctl.cancellable_sleep(min(.05,max(0,deadline-time.monotonic())))


def _outcome(hwnd,pid,action,verified,closed=False):
    data={'ok':verified,'action':action,'target':{'handle':hwnd,'pid':pid},'sent':True,
          'verification':{'status':'verified' if verified else 'failed','method':'window_state_readback'}}
    if not verified:data.update(error='操作已发出，但窗口状态未在时限内满足预期；请重新观察，不要盲目重放。',error_code='verification_failed')
    if not (closed and verified):data['window']=_info(hwnd,user32.GetForegroundWindow()).to_dict()
    return data


def activate_window(title: str | None = None, handle: int | None = None,
                    expected_pid: int | None = None, timeout: float = 2.0) -> dict[str, Any]:
    if not IS_WINDOWS:
        return {"ok": False, "error": "窗口控制仅支持 Windows"}
    from . import input_ctl
    timeout=_timeout(timeout);hwnd,pid=_target(title,handle,expected_pid)
    input_ctl.check_cancelled();_ensure_identity(hwnd,pid)
    _force_foreground(hwnd)
    verified=_verify(hwnd,pid,lambda:user32.GetForegroundWindow()==hwnd,timeout)
    return _outcome(hwnd,pid,'activate_window',verified)


def window_action(
    action: str,
    title: str | None = None,
    handle: int | None = None,
    x: int | None = None,
    y: int | None = None,
    width: int | None = None,
    height: int | None = None,
    timeout: float = 2.0,
    expected_pid: int | None = None,
) -> dict[str, Any]:
    if not IS_WINDOWS:
        return {"ok": False, "error": "窗口控制仅支持 Windows"}
    from . import input_ctl
    timeout=_timeout(timeout);hwnd,pid=_target(title,handle,expected_pid)
    input_ctl.check_cancelled();_ensure_identity(hwnd,pid)
    action = (action or "").strip().lower()
    SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE = 3, 6, 9
    WM_CLOSE = 0x0010
    HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_SHOWWINDOW = 0x0001, 0x0002, 0x0004, 0x0040

    if action in {"maximize", "max"}:
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
        predicate=lambda:bool(user32.IsWindowVisible(hwnd) and user32.IsZoomed(hwnd))
    elif action in {"minimize", "min"}:
        user32.ShowWindow(hwnd, SW_MINIMIZE)
        predicate=lambda:bool(user32.IsIconic(hwnd))
    elif action in {"restore", "normal", "unminimize"}:
        user32.ShowWindow(hwnd, SW_RESTORE)
        predicate=lambda:bool(user32.IsWindowVisible(hwnd)) and not user32.IsIconic(hwnd) and not user32.IsZoomed(hwnd)
    elif action in {"close", "quit"}:
        if not user32.PostMessageW(hwnd, WM_CLOSE, 0, 0):
            raise WindowError('关闭消息发送失败。','window_request_failed')
        return _outcome(hwnd,pid,action,_verify(hwnd,pid,None,timeout,closed=True),closed=True)
    elif action in {"focus", "activate", "foreground"}:
        _force_foreground(hwnd)
        predicate=lambda:user32.GetForegroundWindow()==hwnd
    elif action in {"topmost", "always_on_top"}:
        if not user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | 0x10):
            raise WindowError('置顶请求失败。','window_request_failed')
        predicate=lambda:bool(user32.GetWindowLongW(hwnd,-20)&8)
    elif action in {"untopmost", "normal_z"}:
        if not user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | 0x10):
            raise WindowError('取消置顶请求失败。','window_request_failed')
        predicate=lambda:not user32.GetWindowLongW(hwnd,-20)&8
    elif action in {"move", "resize", "move_resize"}:
        left, top, right, bottom = _window_rect(hwnd)
        cur_w, cur_h = max(1, right - left), max(1, bottom - top)
        new_x = left if x is None else int(x)
        new_y = top if y is None else int(y)
        new_w = cur_w if width is None else max(1, int(width))
        new_h = cur_h if height is None else max(1, int(height))
        if user32.IsZoomed(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        flags = SWP_NOZORDER | SWP_SHOWWINDOW | 0x10
        _ensure_identity(hwnd,pid);input_ctl.check_cancelled()
        if not user32.SetWindowPos(hwnd, 0, new_x, new_y, new_w, new_h, flags):
            raise WindowError('移动/缩放请求失败。','window_request_failed')
        expected=(new_x,new_y,new_x+new_w,new_y+new_h)
        predicate=lambda:all(abs(actual-wanted)<=1 for actual,wanted in zip(_window_rect(hwnd),expected))
    else:
        return {
            "ok": False,
            "error": f"不支持的 action：{action}",
            "supported": ["maximize", "minimize", "restore", "close", "focus",
                          "topmost", "untopmost", "move", "resize"],
        }

    return _outcome(hwnd,pid,action,_verify(hwnd,pid,predicate,timeout))


def launch_app(path: str, args: str = "", cwd: str | None = None,
               wait: float = 0.8) -> dict[str, Any]:
    """启动一个程序或打开文档/URL。

    使用 subprocess 列表参数（不经 shell），避免命令注入与空格路径解析问题；
    URL / 已注册文件类型交给系统 Shell。
    """
    import shlex
    import subprocess
    import time

    path = (path or "").strip()
    if not path:
        return {"ok": False, "error": "path 不能为空"}

    try:
        if path.lower().startswith(("http://", "https://", "mailto:")):
            _open_with_shell(path)
            proc = None
        elif args:
            # posix=False 保留 Windows 反斜杠，避免 shlex 把 C:\ 当转义
            argv = [path] + shlex.split(args, posix=False)
            proc = subprocess.Popen(argv, cwd=cwd)
        elif os.path.exists(path):
            proc = subprocess.Popen([path], cwd=cwd)
        else:
            # 交给 Shell 解析（如 notepad、calc、explorer、.docx 文件关联）
            _open_with_shell(path)
            proc = None
    except Exception as exc:
        return {"ok": False, "error": f"启动失败：{exc}"}

    if wait and wait > 0:
        time.sleep(min(float(wait), 10.0))
    return {
        "ok": True,
        "path": path,
        "pid": getattr(proc, "pid", None),
        "foreground": get_foreground_window(),
    }


def _open_with_shell(target: str) -> None:
    """用系统默认方式打开（Windows: os.startfile；Linux: xdg-open；macOS: open）。"""
    if IS_WINDOWS:
        os.startfile(target)  # type: ignore[attr-defined]
        return
    import subprocess

    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
