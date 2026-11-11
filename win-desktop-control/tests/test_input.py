from __future__ import annotations

import pytest

from wdc import input_ctl


def test_parse_chord_basic():
    assert input_ctl.parse_chord("Ctrl+Shift+S") == ["ctrl", "shift", "s"]
    assert input_ctl.parse_chord("enter") == ["enter"]


def test_parse_chord_aliases():
    assert input_ctl.parse_chord("cmd+space") == ["win", "space"]
    assert input_ctl.parse_chord("control+alt+del") == ["ctrl", "alt", "delete"]
    assert input_ctl.parse_chord("return") == ["enter"]
    assert input_ctl.parse_chord("pgdn") == ["pagedown"]


def test_parse_chord_invalid():
    with pytest.raises(ValueError):
        input_ctl.parse_chord("")


def test_click_validation(fake_backend):
    with pytest.raises(ValueError):
        input_ctl.click(10, 10, button="侧键")


def test_move_and_click(fake_backend):
    input_ctl.move(100, 200, duration=0)
    assert fake_backend.position() == (100, 200)
    input_ctl.click(300, 400, clicks=2)
    assert ("click", 300, 400, "left", 2) in fake_backend.calls


def test_drag(fake_backend):
    input_ctl.drag(10, 10, 50, 60, duration=0.1)
    assert any(call[0] == "drag" for call in fake_backend.calls)


def test_scroll(fake_backend):
    input_ctl.scroll(3)
    assert ("scroll", 3, None, None, False) in fake_backend.calls
    input_ctl.scroll(-2, x=10, y=20, horizontal=True)
    assert ("scroll", -2, 10, 20, True) in fake_backend.calls


def test_type_text_ascii(fake_backend):
    result = input_ctl.type_text("hello", interval=0)
    assert result["method"] == "typewrite"
    assert ("write", "hello") in fake_backend.calls


def test_press_key_single_and_combo(fake_backend):
    input_ctl.press_key("enter")
    input_ctl.press_key("ctrl+c")
    assert ("press", "enter") in fake_backend.calls
    assert ("hotkey", ("ctrl", "c")) in fake_backend.calls


def test_press_key_hold(fake_backend):
    input_ctl.press_key("shift", hold_ms=1)
    assert ("key_down", "shift") in fake_backend.calls
    assert ("key_up", "shift") in fake_backend.calls


def test_press_key_repeat(fake_backend):
    input_ctl.press_key("tab", repeat=3)
    assert fake_backend.calls.count(("press", "tab")) == 3


def test_pointer_events_are_scoped_and_precede_click(fake_backend, monkeypatch, capsys):
    import json
    from wdc import pointer_events
    monkeypatch.setenv('WDC_FEEDBACK_EVENTS', '1')
    original = fake_backend.click
    def click(*args, **kwargs):
        packet = json.loads(capsys.readouterr().err.split('WDC_POINTER ')[1])
        assert packet['id'] == '73' and packet['point'] == [300,400]
        assert fake_backend.position() == (300,400)
        return original(*args, **kwargs)
    monkeypatch.setattr(fake_backend, 'click', click)
    with pointer_events.operation(73):
        input_ctl.click(300,400)
    assert not pointer_events.enabled()


def test_pointer_motion_steps_read_back_positions_and_cancel_releases_drag(fake_backend, monkeypatch):
    from wdc import pointer_events
    monkeypatch.setenv('WDC_FEEDBACK_EVENTS', '1')
    points=[]
    monkeypatch.setattr(pointer_events, 'position', lambda x,y: points.append((x,y)))
    monkeypatch.setattr(input_ctl, 'cancellable_sleep', lambda seconds: None)
    with pointer_events.operation(1):
        input_ctl.move(100,200,duration=.2)
    assert len(points)==4 and points[-1]==(100,200)
    assert all(0<=x<=100 and 0<=y<=200 for x,y in points)
    def stopped(seconds): raise RuntimeError('stopped')
    monkeypatch.setattr(input_ctl, 'cancellable_sleep', stopped)
    with pointer_events.operation(2):
        with pytest.raises(RuntimeError): input_ctl.drag(10,20,100,200,duration=.2)
    assert fake_backend.calls[-1] == ('mouse_up','left')


def test_pointer_events_default_off_and_failures_do_not_change_input(fake_backend, monkeypatch, capsys):
    from wdc import pointer_events
    monkeypatch.delenv('WDC_FEEDBACK_EVENTS', raising=False)
    with pointer_events.operation(1): input_ctl.move(10,20,duration=0)
    assert capsys.readouterr().err == ''
    monkeypatch.setenv('WDC_FEEDBACK_EVENTS','1')
    class Broken:
        def write(self,*args): raise OSError('broken')
    monkeypatch.setattr(pointer_events.sys,'stderr',Broken())
    with pointer_events.operation(2): input_ctl.click(30,40)
    assert fake_backend.position() == (30,40)
