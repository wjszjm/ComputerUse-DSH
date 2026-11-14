"""Frame identity, freshness, coordinate geometry and observable outcomes; no desktop I/O."""
import asyncio
import ctypes as C
import json
from ctypes import wintypes as W
from types import SimpleNamespace

from PIL import Image
import pytest

from wdc import capture, input_ctl, platform_win, schemas, windows
from wdc.config import Config
from wdc.tools import ComputerUse

REAL_WINDOW_ACTION=windows.window_action
REAL_ACTIVATE=windows.activate_window


@pytest.fixture
def app(fake_backend):
    return ComputerUse(Config(audit_log=False,max_actions_per_second=1000))


def frame(origin=(0,0),size=(1000,607),image=(333,203)):
    return capture.Frame(Image.new('RGB',image),origin,size,size,image[0]/size[0],'primary')


@pytest.mark.parametrize('mode',['auto','image'])
def test_frame_outside_image_never_falls_back_to_screen(app,fake_backend,mode):
    app.session.set_frame(frame())
    result=app.click(500,200,coordinate_mode=mode)
    assert result.code=='image_out_of_bounds' and not result.ok and fake_backend.calls==[]


def test_explicit_image_requires_frame_but_explicit_screen_remains_available(app,fake_backend):
    assert app.click(20,20,coordinate_mode='image').code=='frame_required'
    result=app.click(20,20,coordinate_mode='screen')
    assert result.ok and result.data['screen_point']==[20,20]
    assert result.data['verification']['status']=='not_verified'


def test_frame_token_mismatch_and_input_invalidation_require_new_observation(app,fake_backend):
    shot=app.screenshot(grid=False)
    assert shot.ok and shot.data['coordinate_context_valid']
    old=shot.data['frame_id'];new=app.screenshot(grid=False).data['frame_id']
    assert old!=new
    assert app.click(10,10,frame_id=old).code=='frame_mismatch'
    # Re-observe even after a rejected targeting attempt (conservative invalidation).
    fresh=app.screenshot(grid=False).data['frame_id']
    result=app.click(10,10,frame_id=fresh)
    assert result.ok
    assert app.click(10,10,frame_id=fresh).code=='stale_frame'
    assert len([call for call in fake_backend.calls if call[0]=='click'])==1


@pytest.mark.parametrize('clock',['wall','monotonic'])
def test_frame_expiry_cannot_be_downgraded_to_a_warning(app,fake_backend,clock):
    value=frame()
    if clock=='wall':value.captured_at-=31
    else:value.captured_monotonic-=31;value.captured_at+=600  # Wall-clock rollback cannot rejuvenate it.
    app.session.set_frame(value)
    assert app.click(20,20,frame_id=value.frame_id).code=='stale_frame'
    assert fake_backend.calls==[]


@pytest.mark.parametrize('change',[{'handle':456},{'pid':2},{'rect':{'left':20,'top':0,'right':400,'bottom':300}}])
def test_changed_window_identity_or_bounds_blocks_frame_input(app,fake_backend,monkeypatch,change):
    token=app.screenshot(grid=False).data['frame_id']
    current={'handle':123,'pid':1,**change}
    monkeypatch.setattr(windows,'get_foreground_window',lambda:current)
    assert app.click(20,20,frame_id=token).code=='frame_context_changed'
    assert fake_backend.calls==[]


def test_layout_change_with_same_virtual_bounds_is_detected(app,fake_backend,monkeypatch):
    token=app.screenshot(grid=False).data['frame_id']
    monkeypatch.setattr(capture,'list_monitors',lambda:[capture.Monitor(0,0,0,1920,1080),
        capture.Monitor(1,0,0,960,1080,True),capture.Monitor(2,960,0,960,1080)])
    assert app.click(20,20,frame_id=token).code=='frame_context_changed'
    assert fake_backend.calls==[]


def test_mixed_dpi_negative_origin_uses_physical_pixels_and_rejects_dpi_change(app,fake_backend,monkeypatch):
    layout = [capture.Monitor(0,-1600,0,4160,1600), capture.Monitor(1,0,0,2560,1600,True,dpi_scale=1.5),
              capture.Monitor(2,-1600,0,1600,900,dpi_scale=1.0)]
    monkeypatch.setattr(capture,'list_monitors',lambda:layout)
    monkeypatch.setattr(platform_win,'get_virtual_screen',lambda:SimpleNamespace(left=-1600,top=0,right=2560,bottom=1600))
    shot = app.screenshot(region=[-300,80,600,320],scale=0.5,grid=False)
    assert shot.ok
    result = app.click(25,40,coordinate_mode='image',frame_id=shot.data['frame_id'])
    assert result.ok and result.data['screen_point']==[-250,160]
    shot = app.screenshot(region=[-300,80,600,320],scale=0.5,grid=False)
    layout[2].dpi_scale = 1.25
    assert app.click(25,40,coordinate_mode='image',frame_id=shot.data['frame_id']).code=='frame_context_changed'


def test_background_capture_never_samples_desktop_and_rejects_pointer_input(app,fake_backend,monkeypatch):
    from wdc import desktop_targets, window_capture

    bounds=(5,7,205,107)
    monkeypatch.setattr(desktop_targets,'target_window',lambda handle,pid,*,foreground:(handle,pid,bounds))
    monkeypatch.setattr(window_capture,'grab',lambda *args:Image.new('RGB',(200,100)))
    monkeypatch.setattr(capture,'_grab_raw',lambda *args:pytest.fail('background capture must be window-only'))
    shot=app.window_screenshot(100,200,background=True)
    assert shot.ok and shot.data['pointer_input'] is False
    assert app.click(25,40,coordinate_mode='image',frame_id=shot.data['frame_id']).code=='background_frame'
    assert fake_backend.calls==[]


def test_native_size_window_crop_keeps_physical_origin_and_rejects_occlusion(app,fake_backend,monkeypatch):
    from wdc import desktop_targets

    bounds = [5,7,205,107]
    monkeypatch.setattr(desktop_targets,'target_window',lambda handle,pid,**_: (handle,pid,tuple(bounds)))
    monkeypatch.setattr(desktop_targets,'window_at_point',lambda *_:True)
    shot = app.window_screenshot(100,200,scale=1,crop=[50,20,38,29])
    assert shot.ok
    assert shot.data["image_size"] == [38,29]
    assert app.click(12,4,frame_id=shot.data["frame_id"]).data["screen_point"] == [67,31]
    assert app.window_screenshot(100,200,crop=[199,99,2,2]).code == "bad_argument"
    shot = app.window_screenshot(100,200,scale=1,crop=[50,20,38,29])
    assert shot.ok
    monkeypatch.setattr(desktop_targets,'window_at_point',lambda *_:False)
    assert app.click(12,4,frame_id=shot.data["frame_id"]).code == "frame_context_changed"


def test_input_during_capture_makes_the_returned_frame_unusable_for_targeting(app,monkeypatch):
    actual=capture.capture
    def changed_capture(**kwargs):
        assert app.click(30,30,coordinate_mode='screen').ok
        return actual(**kwargs)
    monkeypatch.setattr(capture,'capture',changed_capture)
    result=app.screenshot(grid=False)
    assert result.ok and not result.data['coordinate_context_valid']
    assert app.click(20,20,frame_id=result.data['frame_id']).code=='stale_frame'


def test_capture_started_inside_input_is_not_published_as_valid(app,monkeypatch):
    shots=[]
    def click(*args,**kwargs):shots.append(app.screenshot(grid=False));return {}
    monkeypatch.setattr(input_ctl,'click',click)
    assert app.click(20,20,coordinate_mode='screen').ok
    assert shots[0].ok and not shots[0].data['coordinate_context_valid']
    assert app.press_key('enter',frame_id=shots[0].data['frame_id']).code=='stale_frame'


def test_failed_encoding_does_not_replace_the_last_successfully_delivered_frame(app,monkeypatch):
    old=app.screenshot(grid=False).data['frame_id']
    def fail(*args,**kwargs):raise capture.CaptureError('mock encoding failure')
    monkeypatch.setattr(capture,'encode',fail)
    assert not app.screenshot(grid=False).ok
    assert app.session.last_frame.frame_id==old


def test_window_change_during_capture_marks_observation_as_unusable(app,monkeypatch):
    calls=[{'handle':123,'pid':1},{'handle':456,'pid':2}]
    monkeypatch.setattr(windows,'get_foreground_window',lambda:calls.pop(0))
    result=app.screenshot(grid=False)
    assert result.ok and not result.data['coordinate_context_valid']
    assert app.click(20,20,frame_id=result.data['frame_id']).code=='stale_frame'


def test_stop_and_resume_invalidate_a_previous_observation(app):
    token=app.screenshot(grid=False).data['frame_id']
    app.emergency_stop('test');app.resume()
    assert app.click(20,20,frame_id=token).code=='stale_frame'


def test_per_axis_resize_rounding_and_normalized_endpoints_stay_inside_crop(app):
    value=frame();app.session.set_frame(value)
    result=app.click(200,200,coordinate_mode='image',frame_id=value.frame_id)
    assert result.data['screen_point']==[601,598]
    value=frame(origin=(100,50),size=(800,600),image=(400,300));app.session.set_frame(value)
    result=app.click(1,1,coordinate_mode='normalized',frame_id=value.frame_id)
    assert result.data['screen_point']==[899,649]
    assert value.image_to_screen(399.99,299.99)==(899,649)


def test_drag_keeps_both_endpoints_on_one_frame_during_concurrent_publication(app,monkeypatch):
    first=frame();app.session.set_frame(first);actual=app._resolve_point;calls=[]
    def resolve(*args,**kwargs):
        result=actual(*args,**kwargs);calls.append(result)
        if len(calls)==1:app.session.set_frame(frame(origin=(100,100)))
        return result
    monkeypatch.setattr(app,'_resolve_point',resolve)
    result=app.drag(10,10,20,20,frame_id=first.frame_id)
    assert result.ok and result.data['screen_from']==[30,30] and result.data['screen_to']==[60,60]


def test_negative_monitor_coordinates_and_virtual_desktop_gaps(app,monkeypatch):
    monkeypatch.setattr(platform_win,'get_virtual_screen',lambda:platform_win.VirtualScreen(-1000,0,2920,1080))
    monkeypatch.setattr(capture,'list_monitors',lambda:[capture.Monitor(0,-1000,0,2920,1080),
        capture.Monitor(1,-1000,0,900,600),capture.Monitor(2,0,0,1920,1080,True)])
    assert app.click(-500,200,coordinate_mode='screen').ok
    assert app.click(-500,800,coordinate_mode='screen').code=='out_of_bounds'
    assert app.click(-50,200,coordinate_mode='screen').code=='out_of_bounds'


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),True])
def test_nonfinite_or_boolean_coordinate_never_reaches_backend(app,fake_backend,bad):
    assert app.click(bad,20,coordinate_mode='screen').code=='bad_argument'
    assert fake_backend.calls==[]


@pytest.mark.parametrize('x,y',[(10,None),(None,20)])
def test_partial_scroll_location_is_rejected(app,fake_backend,x,y):
    assert app.scroll(2,x=x,y=y).code=='bad_argument'
    assert fake_backend.calls==[]


def test_keyboard_binding_checks_context_before_clipboard_or_key_input(app,monkeypatch,isolated_desktop,fake_backend):
    token=app.screenshot(grid=False).data['frame_id']
    monkeypatch.setattr(windows,'get_foreground_window',lambda:{'handle':456,'pid':2})
    assert app.type_text('private',frame_id=token).code=='frame_context_changed'
    assert isolated_desktop['clipboard']['text']=='' and fake_backend.calls==[]


def test_optional_binding_fields_are_consistent_in_both_tool_schemas():
    from wdc.mcp_server import build_server
    server=build_server(Config(audit_log=False))
    tools=asyncio.run(server.list_tools())
    for tool in tools:
        schema=getattr(tool,'inputSchema',None) or getattr(tool,'input_schema',None)
        exported=next(spec for spec in schemas.tool_specs() if spec['name']==tool.name)['parameters']
        assert set(schema['properties'])==set(exported['properties']),tool.name
    for name in ('activate_window','window_action'):
        props=next(spec for spec in schemas.tool_specs() if spec['name']==name)['parameters']['properties']
        assert props['timeout']['default']==2 and props['timeout']['maximum']==5


def test_sdk_call_uses_frame_binding_and_preserves_not_verified_outcome():
    from wdc.mcp_server import build_server
    server=build_server(Config(audit_log=False,max_actions_per_second=1000))
    def call(name,args):
        result=asyncio.run(server.call_tool(name,args));return result,json.loads(result.content[0].text)
    _,shot=call('screenshot',{'grid':False})
    result,payload=call('click',{'x':20,'y':20,'frame_id':shot['frame_id']})
    assert payload['ok'] and payload['verification']['status']=='not_verified'
    result,payload=call('click',{'x':20,'y':20,'frame_id':shot['frame_id']})
    assert not payload['ok'] and payload['error_code']=='stale_frame'
    assert getattr(result,'is_error',getattr(result,'isError',None)) is True


class WindowApi:
    def __init__(self):
        self.hwnd=0x100000007;self.pid=44;self.closed=False;self.foreground=0
        self.rect=(10,20,410,320);self.maximized=False;self.minimized=False;self.exstyle=0
        self.calls=[];self.apply=True;self.request_ok=True;self.auto_close=False
    def IsWindow(self,hwnd):return hwnd==self.hwnd and not self.closed
    def GetWindowThreadProcessId(self,hwnd,pointer):
        C.cast(pointer,C.POINTER(W.DWORD)).contents.value=self.pid;return 77
    def GetForegroundWindow(self):return self.foreground
    def GetWindowRect(self,hwnd,pointer):
        rect=C.cast(pointer,C.POINTER(W.RECT)).contents
        rect.left,rect.top,rect.right,rect.bottom=self.rect;return True
    def GetWindowLongW(self,hwnd,index):return self.exstyle
    def IsIconic(self,hwnd):return self.minimized
    def IsZoomed(self,hwnd):return self.maximized
    def IsWindowVisible(self,hwnd):return not self.closed
    def ShowWindow(self,hwnd,mode):
        self.calls.append(('show',hwnd,mode))
        if self.apply:self.maximized=mode==3;self.minimized=mode==6
        return False  # This is previous visibility, not a success/failure indicator.
    def PostMessageW(self,hwnd,*args):
        self.calls.append(('post',hwnd))
        if self.request_ok and self.auto_close:self.closed=True
        return self.request_ok
    def SetWindowPos(self,hwnd,order,x,y,w,h,flags):
        self.calls.append(('position',hwnd,flags))
        if self.apply and self.request_ok:
            if not flags&1:self.rect=(x,y,x+w,y+h)
            if order==-1:self.exstyle|=8
            if order==-2:self.exstyle&=~8
        return self.request_ok


@pytest.fixture
def api(monkeypatch):
    api=WindowApi();monkeypatch.setattr(windows,'user32',api)
    monkeypatch.setattr(windows,'window_action',REAL_WINDOW_ACTION);monkeypatch.setattr(windows,'activate_window',REAL_ACTIVATE)
    monkeypatch.setattr(windows,'_info',lambda hwnd,foreground:SimpleNamespace(to_dict=lambda:{'handle':hwnd,'pid':api.pid,'title':'Target'}))
    def focus(hwnd):
        api.calls.append(('focus',hwnd))
        if api.apply:api.foreground=hwnd
        return api.apply
    monkeypatch.setattr(windows,'_force_foreground',focus)
    return api


def test_ambiguous_and_blank_title_never_choose_first_window(app,api,monkeypatch):
    monkeypatch.setattr(windows,'list_windows',lambda **kwargs:[
        {'handle':api.hwnd,'title':'Editor','process':'editor.exe'},
        {'handle':123,'title':'Editor','process':'editor.exe'}])
    assert app.activate_window(title='Editor',timeout=0).code=='ambiguous_window'
    assert app.activate_window(title='   ',timeout=0).code=='bad_argument'
    assert api.calls==[]


def test_unique_exact_title_beats_less_specific_candidates(api,monkeypatch):
    monkeypatch.setattr(windows,'list_windows',lambda **kwargs:[
        {'handle':api.hwnd,'title':'Editor','process':'editor.exe'},
        {'handle':123,'title':'Editor - draft','process':'editor.exe'}])
    assert windows._match('Editor',None)==api.hwnd


def test_pid_mismatch_rejects_reused_handle_before_mutation(app,api):
    result=app.window_action('close',handle=api.hwnd,expected_pid=45,timeout=0)
    assert result.code=='window_identity_changed' and api.calls==[]


@pytest.mark.parametrize('action',['maximize','minimize','restore','focus','topmost','untopmost','move','resize'])
def test_window_outcomes_use_state_readback_not_previous_visibility(app,api,action):
    result=app.window_action(action,handle=api.hwnd,expected_pid=44,x=50,y=60,width=600,height=400,timeout=0)
    assert result.ok,result.text()
    assert result.data['verification']=={'status':'verified','method':'window_state_readback'}
    assert result.data['target']['handle']>2**32


def test_close_only_reports_success_when_the_original_window_is_gone(app,api):
    result=app.window_action('close',handle=api.hwnd,timeout=0)
    assert not result.ok and result.code=='verification_failed'
    assert result.data['sent'] and result.data['verification']['status']=='failed'
    assert len(api.calls)==1
    api.auto_close=True
    result=app.window_action('close',handle=api.hwnd,timeout=0)
    assert result.ok and 'window' not in result.data


@pytest.mark.parametrize('action',['close','move','topmost'])
def test_failed_window_request_is_not_reported_as_success(app,api,action):
    api.request_ok=False
    result=app.window_action(action,handle=api.hwnd,timeout=0)
    assert not result.ok and result.code=='window_request_failed'


def test_successful_send_with_no_state_change_fails_verification(app,api):
    api.apply=False
    result=app.window_action('maximize',handle=api.hwnd,timeout=0)
    assert not result.ok and result.code=='verification_failed' and result.data['sent']


def test_stop_during_window_verification_cancels_without_replaying(app,api,monkeypatch):
    clock=[0.0]
    monkeypatch.setattr(windows,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    def sleep(seconds):
        clock[0]+=seconds;app.emergency_stop('test');input_ctl.check_cancelled()
    monkeypatch.setattr(input_ctl,'cancellable_sleep',sleep)
    result=app.window_action('close',handle=api.hwnd,timeout=1)
    assert result.code=='emergency_stop' and len(api.calls)==1


def test_async_close_polls_without_resending_the_message(app,api,monkeypatch):
    clock=[0.0]
    monkeypatch.setattr(windows,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    def sleep(seconds):
        clock[0]+=seconds
        if clock[0]>=.1:api.closed=True
    monkeypatch.setattr(input_ctl,'cancellable_sleep',sleep)
    result=app.window_action('close',handle=api.hwnd,expected_pid=44,timeout=.2)
    assert result.ok and len(api.calls)==1 and clock[0]>=.1


def test_move_verification_uses_native_window_rect_not_dwm_visible_border(app,api,monkeypatch):
    monkeypatch.setattr(windows,'_rect',lambda hwnd:(18,28,402,312))
    result=app.window_action('move',handle=api.hwnd,x=50,y=60,timeout=0)
    assert result.ok and api.rect==(50,60,450,360)


@pytest.mark.parametrize('timeout',[float('nan'),-1,6,True])
def test_invalid_verification_budget_is_rejected_before_mutation(app,api,timeout):
    assert app.window_action('close',handle=api.hwnd,timeout=timeout).code=='bad_argument'
    assert api.calls==[]


def test_sdk_pointer_context_reaches_action_events_without_exposing_schema_argument(monkeypatch, capsys):
    from types import SimpleNamespace
    from wdc.mcp_server import build_server
    from wdc.mcp_compat import ToolContext
    monkeypatch.setenv('WDC_FEEDBACK_EVENTS','1')
    server=build_server(Config(audit_log=False,max_actions_per_second=1000))
    ctx=ToolContext(request_context=SimpleNamespace(request_id=902))
    result=asyncio.run(server.call_tool('click',{'x':20,'y':30,'coordinate_mode':'screen'},context=ctx))
    assert json.loads(result.content[0].text)['ok']
    packets=[json.loads(line[12:]) for line in capsys.readouterr().err.splitlines() if line.startswith('WDC_POINTER ')]
    assert packets and packets[0]['id']=='902' and packets[0]['point']==[20,30]
    assert set(packets[0])=={'id','sequence','point'}
