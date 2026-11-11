"""鼠标 / 键盘控制层。

- 后端可替换（默认 pyautogui），便于注入 mock 做无 GUI 单元测试。
- pyautogui 延迟导入：导入失败不影响只读能力（截图/查询）。
"""

from __future__ import annotations

import time
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable

from . import platform_win
from . import pointer_events


class InputError(RuntimeError):
    """输入设备不可用或操作被拒绝。"""


_cancel_check: ContextVar[Callable[[], None] | None] = ContextVar('wdc_cancel_check', default=None)


@contextmanager
def cancellation(check: Callable[[], None]):
    token = _cancel_check.set(check)
    try:
        check()
        yield
        check()
    finally:
        _cancel_check.reset(token)


def check_cancelled() -> None:
    check = _cancel_check.get()
    if check is not None:
        check()


def cancellable_sleep(seconds: float) -> None:
    remaining = max(0.0, float(seconds))
    check_cancelled()
    while remaining > 0:
        step = min(0.05, remaining)
        time.sleep(step)
        remaining -= step
        check_cancelled()


@contextmanager
def held_keys(backend, keys):
    attempted = []
    try:
        for key in keys:
            check_cancelled()
            attempted.append(key)
            backend.key_down(key)
        yield
    finally:
        failed = None
        unwinding = sys.exc_info()[0] is not None
        for key in reversed(attempted):
            try:
                backend.key_up(key)
            except Exception as exc:
                failed = failed or exc
        if failed is not None and not unwinding:
            raise InputError('释放按键失败，请检查按键状态。') from failed


@dataclass
class InputStatus:
    available: bool
    backend: str
    detail: str = ""


# --------------------------------------------------------------------------- #
# 后端
# --------------------------------------------------------------------------- #
@runtime_checkable
class InputBackend(Protocol):
    name: str

    def screen_size(self) -> tuple[int, int]: ...
    def position(self) -> tuple[int, int]: ...
    def move_to(self, x: int, y: int, duration: float = 0.0) -> None: ...
    def mouse_down(self, button: str = "left") -> None: ...
    def mouse_up(self, button: str = "left") -> None: ...
    def click(self, x: int, y: int, button: str = "left", clicks: int = 1, interval: float = 0.05) -> None: ...
    def scroll(self, clicks: int, x: int | None = None, y: int | None = None,
               horizontal: bool = False) -> None: ...
    def write(self, text: str, interval: float = 0.0) -> None: ...
    def press(self, key: str) -> None: ...
    def key_down(self, key: str) -> None: ...
    def key_up(self, key: str) -> None: ...
    def hotkey(self, keys: list[str]) -> None: ...


class PyAutoGUIBackend:
    """基于 pyautogui 的后端（Windows 下最终调用 user32 SendInput）。"""

    name = "pyautogui"

    def __init__(self, pause: float = 0.02, failsafe: bool = False) -> None:
        platform_win.setup_dpi_awareness()  # 必须早于 pyautogui 首次取屏幕尺寸
        try:
            import pyautogui  # noqa: PLC0415
        except Exception as exc:  # pragma: no cover - 取决于运行环境
            raise InputError(f"pyautogui 加载失败：{exc}") from exc
        pyautogui.PAUSE = max(0.0, float(pause))
        pyautogui.FAILSAFE = bool(failsafe)
        self._gui = pyautogui

    # -- 查询 --
    def screen_size(self) -> tuple[int, int]:
        size = self._gui.size()
        return int(size[0]), int(size[1])

    def position(self) -> tuple[int, int]:
        x, y = self._gui.position()
        return int(x), int(y)

    # -- 鼠标 --
    def move_to(self, x: int, y: int, duration: float = 0.0) -> None:
        if duration and duration > 0:
            self._gui.moveTo(x, y, duration=float(duration), tween=self._gui.easeOutQuad)
        else:
            self._gui.moveTo(x, y)

    def mouse_down(self, button: str = "left") -> None:
        self._gui.mouseDown(button=button)

    def mouse_up(self, button: str = "left") -> None:
        self._gui.mouseUp(button=button)

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.5,
             button: str = "left") -> None:
        self.move_to(x1, y1)
        self._gui.mouseDown(button=button)
        try:
            if duration and duration > 0:
                self._gui.moveTo(x2, y2, duration=float(duration), tween=self._gui.easeOutQuad)
            else:
                self._gui.moveTo(x2, y2)
        finally:
            self._gui.mouseUp(button=button)

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1,
              interval: float = 0.05) -> None:
        self._gui.click(x=x, y=y, clicks=int(clicks), interval=float(interval), button=button)

    def scroll(self, clicks: int, x: int | None = None, y: int | None = None,
               horizontal: bool = False) -> None:
        kwargs: dict[str, Any] = {"clicks": int(clicks)}
        if x is not None and y is not None:
            kwargs["x"], kwargs["y"] = int(x), int(y)
        if horizontal:
            self._gui.hscroll(**kwargs)
        else:
            self._gui.scroll(**kwargs)

    # -- 键盘 --
    def write(self, text: str, interval: float = 0.0) -> None:
        self._gui.write(text, interval=float(interval))

    def press(self, key: str) -> None:
        self._gui.press(key)

    def key_down(self, key: str) -> None:
        self._gui.keyDown(key)

    def key_up(self, key: str) -> None:
        self._gui.keyUp(key)

    def hotkey(self, keys: list[str]) -> None:
        self._gui.hotkey(*keys)


# --------------------------------------------------------------------------- #
# 单例管理
# --------------------------------------------------------------------------- #
_backend: InputBackend | None = None
_backend_error: str = ""

_KEY_ALIASES = {
    "cmd": "win", "command": "win", "super": "win", "meta": "win", "win": "win",
    "control": "ctrl", "ctl": "ctrl",
    "option": "alt", "opt": "alt",
    "return": "enter", "cr": "enter",
    "escape": "esc",
    "del": "delete",
    "ins": "insert",
    "pgup": "pageup", "page_up": "pageup", "prior": "pageup",
    "pgdn": "pagedown", "page_down": "pagedown", "next": "pagedown",
    "caps": "capslock", "capital": "capslock",
    "print_screen": "printscreen", "prtsc": "printscreen", "prtscr": "printscreen",
    "spacebar": "space",
    "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right",
    "+" : "plus",
}

_MODIFIERS = {"ctrl", "alt", "shift", "win"}


def normalize_key(key: str) -> str:
    key = (key or "").strip()
    if not key:
        raise ValueError("按键名不能为空")
    low = key.lower().replace(" ", "")
    return _KEY_ALIASES.get(low, low)


def parse_chord(chord: str) -> list[str]:
    """把 "Ctrl+Shift+S" 解析为 ["ctrl", "shift", "s"]。"""
    if chord is None:
        raise ValueError("快捷键不能为空")
    parts = [p for p in str(chord).replace(" ", "").split("+") if p]
    if not parts:
        raise ValueError(f"无法解析的快捷键：{chord!r}")
    keys = [normalize_key(p) for p in parts]
    # 允许 "Ctrl++" 这类把加号本身作为主键的写法
    if str(chord).strip().lower() in {"ctrl++", "shift++", "alt++"}:
        keys = [normalize_key(str(chord).split("+")[0]), "plus"]
    return keys


def get_backend() -> InputBackend:
    global _backend, _backend_error
    if _backend is not None:
        return _backend
    try:
        _backend = PyAutoGUIBackend()
        _backend_error = ""
        return _backend
    except Exception as exc:
        _backend_error = str(exc)
        raise InputError(_backend_error) from exc


def set_backend(backend: InputBackend | None) -> None:
    """测试注入点。"""
    global _backend, _backend_error
    _backend = backend
    _backend_error = "" if backend is not None else _backend_error


def backend_status() -> InputStatus:
    try:
        backend = get_backend()
        backend.screen_size()
        return InputStatus(True, backend.name, "")
    except Exception as exc:
        return InputStatus(False, "none", str(exc) or _backend_error)


# --------------------------------------------------------------------------- #
# 高层动作
# --------------------------------------------------------------------------- #
def _move_feedback(backend, x, y, duration=0.0):
    if not pointer_events.enabled():
        backend.move_to(int(x), int(y), duration=duration)
        return
    start_x, start_y = backend.position()
    seconds = max(0.0, float(duration))
    steps = min(1200, max(1, int(seconds / 0.05)))
    started = time.monotonic()
    for index in range(1, steps + 1):
        check_cancelled()
        ratio = index / steps
        backend.move_to(round(start_x + (x-start_x)*ratio), round(start_y + (y-start_y)*ratio))
        pointer_events.position(*backend.position())
        if seconds: cancellable_sleep(max(0.0, started + seconds*ratio - time.monotonic()))

def move(x: int, y: int, duration: float = 0.15) -> dict[str, Any]:
    check_cancelled()
    backend = get_backend()
    _move_feedback(backend, x, y, duration)
    px, py = backend.position()
    return {"position": [px, py]}


def click(x: int, y: int, button: str = "left", clicks: int = 1,
          interval: float = 0.05, duration: float = 0.0) -> dict[str, Any]:
    backend = get_backend()
    button = (button or "left").lower()
    if button in {"l", "1"}:
        button = "left"
    elif button in {"r", "2"}:
        button = "right"
    elif button in {"m", "3", "middle"}:
        button = "middle"
    if button not in {"left", "right", "middle"}:
        raise ValueError("button 只支持 left / right / middle")
    clicks = max(1, min(int(clicks), 3))
    if pointer_events.enabled() or (duration and duration > 0):
        _move_feedback(backend, x, y, duration)
    check_cancelled()
    if pointer_events.enabled(): cancellable_sleep(0.06)
    if _cancel_check.get() is not None and clicks > 1:
        for index in range(clicks):
            check_cancelled()
            backend.click(int(x), int(y), button=button, clicks=1, interval=interval)
            if index + 1 < clicks:
                cancellable_sleep(interval)
    else:
        backend.click(int(x), int(y), button=button, clicks=clicks, interval=interval)
    px, py = backend.position()
    return {"button": button, "clicks": clicks, "position": [px, py]}


def drag(x1: int, y1: int, x2: int, y2: int, duration: float = 0.5,
         button: str = "left") -> dict[str, Any]:
    check_cancelled()
    backend = get_backend()
    button = (button or "left").lower()
    if button not in {"left", "right", "middle"}:
        raise ValueError("button 只支持 left / right / middle")
    impl = getattr(backend, "drag", None)
    if callable(impl) and not pointer_events.enabled():
        impl(int(x1), int(y1), int(x2), int(y2), duration=float(duration), button=button)
    else:  # 极简后端没有 drag 时的兜底实现
        _move_feedback(backend, x1, y1)
        backend.mouse_down(button)
        try:
            _move_feedback(backend, x2, y2, float(duration))
        finally:
            backend.mouse_up(button)
    px, py = backend.position()
    return {"from": [int(x1), int(y1)], "to": [int(x2), int(y2)], "position": [px, py]}


def scroll(amount: int, x: int | None = None, y: int | None = None,
           horizontal: bool = False) -> dict[str, Any]:
    backend = get_backend()
    amount = int(amount)
    check_cancelled()
    if amount == 0:
        return {"scrolled": 0}
    if pointer_events.enabled():
        if x is not None and y is not None:
            _move_feedback(backend, x, y)
        else:
            pointer_events.position(*backend.position())
    backend.scroll(amount, x=x, y=y, horizontal=horizontal)
    return {"scrolled": amount, "horizontal": horizontal,
            "at": [int(x), int(y)] if x is not None and y is not None else None}


def _paste_text(text: str, interval: float = 0.0) -> bool:
    """用剪贴板粘贴非 ASCII 文本（pyautogui.write 不支持中文/表情）。

    返回 True 表示走的是粘贴通道。
    """
    try:
        import pyperclip  # noqa: PLC0415
    except Exception:
        return False

    previous = ""
    try:
        previous = pyperclip.paste()
    except Exception:
        previous = ""

    try:
        pyperclip.copy(text)
    except Exception:
        return False

    try:
        backend = get_backend()
        cancellable_sleep(0.06)
        check_cancelled()
        with held_keys(backend, ["ctrl"]):
            check_cancelled()
            backend.press("v")
        cancellable_sleep(min(0.35, 0.05 + len(text) / 4000))
    finally:
        # Restore empty text too, but do not overwrite newer clipboard content.
        try:
            if pyperclip.paste() == text:
                pyperclip.copy(previous)
        except Exception:
            pass
    return True


def type_text(text: str, interval: float = 0.02, press_enter: bool = False,
              use_clipboard: str = "auto") -> dict[str, Any]:
    """输入文本。use_clipboard: auto | always | never。

    auto：纯 ASCII 且较短时逐字输入，其余走剪贴板粘贴（对中文/长文本更稳）。
    """
    if text is None:
        text = ""
    text = str(text)
    if use_clipboard not in {"auto", "always", "never"}:
        raise ValueError('use_clipboard 只能是 auto / always / never')
    check_cancelled()
    backend = get_backend()

    method = "typewrite"
    ascii_only = all(ord(ch) < 128 for ch in text)
    need_paste = use_clipboard == "always" or (
        use_clipboard == "auto" and (not ascii_only or len(text) > 400)
    )

    if need_paste:
        if _paste_text(text, interval):
            method = "clipboard-paste"
        else:
            if use_clipboard == "always":
                raise InputError("剪贴板粘贴不可用；未降级为可能被输入法改写的逐字输入。")
            if not ascii_only:
                raise InputError(
                    "文本包含非 ASCII 字符，需要 pyperclip 才能输入，请先 pip install pyperclip"
                )
            _write_text(backend, text, float(interval))
    else:
        _write_text(backend, text, float(interval))

    if press_enter:
        check_cancelled()
        backend.press("enter")
    return {"chars": len(text), "method": method, "press_enter": bool(press_enter)}


def _write_text(backend, text: str, interval: float) -> None:
    if _cancel_check.get() is None:
        backend.write(text, interval=interval)
        return
    for char in text:
        check_cancelled()
        backend.write(char, interval=interval)


def press_key(chord: str, repeat: int = 1, hold_ms: int = 0) -> dict[str, Any]:
    keys = parse_chord(chord)
    backend = get_backend()
    repeat = max(1, min(int(repeat), 50))

    for _ in range(repeat):
        check_cancelled()
        if hold_ms and hold_ms > 0:
            with held_keys(backend, keys):
                cancellable_sleep(hold_ms / 1000.0)
        elif len(keys) == 1:
            backend.press(keys[0])
        elif _cancel_check.get() is None:
            backend.hotkey(keys)
        else:
            with held_keys(backend, keys[:-1]):
                check_cancelled()
                backend.press(keys[-1])
    return {"keys": keys, "repeat": repeat, "hold_ms": int(hold_ms)}


def clipboard_get() -> str:
    try:
        import pyperclip  # noqa: PLC0415
    except Exception as exc:
        raise InputError(f"pyperclip 不可用：{exc}") from exc
    return pyperclip.paste()


def clipboard_set(text: str) -> dict[str, Any]:
    try:
        import pyperclip  # noqa: PLC0415
    except Exception as exc:
        raise InputError(f"pyperclip 不可用：{exc}") from exc
    pyperclip.copy(str(text))
    return {"chars": len(str(text))}
