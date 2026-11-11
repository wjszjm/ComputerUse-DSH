"""pytest 公共夹具：显示环境检测 + 假输入后端。"""

from __future__ import annotations

import os
import shutil
import sys

import pytest
from test_support.desktop_sandbox import isolated_desktop

# pytest 导入本文件时就要保证 DISPLAY 可用，否则 pyautogui 在 import 阶段就会抛异常
if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
    os.environ["DISPLAY"] = ":99"


def display_available() -> bool:
    if sys.platform == "win32":
        return True
    if not os.environ.get("DISPLAY"):
        return False
    return shutil.which("xdpyinfo") is not None or os.path.exists("/tmp/.X11-unix")


@pytest.fixture(scope="session")
def gui() -> None:
    if not display_available():
        pytest.skip("当前环境没有可用的图形显示（需要 Windows 或 Xvfb）")


@pytest.fixture()
def fake_backend():
    """记录调用的假后端，避免测试真的移动用户鼠标。"""
    from wdc.input_ctl import set_backend

    class FakeBackend:
        name = "fake"

        def __init__(self) -> None:
            self.calls: list[tuple] = []
            self._pos = (0, 0)

        def screen_size(self):
            return (1920, 1080)

        def position(self):
            return self._pos

        def move_to(self, x, y, duration=0.0):
            self._pos = (int(x), int(y))
            self.calls.append(("move_to", int(x), int(y), round(float(duration), 3)))

        def mouse_down(self, button="left"):
            self.calls.append(("mouse_down", button))

        def mouse_up(self, button="left"):
            self.calls.append(("mouse_up", button))

        def click(self, x, y, button="left", clicks=1, interval=0.05):
            self._pos = (int(x), int(y))
            self.calls.append(("click", int(x), int(y), button, int(clicks)))

        def scroll(self, clicks, x=None, y=None, horizontal=False):
            self.calls.append(("scroll", int(clicks), x, y, bool(horizontal)))

        def write(self, text, interval=0.0):
            self.calls.append(("write", text))

        def press(self, key):
            self.calls.append(("press", key))

        def key_down(self, key):
            self.calls.append(("key_down", key))

        def key_up(self, key):
            self.calls.append(("key_up", key))

        def hotkey(self, keys):
            self.calls.append(("hotkey", tuple(keys)))

        def drag(self, x1, y1, x2, y2, duration=0.5, button="left"):
            self.calls.append(("drag", x1, y1, x2, y2, button))

    backend = FakeBackend()
    set_backend(backend)
    try:
        yield backend
    finally:
        set_backend(None)


@pytest.fixture()
def temp_config(tmp_path, monkeypatch):
    from wdc.config import Config

    cfg = Config(
        audit_log=True,
        audit_path=str(tmp_path / "audit.log"),
        screenshot_dir=str(tmp_path / "shots"),
        allow_input=True,
        max_actions_per_second=1000,
    )
    return cfg
