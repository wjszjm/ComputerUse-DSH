"""平台适配层：只放 ctypes / 系统信息相关代码，全部做异常兜底。

非 Windows 平台调用时返回中性的默认值，保证模块可被导入与测试。
"""

from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import asdict, dataclass
from typing import Any

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

_dpi_state: str | None = None


# --------------------------------------------------------------------------- #
# DPI
# --------------------------------------------------------------------------- #
def setup_dpi_awareness() -> str:
    """开启进程 DPI 感知，使截图坐标 == 鼠标坐标 == 物理像素。

    必须在 pyautogui / 截图后端第一次获取屏幕尺寸之前调用，否则其内部缓存的
    尺寸会停留在 96 DPI 的逻辑像素上，导致点击位置偏移（高缩放笔记本的经典坑）。

    返回值：实际生效的模式名，便于写入自检报告。
    """
    global _dpi_state
    if _dpi_state is not None:
        return _dpi_state
    if not IS_WINDOWS:
        _dpi_state = "not-windows"
        return _dpi_state

    user32 = ctypes.windll.user32
    try:  # Windows 10 1703+：PER_MONITOR_AWARE_V2
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            _dpi_state = "per-monitor-v2"
            return _dpi_state
    except Exception:
        pass
    try:  # Windows 8.1+
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            _dpi_state = "per-monitor"
            return _dpi_state
    except Exception:
        pass
    try:  # Vista+
        if user32.SetProcessDPIAware():
            _dpi_state = "system"
            return _dpi_state
    except Exception:
        pass
    _dpi_state = "none"
    return _dpi_state


def get_dpi_scale() -> float:
    """主屏 DPI 缩放比（1.0 = 100%，1.5 = 150%）。"""
    if not IS_WINDOWS:
        return 1.0
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        if not hdc:
            return 1.0
        try:
            dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        finally:
            ctypes.windll.user32.ReleaseDC(0, hdc)
        return round((dpi or 96) / 96.0, 3)
    except Exception:
        return 1.0


def monitor_dpi_scale(x: int, y: int) -> float | None:
    if not IS_WINDOWS:
        return None
    try:
        from ctypes import wintypes
        nearest = ctypes.windll.user32.MonitorFromPoint
        nearest.argtypes = [wintypes.POINT,wintypes.DWORD]
        nearest.restype = wintypes.HANDLE
        monitor = nearest(wintypes.POINT(x,y),2)
        dpi_x, dpi_y = ctypes.c_uint(), ctypes.c_uint()
        get_dpi = ctypes.windll.shcore.GetDpiForMonitor
        get_dpi.argtypes = [wintypes.HANDLE,ctypes.c_int,ctypes.POINTER(ctypes.c_uint),ctypes.POINTER(ctypes.c_uint)]
        if monitor and get_dpi(monitor,0,ctypes.byref(dpi_x),ctypes.byref(dpi_y)) == 0 and dpi_x.value:
            return round(dpi_x.value/96,3)
    except (AttributeError,OSError):
        pass
    return None


def enumerate_displays() -> list[dict]:
    if not IS_WINDOWS:
        return []
    from ctypes import wintypes
    user32=ctypes.windll.user32
    displays=[]
    callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HANDLE,wintypes.HDC,
                                    ctypes.POINTER(wintypes.RECT),wintypes.LPARAM)

    @callback_type
    def collect(_monitor, _dc, rect, _param):
        bounds=rect.contents
        displays.append({"left":bounds.left,"top":bounds.top,
                         "width":bounds.right-bounds.left,"height":bounds.bottom-bounds.top})
        return True

    if not user32.EnumDisplayMonitors(None,None,collect,0) or not displays:
        return []
    virtual=get_virtual_screen()
    return [{"left":virtual.left,"top":virtual.top,"width":virtual.width,"height":virtual.height},*displays]


# --------------------------------------------------------------------------- #
# 屏幕 / 会话
# --------------------------------------------------------------------------- #
@dataclass
class VirtualScreen:
    left: int = 0
    top: int = 0
    width: int = 0
    height: int = 0

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


def get_virtual_screen() -> VirtualScreen:
    """整个虚拟桌面（跨多显示器）的包围盒。"""
    if not IS_WINDOWS:
        try:
            import tkinter

            root = tkinter.Tk()
            root.withdraw()
            w, h = root.winfo_screenwidth(), root.winfo_screenheight()
            root.destroy()
            return VirtualScreen(0, 0, w, h)
        except Exception:
            return VirtualScreen(0, 0, 1920, 1080)
    try:
        m = ctypes.windll.user32.GetSystemMetrics
        return VirtualScreen(
            left=m(76), top=m(77), width=m(78), height=m(79)  # SM_X/Y/CX/CYVIRTUALSCREEN
        )
    except Exception:
        return VirtualScreen(0, 0, 1920, 1080)


def is_secure_desktop_active() -> bool:
    """锁屏 / UAC 提权弹窗 / Ctrl+Alt+Del 界面是否占据输入桌面。

    这些状态下截图会失败或全黑、模拟输入也无效，需要提前告知模型而不是静默失败。
    """
    if not IS_WINDOWS:
        return False
    try:
        user32 = ctypes.windll.user32
        hdesk = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
        if not hdesk:
            return True
        user32.CloseDesktop(hdesk)
        return False
    except Exception:
        return False


def is_elevated() -> bool:
    """当前进程是否以管理员身份运行。"""
    if not IS_WINDOWS:
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def is_remote_session() -> bool:
    """是否为 RDP 远程会话（远程会话下部分截屏/输入 API 行为不同）。"""
    if not IS_WINDOWS:
        return bool(os.environ.get("SSH_CONNECTION"))
    return os.environ.get("SESSIONNAME", "").upper().startswith("RDP")


def get_session_info() -> dict[str, Any]:
    import getpass

    vs = get_virtual_screen()
    try:
        user = getpass.getuser()
    except Exception:
        user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    return {
        "platform": sys.platform,
        "is_windows": IS_WINDOWS,
        "python": sys.version.split()[0],
        "dpi_awareness": _dpi_state or "not-initialized",
        "dpi_scale": get_dpi_scale(),
        "virtual_screen": asdict(vs),
        "secure_desktop": is_secure_desktop_active(),
        "elevated": is_elevated(),
        "remote_session": is_remote_session(),
        "session_name": os.environ.get("SESSIONNAME", ""),
        "user": user,
        "computer": os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "",
    }
