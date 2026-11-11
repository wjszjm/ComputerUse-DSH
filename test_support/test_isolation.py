import sys
import pytest


def test_uses_public_source():
    import wdc
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert Path(wdc.__file__).resolve().is_relative_to(root / 'win-desktop-control')


def test_clipboard_and_input_are_memory_only(isolated_desktop):
    from wdc import input_ctl
    input_ctl.clipboard_set('baseline')
    assert input_ctl.clipboard_get() == 'baseline'
    result = input_ctl.type_text('隔离输入 1,234', use_clipboard='always')
    assert result['method'] == 'clipboard-paste'
    assert input_ctl.clipboard_get() == 'baseline'
    assert isolated_desktop['input'].calls


def test_native_and_launch_escape_routes_blocked():
    from wdc import windows, capture
    with pytest.raises(RuntimeError): windows.user32.SetForegroundWindow(123)
    with pytest.raises(RuntimeError): windows._open_with_shell('notepad')
    with pytest.raises(RuntimeError): capture._mss_factory()
    assert windows.launch_app('notepad')['ok'] is False


def test_unapproved_child_process_blocked():
    import subprocess
    with pytest.raises(RuntimeError): subprocess.Popen(['notepad'])
