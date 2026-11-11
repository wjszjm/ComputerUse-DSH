"""Fault scenarios: all OS boundaries are isolated by the root conftest."""
import asyncio
import json

import pytest

from wdc import input_ctl, windows
from wdc.config import Config
from wdc.mcp_server import build_server
from wdc.tools import ComputerUse


@pytest.mark.parametrize('action', ['focus', 'activate', 'foreground'])
@pytest.mark.parametrize('mode,code', [('read_only','read_only'), ('disabled','input_disabled'), ('stopped','emergency_stop')])
def test_focus_obeys_all_input_guards(monkeypatch, action, mode, code):
    calls = []
    monkeypatch.setattr(windows,'window_action',lambda *a,**kw:calls.append(a) or {'ok':True})
    cfg = Config(audit_log=False, read_only=mode=='read_only', allow_input=mode!='disabled')
    app = ComputerUse(cfg)
    if mode == 'stopped': app.emergency_stop('test')
    result = app.window_action(action, handle=123)
    assert not result.ok and result.code == code
    assert calls == []


@pytest.mark.parametrize('keys',['control+alt+del','delete+alt+ctrl','L+command','shift+meta+l'])
def test_shortcut_aliases_and_order_cannot_bypass_blocklist(keys, isolated_desktop):
    result = ComputerUse(Config(audit_log=False)).press_key(keys)
    assert not result.ok and result.code == 'blocked_shortcut'
    assert isolated_desktop['input'].calls == []


def test_repeated_input_stops_and_resume_does_not_continue_old_action(monkeypatch, isolated_desktop):
    app = ComputerUse(Config(audit_log=False))
    pressed = []
    def press(key):
        pressed.append(key)
        if len(pressed) == 1:
            app.emergency_stop('interrupt')
            app.resume()
    monkeypatch.setattr(isolated_desktop['input'],'press',press,raising=False)
    result = app.press_key('tab',repeat=5)
    assert result.code == 'emergency_stop' and pressed == ['tab']
    assert app.press_key('enter').ok
    assert pressed == ['tab','enter']


def test_stop_file_interrupts_hold_and_releases_keys(monkeypatch,tmp_path,isolated_desktop):
    stop = tmp_path/'stop.flag'
    monkeypatch.setenv('WDC_STOP_FILE',str(stop))
    sleeps = []
    def sleep(seconds):
        sleeps.append(seconds)
        stop.write_text('stop')
    monkeypatch.setattr(input_ctl.time,'sleep',sleep)
    app = ComputerUse(Config(audit_log=False))
    result = app.press_key('ctrl+shift',hold_ms=10000)
    assert result.code == 'emergency_stop'
    assert sleeps == [0.05]
    assert [c[0:2] for c in isolated_desktop['input'].calls][-2:] == [('key_up',('shift',)),('key_up',('ctrl',))]
    # A new guard/process must still honor the persistent stop file.
    assert ComputerUse(Config(audit_log=False)).click(10,10).code == 'emergency_stop'
    assert app.resume().data['stopped'] is True


def test_text_cancel_does_not_type_remaining_chars_or_enter(monkeypatch,tmp_path,isolated_desktop):
    stop = tmp_path/'stop.flag'
    monkeypatch.setenv('WDC_STOP_FILE',str(stop))
    writes = []
    def write(text,**kwargs):
        writes.append(text)
        stop.write_text('stop')
    monkeypatch.setattr(isolated_desktop['input'],'write',write,raising=False)
    result = ComputerUse(Config(audit_log=False)).type_text('abc',press_enter=True,use_clipboard='never')
    assert result.code == 'emergency_stop' and writes == ['a']
    assert not any(call[0]=='press' for call in isolated_desktop['input'].calls)


def test_partial_key_down_failure_releases_every_attempted_key(monkeypatch,isolated_desktop):
    released = []
    def down(key):
        if key=='shift': raise RuntimeError('partial key-down failure')
    monkeypatch.setattr(isolated_desktop['input'],'key_down',down,raising=False)
    monkeypatch.setattr(isolated_desktop['input'],'key_up',released.append,raising=False)
    result = ComputerUse(Config(audit_log=False)).click(10,10,modifiers='ctrl+shift')
    assert not result.ok and released == ['shift','ctrl']


def test_key_release_failure_still_releases_other_keys(monkeypatch,isolated_desktop):
    released=[]
    def up(key):
        released.append(key)
        if key=='shift': raise RuntimeError('release failed')
    monkeypatch.setattr(isolated_desktop['input'],'key_up',up,raising=False)
    result=ComputerUse(Config(audit_log=False)).press_key('ctrl+shift',hold_ms=1)
    assert result.code=='input_unavailable' and released==['shift','ctrl']


def test_paste_cancel_restores_empty_clipboard_and_sends_no_input(monkeypatch,isolated_desktop):
    app=ComputerUse(Config(audit_log=False))
    monkeypatch.setattr(input_ctl.time,'sleep',lambda _:app.emergency_stop('cancel paste'))
    result=app.type_text('not sent')
    assert result.code=='emergency_stop'
    assert input_ctl.clipboard_get()==''
    assert isolated_desktop['input'].calls==[]


def test_always_clipboard_failure_does_not_fallback_to_typing(monkeypatch,isolated_desktop):
    import pyperclip
    def fail(text): raise RuntimeError('clipboard unavailable')
    monkeypatch.setattr(pyperclip,'copy',fail)
    result=ComputerUse(Config(audit_log=False)).type_text('1,234')
    assert result.code=='input_unavailable'
    assert isolated_desktop['input'].calls==[]


def test_mcp_error_flag_agrees_with_business_failure():
    server=build_server(Config(audit_log=False,read_only=True))
    result=asyncio.run(server.call_tool('click',{'x':10,'y':10}))
    flag=getattr(result,'is_error',getattr(result,'isError',None))
    assert flag is True
    payload=json.loads(result.content[0].text)
    assert payload['ok'] is False and payload['error_code']=='read_only'


def test_multi_click_checks_stop_between_clicks(monkeypatch,isolated_desktop):
    app=ComputerUse(Config(audit_log=False))
    clicks=[]
    def click(*args,**kwargs):
        clicks.append(kwargs['clicks'])
        app.emergency_stop('one click only')
    monkeypatch.setattr(isolated_desktop['input'],'click',click,raising=False)
    result=app.click(10,10,clicks=3)
    assert result.code=='emergency_stop' and clicks==[1]


def test_overlapping_input_rejected_but_stop_is_not_blocked(monkeypatch,isolated_desktop):
    app=ComputerUse(Config(audit_log=False))
    overlap=[]
    def press(key):
        overlap.append(app.click(10,10))
        app.emergency_stop('stop during input')
    monkeypatch.setattr(isolated_desktop['input'],'press',press,raising=False)
    result=app.press_key('enter')
    assert overlap[0].code=='action_busy'
    assert result.code=='emergency_stop'
    app.resume()
    assert app.click(10,10).ok  # A failure must release the action lock.


def test_stop_and_resume_before_backend_entry_does_not_resurrect_request(monkeypatch,isolated_desktop):
    app=ComputerUse(Config(audit_log=False))
    def during_setup():
        app.emergency_stop('interrupt before backend entry')
        app.resume()
    monkeypatch.setattr(app.guard,'check_rate',during_setup)
    result=app.click(10,10)
    assert result.code=='emergency_stop'
    assert isolated_desktop['input'].calls==[]
