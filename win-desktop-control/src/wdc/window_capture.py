"""Render a single window into memory with a bounded, disposable worker."""

from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import subprocess
import struct
import sys

from .capture import CaptureError, Image
from .desktop_targets import target_window
from .native_overlay import BITMAPINFO, BITMAPINFOHEADER
from .windows import _window_rect, user32

# Render the full DWM surface when available, including DPI-scaled content.
PW_RENDERFULLCONTENT = 2


def grab(handle, pid, bounds):
    if Image is None:
        raise CaptureError("Pillow 未安装，无法处理截图。")
    width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    if width <= 0 or height <= 0 or width * height > 16_000_000:
        raise CaptureError("窗口截图尺寸不受支持。")
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-m", "wdc.window_capture", str(handle), str(pid), *map(str, bounds)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW, timeout=3, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CaptureError("窗口截图后端不可用或已超时。") from exc
    if result.returncode or len(result.stdout) < 8:
        raise CaptureError("目标窗口未提供可用截图，或已禁止截图。")
    native_width, native_height = struct.unpack_from("<II", result.stdout)
    if not native_width or not native_height or len(result.stdout) != 8 + native_width * native_height * 4:
        raise CaptureError("窗口截图返回的尺寸无效。")
    if target_window(handle, pid, foreground=False)[2] != bounds:
        raise CaptureError("窗口截图期间目标位置已改变。")
    image = Image.frombytes("RGB", (native_width, native_height), result.stdout[8:], "raw", "BGRX")
    if not any(low != high for low, high in image.getextrema()):
        raise CaptureError("窗口截图只返回了单色画面。")
    return image if image.size == (width, height) else image.resize((width, height), Image.Resampling.LANCZOS)


def _render(handle, pid, bounds):
    if target_window(handle, pid, foreground=False)[2] != bounds:
        raise CaptureError("窗口截图期间目标位置已改变。")
    gdi = C.windll.gdi32
    for library, name, restype, argtypes in (
        (user32, "GetWindowDisplayAffinity", W.BOOL, [W.HWND, C.POINTER(W.DWORD)]),
        (user32, "GetWindowDpiAwarenessContext", W.HANDLE, [W.HWND]),
        (user32, "SetThreadDpiAwarenessContext", W.HANDLE, [W.HANDLE]),
        (user32, "PrintWindow", W.BOOL, [W.HWND, W.HDC, W.UINT]),
        (gdi, "CreateCompatibleDC", W.HDC, [W.HDC]),
        (gdi, "CreateDIBSection", W.HBITMAP, [W.HDC, C.POINTER(BITMAPINFO), W.UINT, C.POINTER(C.c_void_p), W.HANDLE, W.DWORD]),
        (gdi, "SelectObject", W.HANDLE, [W.HDC, W.HANDLE]),
        (gdi, "DeleteObject", W.BOOL, [W.HANDLE]),
        (gdi, "DeleteDC", W.BOOL, [W.HDC]),
        (gdi, "GdiFlush", W.BOOL, []),
    ):
        function = getattr(library, name)
        function.restype, function.argtypes = restype, argtypes

    affinity = W.DWORD()
    if not user32.GetWindowDisplayAffinity(handle, C.byref(affinity)) or affinity.value:
        raise CaptureError("无法确认目标允许截图。")
    context = user32.GetWindowDpiAwarenessContext(handle)
    original_context = user32.SetThreadDpiAwarenessContext(context) if context else None
    dc = bitmap = previous = None
    try:
        left, top, right, bottom = _window_rect(handle)
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0 or width * height > 16_000_000:
            raise CaptureError("窗口截图尺寸不受支持。")
        dc = gdi.CreateCompatibleDC(None)
        if not dc:
            raise CaptureError("无法创建窗口截图缓冲区。")
        info = BITMAPINFO()
        info.bmiHeader = BITMAPINFOHEADER(C.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = C.c_void_p()
        bitmap = gdi.CreateDIBSection(dc, C.byref(info), 0, C.byref(bits), None, 0)
        if not bitmap or not bits.value:
            raise CaptureError("无法创建窗口截图位图。")
        previous = gdi.SelectObject(dc, bitmap)
        if not previous or not user32.PrintWindow(handle, dc, PW_RENDERFULLCONTENT):
            raise CaptureError("目标窗口未提供可用截图。")
        gdi.GdiFlush()
        if not user32.GetWindowDisplayAffinity(handle, C.byref(affinity)) or affinity.value:
            raise CaptureError("目标窗口截图保护状态已改变。")
        data = struct.pack("<II", width, height) + C.string_at(bits, width * height * 4)
    finally:
        if previous:
            gdi.SelectObject(dc, previous)
        if bitmap:
            gdi.DeleteObject(bitmap)
        if dc:
            gdi.DeleteDC(dc)
        if original_context:
            user32.SetThreadDpiAwarenessContext(original_context)
    if target_window(handle, pid, foreground=False)[2] != bounds:
        raise CaptureError("窗口截图期间目标位置已改变。")
    return data


if __name__ == "__main__":
    handle, pid, *coords = map(int, sys.argv[1:])
    sys.stdout.buffer.write(_render(handle, pid, tuple(coords)))
