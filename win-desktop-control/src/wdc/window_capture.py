"""Render a single window into memory using bounded, reusable capture workers."""

from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import struct
from pathlib import Path

from .capture import CaptureError, Image
from .desktop_targets import target_window
from .native_overlay import BITMAPINFO, BITMAPINFOHEADER
from .read_worker import ReadWorker, ReadWorkerError, serve
from .safety import SafetyViolation
from .platform_win import setup_dpi_awareness
from .windows import _window_rect, user32

# Render the full DWM surface when available, including DPI-scaled content.
PW_RENDERFULLCONTENT = 2
MAX_PIXELS = 16_000_000
_print_worker = ReadWorker("wdc.window_capture",8+MAX_PIXELS*4)
_wgc_path = Path(__file__).with_name("wgc_capture.exe")
_wgc_worker = ReadWorker(None,8+MAX_PIXELS*4,[str(_wgc_path)],parent_argument=True)


def _visible_pixels(data, width, height, *, reject_uniform=False):
    if len(data) != 8+width*height*4 or struct.unpack_from("<II",data) != (width,height):
        raise CaptureError("窗口截图返回的尺寸无效。")
    image = Image.frombytes("RGB",(width,height),memoryview(data)[8:],"raw","BGRX")
    if reject_uniform and not any(low != high for low,high in image.getextrema()):
        raise CaptureError("窗口截图只返回了单色画面。")
    return image


def _eligible(handle,pid,bounds):
    if target_window(handle,pid,foreground=False)[2] != bounds:
        raise CaptureError("窗口截图期间目标位置已改变。")
    function = user32.GetWindowDisplayAffinity
    function.restype,function.argtypes = W.BOOL,[W.HWND,C.POINTER(W.DWORD)]
    affinity = W.DWORD()
    if not function(handle,C.byref(affinity)) or affinity.value:
        raise CaptureError("无法确认目标允许截图。")


def grab(handle, pid, bounds, region=None, cancel=None):
    if Image is None:
        raise CaptureError("Pillow 未安装，无法处理截图。")
    left,top,right,bottom = bounds
    x,y,width,height = region or (left,top,right-left,bottom-top)
    if (width <= 0 or height <= 0 or (right-left)*(bottom-top) > MAX_PIXELS or
            x < left or y < top or x+width > right or y+height > bottom):
        raise CaptureError("窗口截图尺寸不受支持。")
    crop = x,y,x+width,y+height

    def check():
        if cancel is not None and cancel.is_set():
            raise SafetyViolation("截图已取消。","operation_cancelled")

    check()
    _eligible(handle,pid,bounds)
    try:
        packet = _print_worker.call([handle,pid,bounds,crop],1.8,check)
        if packet == b"denied":
            raise SafetyViolation("目标窗口身份或截图保护状态已改变。","capture_context_changed")
        image = _visible_pixels(packet,width,height)
        backend = "print_window"
    except (CaptureError,OSError,ReadWorkerError) as exc:
        if not _wgc_path.is_file():
            raise CaptureError("PrintWindow 未提供有效截图，当前安装包未包含 WGC。") from exc
        _eligible(handle,pid,bounds)
        try:
            packet = _wgc_worker.call(" ".join(map(str,(handle,pid,*bounds,*crop))),1.6,check)
            image = _visible_pixels(packet,width,height,reject_uniform=True)
        except (CaptureError,OSError,ReadWorkerError) as fallback:
            raise CaptureError("目标窗口未提供可验证的后台截图。") from fallback
        backend = "windows_graphics_capture"
    check()
    _eligible(handle,pid,bounds)
    image.info["capture_backend"] = backend
    return image


def _render(handle, pid, bounds, crop):
    setup_dpi_awareness()
    try:
        _eligible(handle,pid,bounds)
    except (ValueError,CaptureError,SafetyViolation) as exc:
        raise SafetyViolation("目标窗口截图资格已变化。","capture_context_changed") from exc
    gdi = C.windll.gdi32
    for library, name, restype, argtypes in (
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

    context = user32.GetWindowDpiAwarenessContext(handle)
    original_context = user32.SetThreadDpiAwarenessContext(context) if context else None
    dc = bitmap = previous = None
    try:
        left, top, right, bottom = _window_rect(handle)
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
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
            if original_context:
                user32.SetThreadDpiAwarenessContext(original_context)
                original_context = None
            try:
                _eligible(handle,pid,bounds)
            except (ValueError,CaptureError,SafetyViolation) as exc:
                raise SafetyViolation("目标窗口截图资格已变化。","capture_context_changed") from exc
            raise CaptureError("目标窗口未提供可用截图。")
        gdi.GdiFlush()
        native = Image.frombytes("RGB",(width,height),C.string_at(bits,width*height*4),"raw","BGRX")
    finally:
        if previous:
            gdi.SelectObject(dc, previous)
        if bitmap:
            gdi.DeleteObject(bitmap)
        if dc:
            gdi.DeleteDC(dc)
        if original_context:
            user32.SetThreadDpiAwarenessContext(original_context)
    try:
        _eligible(handle,pid,bounds)
    except (ValueError,CaptureError,SafetyViolation) as exc:
        raise SafetyViolation("目标窗口截图资格已变化。","capture_context_changed") from exc
    left,top,right,bottom = bounds
    x,y,x2,y2 = crop
    if not any(low != high for low,high in native.getextrema()):
        raise CaptureError("窗口渲染只返回了单色画面。")
    physical = (right-left,bottom-top)
    if native.size != physical:
        native = native.resize(physical,Image.Resampling.LANCZOS)
    output = native.crop((x-left,y-top,x2-left,y2-top))
    return struct.pack("<II",*output.size)+output.tobytes("raw","BGRX")


def _serve_request(request):
    try:
        handle,pid,bounds,crop = request
        return _render(handle,pid,tuple(bounds),tuple(crop))
    except SafetyViolation:
        return b"denied"
    except Exception:
        return b""


if __name__ == "__main__":
    serve(_serve_request)
