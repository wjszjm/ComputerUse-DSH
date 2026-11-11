"""Mock OS boundaries while retaining production coordinate/encoding/guard logic."""
import os
import asyncio
import platform
from pathlib import Path
import subprocess
import sys
import types

import pytest


class MemoryInput:
    name = 'memory-desktop'
    def __init__(self):
        self.calls = []
        self.point = (0, 0)
    def screen_size(self): return (1920, 1080)
    def position(self): return self.point
    def move_to(self, x, y, duration=0):
        self.point = (int(x), int(y)); self.calls.append(('move', x, y))
    def __getattr__(self, name):
        if name in {'mouse_down','mouse_up','click','scroll','write','press','key_down','key_up','hotkey','drag'}:
            return lambda *args, **kwargs: self.calls.append((name, args, kwargs))
        raise AttributeError(name)


def denied(*args, **kwargs):
    raise RuntimeError('Real desktop/process access blocked by development sandbox')


class DeniedNative:
    def __getattr__(self, name): return denied


@pytest.fixture(autouse=True)
def isolated_desktop(monkeypatch, tmp_path):
    from test_support.memory_loop import MemoryPolicy
    monkeypatch.setattr(asyncio.events, '_event_loop_policy', MemoryPolicy())
    from PIL import Image
    from wdc import capture, cli, config, input_ctl, platform_win, windows
    from wdc import native_overlay
    monkeypatch.setattr(native_overlay, 'WindowsApi', denied)
    memory = MemoryInput()
    for name, value in {'system':'Windows','release':'11','version':'10.0.26100','machine':'AMD64'}.items():
        monkeypatch.setattr(platform, name, lambda value=value: value)
    monkeypatch.setattr(platform, 'architecture', lambda *args, **kwargs: ('64bit','WindowsPE'))
    clipboard = {'text': ''}
    clip = types.ModuleType('pyperclip')
    clip.copy = lambda text: clipboard.update(text=str(text))
    clip.paste = lambda: clipboard['text']
    monkeypatch.setitem(sys.modules, 'pyperclip', clip)
    monkeypatch.setitem(sys.modules, 'pyautogui', types.ModuleType('pyautogui'))
    monkeypatch.setattr(input_ctl, '_backend', memory)
    monkeypatch.setattr(input_ctl, 'PyAutoGUIBackend', denied)
    monkeypatch.setattr(windows, 'user32', DeniedNative())
    monkeypatch.setattr(windows, 'kernel32', DeniedNative())
    monkeypatch.setattr(windows, 'dwmapi', DeniedNative())
    window = {'handle': 123, 'title': 'Sandbox Window', 'process': 'sandbox.exe', 'pid': 1}
    monkeypatch.setattr(windows, 'list_windows', lambda **kwargs: [dict(window)])
    monkeypatch.setattr(windows, 'get_foreground_window', lambda: dict(window))
    monkeypatch.setattr(windows, 'find_window', lambda **kwargs: dict(window))
    monkeypatch.setattr(windows, 'activate_window', lambda **kwargs: {'ok': True, 'window': dict(window)})
    monkeypatch.setattr(windows, 'window_action', lambda action, **kwargs: {'ok': True, 'action': action, 'window': dict(window)})
    monkeypatch.setattr(windows, '_open_with_shell', denied)
    # Retain launch_app's empty-path validation; valid launches are rejected at Popen/Shell.
    monkeypatch.setattr(platform_win, 'setup_dpi_awareness', lambda: 'per-monitor-v2')
    monkeypatch.setattr(platform_win, 'get_dpi_scale', lambda: 1.0)
    monkeypatch.setattr(platform_win, 'get_virtual_screen', lambda: platform_win.VirtualScreen(0, 0, 1920, 1080))
    monkeypatch.setattr(platform_win, 'is_secure_desktop_active', lambda: False)
    monkeypatch.setattr(platform_win, 'get_session_info', lambda: {
        'platform':'sandbox', 'is_windows':True, 'python':sys.version.split()[0],
        'dpi_scale':1.0, 'dpi_awareness':'per-monitor-v2', 'secure_desktop':False,
        'elevated':False, 'remote_session':False, 'user':'sandbox', 'computer':'sandbox',
        'virtual_screen':{'left':0,'top':0,'width':1920,'height':1080},
    })
    monkeypatch.setattr(capture, 'list_monitors', lambda: [
        capture.Monitor(0,0,0,1920,1080), capture.Monitor(1,0,0,1920,1080,True)])
    monkeypatch.setattr(capture, '_grab_raw', lambda left, top, width, height: Image.new('RGB',(width,height),'#375a7f'))
    monkeypatch.setattr(capture, '_mss_factory', denied)
    monkeypatch.setattr(config, 'user_config_dir', lambda: tmp_path / 'config')
    monkeypatch.setattr(cli, 'user_config_dir', lambda: tmp_path / 'config')
    # Prevent console code-page mutation in the CLI tests.
    monkeypatch.setattr(cli, '_setup_console', lambda: None)
    monkeypatch.setenv('APPDATA', str(tmp_path))
    for key in list(os.environ):
        if key.startswith('WDC_'): monkeypatch.delenv(key, raising=False)
    # Only the existing import-only subprocess test is allowed to create a child.
    class SafePopen(subprocess.Popen):
        def __init__(self, args, *extra, **kwargs):
            if not (isinstance(args, list) and len(args)==3 and args[0]==sys.executable
                and args[1]=='-c' and args[2]=="import sys;import wdc, wdc.tools, wdc.selftest;print('pyautogui' in sys.modules)"):
                denied()
            super().__init__(args, *extra, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', SafePopen)
    return {'input': memory, 'clipboard': clipboard}
