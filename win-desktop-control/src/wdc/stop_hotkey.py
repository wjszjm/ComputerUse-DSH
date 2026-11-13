"""Opt-in, non-consuming emergency key polling during an owned input call."""
from __future__ import annotations

import ctypes
import os
import sys
from contextlib import contextmanager
from contextvars import ContextVar

_keys = {"esc":(0x1B,), "ctrl+shift+f12":(0x11,0x10,0x7B)}
_active: ContextVar[tuple | None] = ContextVar("wdc_stop_hotkey",default=None)


def _pressed(keys):
    return all(ctypes.windll.user32.GetAsyncKeyState(key) & 0x8000 for key in keys)


@contextmanager
def input_scope():
    keys = _keys.get(os.environ.get("WDC_STOP_HOTKEY","")) if sys.platform == "win32" else None
    token = _active.set((keys, _pressed(keys)) if keys else None)
    try:
        yield
    finally:
        _active.reset(token)


def should_stop():
    active = _active.get()
    if active is None:
        return False
    keys, started_pressed = active
    pressed = _pressed(keys)
    if started_pressed:
        if not pressed:
            _active.set((keys,False))
        return False
    return pressed
