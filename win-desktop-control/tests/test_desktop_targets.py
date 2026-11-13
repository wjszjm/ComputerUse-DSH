from wdc import desktop_targets, stop_hotkey
from wdc.config import Config
from wdc.tools import ComputerUse


def test_ocr_matches_multi_word_labels_without_crossing_lines():
    words=[
        {"text":"Save","line":0,"rect":[0,0,15,10]},
        {"text":"As","line":0,"rect":[16,0,8,10]},
        {"text":"As","line":1,"rect":[0,20,8,10]},
    ]
    assert desktop_targets.text_match(words,"Save As")==[(12,5)]
    assert not desktop_targets.text_match(words,"As As")


def test_window_batch_prevalidates_and_stops_after_failed_readback(monkeypatch):
    app=ComputerUse(Config(audit_log=False))
    actions=[]
    def send(**step):
        actions.append(step["action"])
        assert app.session.input_active
        if step["action"]=="minimize":
            return {"ok": False, "verification": {"status": "failed"}}
        return {"ok": True, "verification": {"status": "verified"}}
    monkeypatch.setattr("wdc.tools.windows.window_action",send)
    target={"handle":100,"expected_pid":20}
    invalid=app.batch_windows([{"action":"focus",**target},{"action":"move","x":-100,**target}])
    assert not invalid.ok and actions==[]
    result=app.batch_windows([{"action":action,**target} for action in ("focus","minimize","restore")])
    assert not result.ok
    assert result.data["failed_step"]==2
    assert result.data["completed"]==[{"step":1,"action":"focus","verified":True}]
    assert actions==["focus","minimize"]


def test_stop_hotkey_starts_armed_only_after_release(monkeypatch):
    pressed=[True]
    monkeypatch.setattr(stop_hotkey,"_pressed",lambda _:pressed[0])
    monkeypatch.setattr(stop_hotkey.sys,"platform","win32")
    monkeypatch.setenv("WDC_STOP_HOTKEY","esc")
    assert not stop_hotkey.should_stop()
    with stop_hotkey.input_scope():
        assert not stop_hotkey.should_stop()
        pressed[0]=False
        assert not stop_hotkey.should_stop()
        pressed[0]=True
        assert stop_hotkey.should_stop()
    assert not stop_hotkey.should_stop()
