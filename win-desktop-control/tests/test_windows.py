"""窗口模块：非 Windows 平台必须「优雅降级」，而不是抛异常把服务带崩。"""

from __future__ import annotations

import sys

import pytest

from wdc import windows

non_windows = pytest.mark.skipif(sys.platform == "win32", reason="仅验证非 Windows 降级路径")


@non_windows
def test_list_windows_returns_empty():
    assert windows.list_windows() == []


@non_windows
def test_foreground_none():
    assert windows.get_foreground_window() is None


@non_windows
def test_activate_unsupported():
    result = windows.activate_window(title="记事本")
    assert result["ok"] is False
    assert "Windows" in result["error"]


@non_windows
def test_window_action_unsupported():
    result = windows.window_action("maximize", title="x")
    assert result["ok"] is False


@non_windows
def test_find_window_none():
    assert windows.find_window(title="anything") is None


def test_window_info_serialization():
    info = windows.WindowInfo(handle=0x1F, title="示例", rect=(10, 20, 110, 220))
    data = info.to_dict()
    assert data["handle_hex"] == "0x1f"
    assert data["rect"]["width"] == 100
    assert data["rect"]["height"] == 200


def test_launch_app_empty_path():
    result = windows.launch_app("")
    assert result["ok"] is False
