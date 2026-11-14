"""Bounded, window-scoped UI Automation and in-memory Windows OCR."""
from __future__ import annotations

import asyncio
import ctypes
import sys
import time
from collections import deque

from . import input_ctl, platform_win, windows
from .safety import SafetyViolation


def target_window(handle: int, expected_pid: int, *, foreground: bool = True):
    if platform_win.is_secure_desktop_active():
        raise SafetyViolation("安全桌面不允许观察或操作。", "secure_desktop")
    platform_win.setup_dpi_awareness()
    if type(handle) is not int or handle <= 0 or type(expected_pid) is not int or expected_pid <= 0:
        raise ValueError("需要有效的窗口句柄和 PID。")
    if not windows.IS_WINDOWS:
        raise windows.WindowError("窗口操作仅支持 Windows。", "window_unavailable")
    hwnd, pid = windows._target(None, handle, expected_pid)
    if not windows.user32.IsWindowVisible(hwnd) or windows.user32.IsIconic(hwnd):
        raise windows.WindowError("目标窗口需要可见且未最小化。", "window_not_visible")
    cloaked = ctypes.c_uint()
    if windows.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) == 0 and cloaked.value:
        raise windows.WindowError("目标窗口当前不可见。", "window_not_visible")
    if foreground and windows.user32.GetForegroundWindow() != hwnd:
        raise windows.WindowError("请先确认目标窗口位于前台。", "window_not_foreground")
    return hwnd, pid, windows._window_rect(hwnd)


def window_at_point(handle, x, y):
    from ctypes import wintypes
    from_point=windows.user32.WindowFromPoint
    from_point.argtypes=[wintypes.POINT]
    from_point.restype=wintypes.HWND
    ancestor=windows.user32.GetAncestor
    ancestor.argtypes=[wintypes.HWND,wintypes.UINT]
    ancestor.restype=wintypes.HWND
    child=from_point(wintypes.POINT(x,y))
    return bool(child and ancestor(child,2)==handle)


def uia_controls(handle, expected_pid, *, name="", control_type="", limit=40, select=None,
                 background=False, cancelled=None, deadline=None):
    if not 1 <= limit <= 80 or len(name) > 160 or len(control_type) > 64:
        raise ValueError("控件查询超过支持范围。")
    try:
        import uiautomation as uia
    except ImportError as exc:
        raise SafetyViolation("当前 Python 环境缺少 UI Automation 支持。", "uia_unavailable") from exc
    hwnd, pid, bounds = target_window(handle, expected_pid, foreground=not background)
    results = []
    queue = deque()
    deadline = min(deadline if deadline is not None else float("inf"),time.monotonic()+2)
    truncated = False
    with uia.UIAutomationInitializerInThread():
        queue.append((uia.ControlFromHandle(hwnd), 0))
        visited = 0
        while queue and visited < 500 and time.monotonic() < deadline:
            if cancelled is not None and cancelled():
                raise SafetyViolation("等待已取消。","operation_cancelled")
            input_ctl.check_cancelled()
            control, depth = queue.popleft()
            visited += 1
            if control is None or control.ProcessId != pid:
                continue
            if depth < 12:
                children = control.GetChildren()
                truncated |= len(children)>100
                queue.extend((child, depth + 1) for child in children[:100])
            else:
                truncated = True
            if control.IsOffscreen or control.IsPassword:
                continue
            label = control.Name or ""
            kind = control.ControlTypeName
            if (name and label.casefold() != name.casefold()) or (control_type and kind.casefold() != control_type.casefold()):
                continue
            rect = control.BoundingRectangle
            box = [rect.left, rect.top, rect.right, rect.bottom]
            if box[0] >= box[2] or box[1] >= box[3]:
                continue
            runtime_id = control.Element.GetRuntimeId()
            if runtime_id is None:
                continue
            identifier = ".".join(str(item) for item in runtime_id)
            info = {"id":identifier,"name":label[:160],"type":kind,"rect":box,"enabled":control.IsEnabled,"depth":depth}
            if select is not None:
                if info["id"] == select[0]:
                    if target_window(handle, expected_pid, foreground=not background)[2] != bounds:
                        raise windows.WindowError("控件所在窗口已变化。", "window_identity_changed")
                    focused_window = windows.user32.GetForegroundWindow() if background else None
                    outcome = invoke(control, info, *select[1:])
                    if background:
                        outcome["foreground_changed"] = windows.user32.GetForegroundWindow() != focused_window
                    return outcome
            else:
                results.append(info)
                if len(results) == limit:
                    break
    if select is not None:
        raise windows.WindowError("控件已变化或不可用，请重新读取控件树。", "control_not_found")
    if target_window(handle, expected_pid, foreground=not background)[2] != bounds:
        raise windows.WindowError("读取控件期间窗口已变化。", "window_identity_changed")
    return {"controls":results,"truncated":truncated or bool(queue)}


def text_matches(words, query, limit=None):
    """Match consecutive words on one OCR line and keep the original text."""
    needle = "".join(query.casefold().split())
    matches = []
    for index, word in enumerate(words):
        text = ""
        rectangles = []
        originals = []
        for offset in range(index, len(words)):
            part = words[offset]
            if part.get("line") != word.get("line"):
                break
            text += "".join(part["text"].casefold().split())
            if not needle.startswith(text):
                break
            rectangles.append(part["rect"])
            originals.append(part["text"])
            if text == needle:
                left=min(box[0] for box in rectangles)
                top=min(box[1] for box in rectangles)
                right=max(box[0]+box[2] for box in rectangles)
                bottom=max(box[1]+box[3] for box in rectangles)
                matches.append({"text":" ".join(originals),"rect":[left,top,right-left,bottom-top],
                                "line":word.get("line")})
                if limit is not None and len(matches) >= limit:
                    return matches
                break
    return matches


def text_match(words, query, limit=None):
    return [(x+w/2,y+h/2) for x,y,w,h in (item["rect"] for item in text_matches(words,query,limit))]


def invoke(control, info, action, text):
    if not info["enabled"]:
        raise SafetyViolation("控件已禁用。", "control_unavailable")
    input_ctl.check_cancelled()
    if action == "invoke":
        pattern = control.GetInvokePattern()
        if not pattern:
            raise SafetyViolation("控件不提供 Invoke 模式。", "pattern_unavailable")
        input_ctl.check_cancelled()
        pattern.Invoke()
        return {"sent":True,"verification":{"status":"not_verified","method":"uia_provider_return"}}
    if action == "set_value":
        if len(text) > 4096:
            raise ValueError("文本过长。")
        pattern = control.GetValuePattern()
        if not pattern or pattern.IsReadOnly:
            raise SafetyViolation("控件不提供可写的 Value 模式。", "pattern_unavailable")
        input_ctl.check_cancelled()
        pattern.SetValue(text)
        verified = pattern.Value == text
    elif action == "select":
        pattern = control.GetSelectionItemPattern()
        if not pattern:
            raise SafetyViolation("控件不提供 SelectionItem 模式。", "pattern_unavailable")
        input_ctl.check_cancelled()
        pattern.Select()
        verified = pattern.IsSelected
    elif action in {"check", "uncheck"}:
        pattern = control.GetTogglePattern()
        if not pattern:
            raise SafetyViolation("控件不提供 Toggle 模式。", "pattern_unavailable")
        desired = int(action == "check")
        state = int(pattern.ToggleState)
        if state not in (0, 1):
            raise SafetyViolation("无法安全切换不确定的复选框状态。", "control_unavailable")
        if state == desired:
            return {"sent":False,"verification":{"status":"verified","method":"uia_readback"}}
        input_ctl.check_cancelled()
        pattern.Toggle()
        verified = int(pattern.ToggleState) == desired
    else:
        raise ValueError("未知的控件操作。")
    return {"sent":True,"verification":{"status":"verified" if verified else "failed","method":"uia_readback"}}


def ocr_words(image, language, limit=160):
    if sys.platform != "win32":
        raise SafetyViolation("Windows OCR 仅支持 Windows。", "ocr_unavailable")
    if language not in {"en-US","zh-CN","zh-TW"}:
        raise ValueError("OCR 语言请选择 en-US、zh-CN 或 zh-TW。")
    try:
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import Buffer
    except ImportError as exc:
        raise SafetyViolation("当前 Python 环境缺少 Windows OCR 支持。", "ocr_unavailable") from exc
    engine = OcrEngine.try_create_from_language(Language(language))
    if engine is None:
        raise SafetyViolation("Windows 未安装所选 OCR 识别语言。", "ocr_language_unavailable")
    width, height = image.size
    if max(width, height) > OcrEngine.max_image_dimension:
        raise ValueError("截图尺寸超过 OCR 限制，请缩小范围。")
    pixels = image.convert("RGBA").tobytes("raw","BGRA")
    data = Buffer(len(pixels))
    data.length = len(pixels)
    memoryview(data)[:] = pixels
    bitmap = SoftwareBitmap.create_copy_from_buffer(
        data, BitmapPixelFormat.BGRA8, width, height, BitmapAlphaMode.IGNORE,
    )

    async def recognize():
        return await engine.recognize_async(bitmap)

    try:
        result = asyncio.run(recognize())
        words = []
        for line_index, line in enumerate(result.lines):
            for word in line.words:
                box = word.bounding_rect
                words.append({"text":word.text,"rect":[box.x,box.y,box.width,box.height],"line":line_index})
                if limit is not None and len(words) == limit:
                    return words
        return words
    finally:
        bitmap.close()
