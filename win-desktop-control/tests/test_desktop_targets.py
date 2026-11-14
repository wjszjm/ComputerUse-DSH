from threading import Event
from types import SimpleNamespace
import sys

import pytest

from wdc import desktop_targets, stop_hotkey, windows
from wdc.config import Config
from wdc.tools import ComputerUse
from wdc.safety import SafetyViolation


def test_control_action_rechecks_live_properties_before_invoking(monkeypatch):
    from contextlib import nullcontext

    control = SimpleNamespace(ProcessId=20,Name="Live name",ControlTypeName="ButtonControl",
        IsOffscreen=False,IsPassword=False,IsEnabled=True,GetChildren=lambda: [],
        BoundingRectangle=SimpleNamespace(left=1,top=2,right=3,bottom=4),
        Element=SimpleNamespace(GetRuntimeId=lambda: [1,2]))
    uia = SimpleNamespace(UIAutomationInitializerInThread=nullcontext,ControlFromHandle=lambda _:control)
    monkeypatch.setitem(sys.modules,"uiautomation",uia)
    monkeypatch.setattr(desktop_targets,"target_window",lambda *args,**kwargs: (100,20,(0,0,100,100)))
    monkeypatch.setattr(desktop_targets,"invoke",lambda *_: pytest.fail("Changed control must not be invoked"))
    expected = {"name":"Old name","type":"ButtonControl","rect":[1,2,3,4]}
    with pytest.raises(SafetyViolation) as failure:
        desktop_targets.uia_controls(100,20,select=("1.2","invoke","",expected))
    assert failure.value.code == "stale_control"


def test_cached_reader_batches_child_properties_and_honors_process_scope(monkeypatch):
    from types import ModuleType

    names = ("ProcessId","Name","ControlType","BoundingRectangle","IsOffscreen","IsPassword","IsEnabled","RuntimeId")
    ids = SimpleNamespace(**{name+"Property":index for index,name in enumerate(names)})
    properties = []
    subtree_reads = []
    request = SimpleNamespace(AddProperty=properties.append)
    class Element:
        def __init__(self,pid,label,kind,children=(),password=False):
            self.CachedProcessId,self.CachedName,self.CachedControlType = pid,label,kind
            self.CachedIsEnabled,self.CachedIsOffscreen,self.CachedIsPassword = True,False,password
            self.CachedBoundingRectangle = SimpleNamespace(left=2,top=4,right=10,bottom=20)
            self.children = children

        def GetCachedPropertyValue(self,prop):
            assert prop == ids.RuntimeIdProperty
            return [3,self.CachedControlType]

        def FindAllBuildCache(self,scope,condition,cache):
            assert scope == 2 and cache is request
            subtree_reads.append(self.CachedName)
            return SimpleNamespace(Length=len(self.children),GetElement=self.children.__getitem__)

    root = Element(20,"Window",1,[Element(20,"Open",2),Element(20,"Open",2,password=True),Element(21,"Open",2)])
    automation = SimpleNamespace(CreateCacheRequest=lambda: request,RawViewCondition=object(),
        ElementFromHandleBuildCache=lambda hwnd,cache:root,CreateTrueCondition=lambda:object())
    fixture = ModuleType("uiautomation.uiautomation")
    fixture.ControlTypeNames = {1:"WindowControl",2:"ButtonControl"}
    fixture._AutomationClient = SimpleNamespace(instance=lambda:SimpleNamespace(IUIAutomation=automation))
    monkeypatch.setitem(sys.modules,"uiautomation.uiautomation",fixture)
    result = desktop_targets._cached_uia(SimpleNamespace(PropertyId=ids),12,20,"open","buttoncontrol",40,float("inf"),None)
    assert len(properties) == 8 and subtree_reads == ["Window","Open","Open"]
    assert result == {"controls":[{"id":"3.2","name":"Open","type":"ButtonControl","rect":[2,4,10,20],
                                   "enabled":True,"depth":1}],"truncated":False}


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


def test_wait_and_batch_bind_identity_and_stop_on_failed_confirmation(monkeypatch):
    app = ComputerUse(Config(audit_log=False))
    calls = []
    monkeypatch.setattr(windows,"IS_WINDOWS",True)
    monkeypatch.setattr("wdc.tools.platform_win.is_secure_desktop_active",lambda: False)
    def target(handle,pid,*,foreground=True):
        calls.append(("target",handle,pid,foreground))
        if foreground:
            raise windows.WindowError("Focus moved.","window_not_foreground")
        return handle,pid,(0,0,100,100)
    monkeypatch.setattr(desktop_targets,"target_window",target)
    monkeypatch.setattr(windows,"window_action",lambda **step: calls.append(("action",step["action"])) or
                        {"ok":True,"verification":{"status":"verified"}})
    identity = {"handle":100,"expected_pid":20}
    condition = {"kind":"window_visible",**identity}
    cancel = Event()
    cancel.set()
    assert app.wait(condition=condition,cancel=cancel).code == "operation_cancelled"
    assert not calls
    assert app.wait(condition=condition).data["pid"] == 20
    result = app.batch_windows([{"action":action,**identity} for action in
                                ("wait_visible","focus","confirm_foreground","maximize")])
    assert result.code == "window_not_foreground"
    assert result.data["failed_step"] == 3
    assert [item["action"] for item in result.data["completed"]] == ["wait_visible","focus"]
    assert ("action","maximize") not in calls


def test_checkbox_verified_only_after_exact_readback():
    class Toggle:
        ToggleState = 0
        toggles = 0
        def Toggle(self):
            self.toggles += 1
            self.ToggleState = 1 - self.ToggleState

    pattern = Toggle()
    class Control:
        def GetTogglePattern(self):
            return pattern

    assert desktop_targets.invoke(Control(),{"enabled":True},"check","") == {
        "sent":True,"verification":{"status":"verified","method":"uia_readback"},
    }
    assert desktop_targets.invoke(Control(),{"enabled":True},"check","")["sent"] is False
    assert pattern.toggles == 1
    assert desktop_targets.invoke(Control(),{"enabled":True},"uncheck","")["verification"]["status"] == "verified"
    assert pattern.ToggleState == 0
