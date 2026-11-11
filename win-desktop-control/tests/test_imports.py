from __future__ import annotations

import importlib

import pytest

MODULES = [
    "wdc",
    "wdc.config",
    "wdc.platform_win",
    "wdc.capture",
    "wdc.input_ctl",
    "wdc.windows",
    "wdc.safety",
    "wdc.tools",
    "wdc.schemas",
    "wdc.mcp_compat",
    "wdc.mcp_server",
    "wdc.selftest",
    "wdc.cli",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_importable(name):
    assert importlib.import_module(name) is not None


def test_version():
    import wdc

    assert wdc.__version__.count(".") == 2


def test_platform_info_on_any_os():
    from wdc import platform_win

    info = platform_win.get_session_info()
    for key in ("platform", "is_windows", "python", "dpi_scale", "virtual_screen"):
        assert key in info
    # 幂等：重复调用不抛异常
    assert platform_win.setup_dpi_awareness() == platform_win.setup_dpi_awareness()


def test_no_hard_dependency_on_pyautogui_at_import():
    """核心模块不能在导入期就拉起 GUI 库，否则无显示环境直接崩。"""
    import subprocess
    import sys

    code = (
        "import sys;"
        "import wdc, wdc.tools, wdc.selftest;"
        "print('pyautogui' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"
