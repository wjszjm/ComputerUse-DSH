"""No real windows: fake Win32 handles, Pillow buffers and protocol controller only."""
import ctypes as C
from ctypes import wintypes as W
import copy
import io
import json
import queue

from PIL import Image
import pytest

from wdc.native_overlay import (NativeWindows, OverlayUnavailable, OVERLAY_EX_STYLE, WS_POPUP,
    SWP_NOACTIVATE, SWP_SHOWWINDOW, WDA_EXCLUDEFROMCAPTURE, MONITORINFO, BITMAPINFO, BLENDFUNCTION,
    WM_CLOSE, HTTRANSPARENT, MA_NOACTIVATE)
from wdc.overlay_host import OverlayController, run
from wdc.overlay_scene import Tile, validate_scene, render_tiles

VIEW={'x':-1280,'y':0,'width':3200,'height':1080}
WORK={'x':0,'y':0,'width':1920,'height':1040}


class FakeApi:
    def __init__(self, fail_affinity=0, fail_update=False):
        self.calls=[];self.next=0x100000000;self.fail_affinity=fail_affinity;self.fail_update=fail_update;self.buffers=[]
    def GetModuleHandleW(self,arg):return 0x100000001
    def SetProcessDpiAwarenessContext(self,arg):return True
    def RegisterClassExW(self,arg):self.calls.append(('register',));return 1
    def UnregisterClassW(self,*args):self.calls.append(('unregister',));return True
    def CreateWindowExW(self,style,name,title,window_style,*args):
        assert style==OVERLAY_EX_STYLE and window_style==WS_POPUP
        self.next+=1;self.calls.append(('create',self.next,style));return self.next
    def SetWindowDisplayAffinity(self,hwnd,affinity):
        assert affinity==WDA_EXCLUDEFROMCAPTURE
        self.calls.append(('exclude',hwnd));return len([c for c in self.calls if c[0]=='exclude'])!=self.fail_affinity
    def GetWindowDisplayAffinity(self,hwnd,pointer):
        C.cast(pointer,C.POINTER(W.DWORD)).contents.value=17;return True
    def SetTimer(self,*args):self.calls.append(('timer',));return 1
    def KillTimer(self,*args):self.calls.append(('kill_timer',));return True
    def ShowWindow(self,hwnd,mode):self.calls.append(('show',hwnd,mode));return False
    def DestroyWindow(self,hwnd):self.calls.append(('destroy',hwnd));return True
    def DefWindowProcW(self,*args):return 1
    def PostQuitMessage(self,code):self.calls.append(('quit',code))
    def PostMessageW(self,*args):self.calls.append(('post',*args));return True
    def GetSystemMetrics(self,index):return {76:-1280,77:0,78:3200,79:1080}[index]
    def MonitorFromPoint(self,*args):return 123
    def GetMonitorInfoW(self,monitor,pointer):
        info=C.cast(pointer,C.POINTER(MONITORINFO)).contents;info.rcWork=W.RECT(0,0,1920,1040);return True
    def GetDpiForWindow(self,hwnd):return 144
    def GetDC(self,*args):self.calls.append(('get_dc',));return 200
    def ReleaseDC(self,*args):self.calls.append(('release_dc',));return 1
    def CreateCompatibleDC(self,*args):self.calls.append(('create_dc',));return 300
    def DeleteDC(self,*args):self.calls.append(('delete_dc',));return True
    def CreateDIBSection(self,dc,info,usage,bits,*args):
        header=C.cast(info,C.POINTER(BITMAPINFO)).contents.bmiHeader
        assert header.biHeight<0 and header.biBitCount==32
        buffer=C.create_string_buffer(header.biWidth*(-header.biHeight)*4);self.buffers.append(buffer)
        C.cast(bits,C.POINTER(C.c_void_p)).contents.value=C.addressof(buffer)
        self.calls.append(('bitmap',));return 400
    def SelectObject(self,dc,obj):self.calls.append(('select',obj));return 500
    def DeleteObject(self,obj):self.calls.append(('delete_object',obj));return True
    def UpdateLayeredWindow(self,hwnd,dc,dst,size,memory,src,key,blend,flags):
        b=C.cast(blend,C.POINTER(BLENDFUNCTION)).contents
        assert b.AlphaFormat==1 and b.SourceConstantAlpha==255 and flags==2
        self.calls.append(('update',hwnd));return not self.fail_update
    def SetWindowPos(self,hwnd,order,x,y,width,height,flags):
        assert flags&SWP_NOACTIVATE and flags&SWP_SHOWWINDOW
        self.calls.append(('position',hwnd,x,y));return True


def scene():
    return {'visible':True,'geometryKnown':True,'viewport':VIEW,'state':'clicking','label':'UNTRUSTED TEXT',
            'badge':{'x':1700,'y':980,'width':170,'height':34,'compact':False,'scale':1,'opacity':.88},
            'rings':[{'x':-1000,'y':200,'radius':17,'strokeWidth':2,'opacity':.6}]}


def test_window_flags_exclusion_and_no_focus_calls():
    api=FakeApi();native=NativeWindows(api=api)
    try:
        assert len(native.windows)==3 and min(native.windows)>2**32
        assert len([c for c in api.calls if c[0]=='exclude'])==3
        assert not any(c[0] in {'show','position'} for c in api.calls)
        assert native._wndproc(native.windows[0],0x84,0,0)==HTTRANSPARENT
        assert native._wndproc(native.windows[0],0x21,0,0)==MA_NOACTIVATE
        assert native.geometry()['uiScale']==1.5
        assert native.geometry()['workArea']==WORK
        assert C.sizeof(C.c_ssize_t)==C.sizeof(C.c_void_p)
    finally:native.close()
    assert len([c for c in api.calls if c[0]=='destroy'])==3
    assert api.calls[-1][0]=='unregister'


@pytest.mark.parametrize('failed_window',[1,2,3])
def test_capture_exclusion_failure_never_shows_a_window(failed_window):
    api=FakeApi(fail_affinity=failed_window)
    with pytest.raises(OverlayUnavailable,match='capture_exclusion'):
        NativeWindows(api=api)
    assert not any(c[0]=='position' or c[0]=='show' and c[2]!=0 for c in api.calls)
    assert len([c for c in api.calls if c[0]=='destroy'])==failed_window


def test_capture_exclusion_readback_mismatch_cleans_up_hidden_window():
    api=FakeApi()
    api.GetWindowDisplayAffinity=lambda hwnd,pointer:True  # Success with affinity zero is insufficient.
    with pytest.raises(OverlayUnavailable,match='capture_exclusion'):NativeWindows(api=api)
    assert [c[0] for c in api.calls].count('destroy')==1
    assert not any(c[0]=='position' for c in api.calls)


def test_capture_exclusion_lost_before_paint_hides_without_allocating_or_updating():
    api=FakeApi();native=NativeWindows(api=api)
    api.GetWindowDisplayAffinity=lambda hwnd,pointer:False
    try:
        with pytest.raises(OverlayUnavailable,match='capture_exclusion'):
            native.render([Tile(0,0,Image.new('RGBA',(2,2))),None,None])
        assert not any(c[0] in {'get_dc','bitmap','update','position'} for c in api.calls)
        assert len([c for c in api.calls if c[0]=='show' and c[2]==0])==3
    finally:native.close()


@pytest.mark.parametrize('failure',[False,True])
def test_bitmap_premultiplied_alpha_and_gdi_cleanup(failure):
    api=FakeApi(fail_update=failure);native=NativeWindows(api=api)
    tile=Tile(-20,40,Image.new('RGBA',(2,2),(100,50,25,128)))
    assert tile.bgra()[:4]==bytes([12,25,50,128])
    try:
        if failure:
            with pytest.raises(OverlayUnavailable):native.render([tile,None,None])
            assert not any(c[0]=='position' for c in api.calls)
        else:native.render([tile,None,None])
        assert api.buffers[0].raw[:4]==tile.bgra()[:4]
        for action in ['delete_object','delete_dc','release_dc']:
            assert len([c for c in api.calls if c[0]==action])==1
    finally:native.close()


def test_scene_uses_fixed_labels_and_small_tiles_preserve_negative_screen_points():
    clean=validate_scene(scene(),VIEW,WORK)
    assert 'label' not in clean
    tiles=render_tiles(clean,VIEW)
    assert len(tiles)==3 and tiles[0].image.width==170 and tiles[2] is None
    ring=tiles[1];assert ring.x<-1000 and ring.image.width<64
    assert ring.image.getpixel((round(-1000-ring.x),round(200-ring.y)))[3]==0


@pytest.mark.parametrize('change',[lambda s:s.update(state='PRIVATE'),lambda s:s['badge'].update(width=5000),
    lambda s:s['badge'].update(y=1050),lambda s:s['rings'][0].update(radius=float('nan')),
    lambda s:s.update(rings=s['rings']*3)])
def test_invalid_scene_is_rejected_before_rendering(change):
    value=copy.deepcopy(scene());change(value)
    with pytest.raises((ValueError,KeyError,TypeError)):validate_scene(value,VIEW,WORK)


class Backend:
    def __init__(self):self.calls=[];self.closed=False
    def geometry(self):return {'viewport':VIEW,'workArea':WORK,'uiScale':1.5,'captureExcluded':True}
    def render(self,tiles):self.calls.append(('render',tiles))
    def hide_all(self):self.calls.append(('hide',))
    def close(self):self.closed=True;self.calls.append(('close',))


def test_protocol_stale_sequence_watchdog_and_recovery_request():
    backend=Backend();output=[];now=[0.0];control=OverlayController(backend,output.append,lambda:now[0])
    control.ready();control.receive({'type':'scene','sequence':1,'scene':scene()})
    assert output[-1]=={'type':'ack','sequence':1}
    control.receive({'type':'scene','sequence':1,'scene':{'invalid':True}})
    assert len([c for c in backend.calls if c[0]=='render'])==1
    now[0]=3;control.watchdog();assert backend.calls[-1][0]=='hide'
    control.receive({'type':'heartbeat','sequence':2});assert {'type':'refresh'} in output
    now[0]=9;control.watchdog();assert backend.closed


def test_stale_geometry_hides_and_requests_fresh_scene_instead_of_drawing():
    backend=Backend();output=[];control=OverlayController(backend,output.append)
    value=scene();value['viewport']={'x':0,'y':0,'width':100,'height':100}
    control.receive({'type':'scene','sequence':1,'scene':value})
    assert backend.calls==[('hide',)]
    assert output[0]['type']=='geometry' and output[1]['type']=='ack'


@pytest.mark.parametrize('sequence',[True,0,2**53])
def test_invalid_sequence_does_not_refresh_watchdog_or_draw(sequence):
    backend=Backend();output=[];now=[0.0];control=OverlayController(backend,output.append,lambda:now[0])
    now[0]=1
    with pytest.raises(ValueError,match='invalid_sequence'):
        control.receive({'type':'scene','sequence':sequence,'scene':scene()})
    assert control.last_seen==0 and backend.calls==[] and output==[]


@pytest.mark.parametrize('packet',[{'type':'heartbeat','sequence':1}, {'type':'invalid','sequence':1}])
def test_worker_pipe_dispatch_and_eof_cleanup_use_only_in_memory_backend(packet):
    class WorkerBackend(Backend):
        def __init__(self,on_message):
            super().__init__();self.on_message=on_message;self.events=queue.Queue()
        def wake(self):self.events.put('update')
        def request_close(self):self.events.put('close')
        def loop(self):
            while not self.closed:self.on_message(self.events.get(timeout=2))
    workers=[]
    def factory(**kwargs):
        backend=WorkerBackend(**kwargs);workers.append(backend);return backend
    output=io.StringIO()
    assert run(factory,io.StringIO(json.dumps(packet)+'\n'),output)==0
    messages=[json.loads(line) for line in output.getvalue().splitlines()]
    assert messages[0]['type']=='ready' and workers[0].closed
    assert messages[1]['type']==('ack' if packet['type']=='heartbeat' else 'disabled')
    assert len([c for c in workers[0].calls if c[0]=='close'])==1


def test_test_sandbox_blocks_real_window_api_creation():
    with pytest.raises(RuntimeError,match='blocked'):NativeWindows()


def test_black_arrow_tile_is_bounded_at_negative_origin_and_right_edge():
    for x,y in [(-1280,0),(1919,1079)]:
        raw={'visible':True,'geometryKnown':True,'viewport':VIEW,'state':'clicking','badge':None,'rings':[],
             'pointer':{'x':x,'y':y,'scale':1.5}}
        clean=validate_scene(raw,VIEW,WORK)
        tile=render_tiles(clean,VIEW)[2]
        assert tile.x==x and tile.y==y
        assert tile.x+tile.image.width<=1920 and tile.y+tile.image.height<=1080
        assert tile.image.getchannel('A').getbbox() is not None
    with pytest.raises(ValueError):
        validate_scene({**raw,'pointer':{'x':float('nan'),'y':0,'scale':1}},VIEW,WORK)
