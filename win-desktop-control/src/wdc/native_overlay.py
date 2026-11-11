"""Own-process Win32 layered windows. Importing this module never creates a window."""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import os
import sys

from .overlay_scene import Tile

WS_EX_LAYERED=0x80000
WS_EX_TRANSPARENT=0x20
WS_EX_NOACTIVATE=0x08000000
WS_EX_TOOLWINDOW=0x80
OVERLAY_EX_STYLE=WS_EX_LAYERED|WS_EX_TRANSPARENT|WS_EX_NOACTIVATE|WS_EX_TOOLWINDOW
WS_POPUP=0x80000000
SWP_NOACTIVATE=0x10
SWP_SHOWWINDOW=0x40
WDA_EXCLUDEFROMCAPTURE=0x11
WM_CLOSE=0x10
WM_TIMER=0x113
WM_APP_UPDATE=0x8001
HTTRANSPARENT=-1
MA_NOACTIVATE=3
WNDPROC=getattr(C,'WINFUNCTYPE',C.CFUNCTYPE)(C.c_ssize_t,W.HWND,W.UINT,W.WPARAM,W.LPARAM)


class WNDCLASSEX(C.Structure):
    _fields_=[('cbSize',W.UINT),('style',W.UINT),('lpfnWndProc',WNDPROC),('cbClsExtra',C.c_int),('cbWndExtra',C.c_int),
              ('hInstance',W.HINSTANCE),('hIcon',W.HANDLE),('hCursor',W.HANDLE),('hbrBackground',W.HBRUSH),
              ('lpszMenuName',W.LPCWSTR),('lpszClassName',W.LPCWSTR),('hIconSm',W.HANDLE)]


class MONITORINFO(C.Structure):
    _fields_=[('cbSize',W.DWORD),('rcMonitor',W.RECT),('rcWork',W.RECT),('dwFlags',W.DWORD)]


class BITMAPINFOHEADER(C.Structure):
    _fields_=[('biSize',W.DWORD),('biWidth',W.LONG),('biHeight',W.LONG),('biPlanes',W.WORD),('biBitCount',W.WORD),
              ('biCompression',W.DWORD),('biSizeImage',W.DWORD),('biXPelsPerMeter',W.LONG),('biYPelsPerMeter',W.LONG),
              ('biClrUsed',W.DWORD),('biClrImportant',W.DWORD)]


class BITMAPINFO(C.Structure):
    _fields_=[('bmiHeader',BITMAPINFOHEADER),('bmiColors',W.DWORD*1)]


class BLENDFUNCTION(C.Structure):
    _fields_=[('BlendOp',C.c_ubyte),('BlendFlags',C.c_ubyte),('SourceConstantAlpha',C.c_ubyte),('AlphaFormat',C.c_ubyte)]


class OverlayUnavailable(RuntimeError):
    pass


class WindowsApi:
    """Bind pointer-sized results explicitly; no input/focus APIs are imported."""
    def __init__(self):
        if sys.platform!='win32' or sys.getwindowsversion().build<19041:
            raise OverlayUnavailable('capture_exclusion_requires_windows_2004')
        self.user=C.WinDLL('user32',use_last_error=True)
        self.gdi=C.WinDLL('gdi32',use_last_error=True)
        self.kernel=C.WinDLL('kernel32',use_last_error=True)
        bindings={
            'GetModuleHandleW':(self.kernel,W.HINSTANCE,[W.LPCWSTR]),
            'SetProcessDpiAwarenessContext':(self.user,W.BOOL,[C.c_void_p]),
            'GetThreadDpiAwarenessContext':(self.user,C.c_void_p,[]),
            'AreDpiAwarenessContextsEqual':(self.user,W.BOOL,[C.c_void_p,C.c_void_p]),
            'RegisterClassExW':(self.user,W.ATOM,[C.POINTER(WNDCLASSEX)]),
            'UnregisterClassW':(self.user,W.BOOL,[W.LPCWSTR,W.HINSTANCE]),
            'CreateWindowExW':(self.user,W.HWND,[W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,C.c_int,C.c_int,C.c_int,C.c_int,W.HWND,W.HMENU,W.HINSTANCE,C.c_void_p]),
            'DestroyWindow':(self.user,W.BOOL,[W.HWND]),
            'DefWindowProcW':(self.user,C.c_ssize_t,[W.HWND,W.UINT,W.WPARAM,W.LPARAM]),
            'SetWindowDisplayAffinity':(self.user,W.BOOL,[W.HWND,W.DWORD]),
            'GetWindowDisplayAffinity':(self.user,W.BOOL,[W.HWND,C.POINTER(W.DWORD)]),
            'SetWindowPos':(self.user,W.BOOL,[W.HWND,W.HWND,C.c_int,C.c_int,C.c_int,C.c_int,W.UINT]),
            'ShowWindow':(self.user,W.BOOL,[W.HWND,C.c_int]),
            'GetSystemMetrics':(self.user,C.c_int,[C.c_int]),
            'MonitorFromPoint':(self.user,W.HANDLE,[W.POINT,W.DWORD]),
            'GetMonitorInfoW':(self.user,W.BOOL,[W.HANDLE,C.POINTER(MONITORINFO)]),
            'GetDpiForWindow':(self.user,W.UINT,[W.HWND]),
            'GetDC':(self.user,W.HDC,[W.HWND]),
            'ReleaseDC':(self.user,C.c_int,[W.HWND,W.HDC]),
            'UpdateLayeredWindow':(self.user,W.BOOL,[W.HWND,W.HDC,C.POINTER(W.POINT),C.POINTER(W.SIZE),W.HDC,C.POINTER(W.POINT),W.DWORD,C.POINTER(BLENDFUNCTION),W.DWORD]),
            'CreateCompatibleDC':(self.gdi,W.HDC,[W.HDC]),
            'DeleteDC':(self.gdi,W.BOOL,[W.HDC]),
            'CreateDIBSection':(self.gdi,W.HBITMAP,[W.HDC,C.POINTER(BITMAPINFO),W.UINT,C.POINTER(C.c_void_p),W.HANDLE,W.DWORD]),
            'SelectObject':(self.gdi,W.HANDLE,[W.HDC,W.HANDLE]),
            'DeleteObject':(self.gdi,W.BOOL,[W.HANDLE]),
            'PostMessageW':(self.user,W.BOOL,[W.HWND,W.UINT,W.WPARAM,W.LPARAM]),
            'PostQuitMessage':(self.user,None,[C.c_int]),
            'SetTimer':(self.user,C.c_size_t,[W.HWND,C.c_size_t,W.UINT,C.c_void_p]),
            'KillTimer':(self.user,W.BOOL,[W.HWND,C.c_size_t]),
            'GetMessageW':(self.user,W.BOOL,[C.POINTER(W.MSG),W.HWND,W.UINT,W.UINT]),
            'TranslateMessage':(self.user,W.BOOL,[C.POINTER(W.MSG)]),
            'DispatchMessageW':(self.user,C.c_ssize_t,[C.POINTER(W.MSG)]),
        }
        for name,(library,result,args) in bindings.items():
            fn=getattr(library,name);fn.restype=result;fn.argtypes=args;setattr(self,name,fn)


class NativeWindows:
    def __init__(self, api=None, on_message=lambda event:None):
        self.api=api or WindowsApi()
        self.on_message=on_message
        self.windows=[]
        self.closed=False
        self.registered=False
        self.class_name=f'WDCFeedback-{os.getpid()}'
        self.instance=self.api.GetModuleHandleW(None)
        self.callback=WNDPROC(self._wndproc)
        context=C.c_void_p(-4)
        if not self.api.SetProcessDpiAwarenessContext(context) and not self.api.AreDpiAwarenessContextsEqual(self.api.GetThreadDpiAwarenessContext(),context):
            raise OverlayUnavailable('dpi_awareness_unavailable')
        cls=WNDCLASSEX();cls.cbSize=C.sizeof(cls);cls.lpfnWndProc=self.callback
        cls.hInstance=self.instance;cls.lpszClassName=self.class_name
        if not self.api.RegisterClassExW(C.byref(cls)):
            raise OverlayUnavailable('window_class_unavailable')
        self.registered=True
        try:
            for _ in range(3):
                hwnd=self.api.CreateWindowExW(OVERLAY_EX_STYLE,self.class_name,'DSH 操作提示',WS_POPUP,0,0,1,1,None,None,self.instance,None)
                if not hwnd:raise OverlayUnavailable('window_creation_failed')
                self.windows.append(hwnd)
                # Never show a window unless exclusion succeeds on that exact HWND.
                if not self.api.SetWindowDisplayAffinity(hwnd,WDA_EXCLUDEFROMCAPTURE):
                    raise OverlayUnavailable('capture_exclusion_unavailable')
                self.require_exclusion(hwnd)
            if not self.api.SetTimer(self.windows[0],1,250,None):
                raise OverlayUnavailable('watchdog_unavailable')
        except Exception:
            self.close();raise

    def _wndproc(self,hwnd,message,wparam,lparam):
        if message==0x84:return HTTRANSPARENT  # WM_NCHITTEST
        if message==0x21:return MA_NOACTIVATE  # WM_MOUSEACTIVATE
        try:
            if message==WM_CLOSE:self.on_message('close');return 0
            if message==WM_APP_UPDATE:self.on_message('update');return 0
            if message==WM_TIMER:self.on_message('watchdog');return 0
            if message==0x2E0:
                if self.windows and hwnd==self.windows[0]:self.on_message('geometry')
                return 0  # Ring tiles remain in physical pixels on other monitors.
            if message in {0x7E,0x1A}:self.on_message('geometry')  # Display/work-area changes
            if message==2 and self.windows and hwnd==self.windows[0]:self.api.PostQuitMessage(0);return 0
        except Exception:
            self.hide_all();self.api.PostQuitMessage(1);return 0
        return self.api.DefWindowProcW(hwnd,message,wparam,lparam)

    def geometry(self):
        x,y,w,h=[self.api.GetSystemMetrics(index) for index in (76,77,78,79)]
        info=MONITORINFO();info.cbSize=C.sizeof(info)
        monitor=self.api.MonitorFromPoint(W.POINT(0,0),1)
        if w<=0 or h<=0 or not monitor or not self.api.GetMonitorInfoW(monitor,C.byref(info)):
            raise OverlayUnavailable('geometry_unavailable')
        rect=info.rcWork
        viewport={'x':x,'y':y,'width':w,'height':h}
        work={'x':rect.left,'y':rect.top,'width':rect.right-rect.left,'height':rect.bottom-rect.top}
        if work['width']<=0 or work['height']<=0 or work['x']<x or work['y']<y or work['x']+work['width']>x+w or work['y']+work['height']>y+h:
            raise OverlayUnavailable('invalid_work_area')
        dpi=self.api.GetDpiForWindow(self.windows[0])
        if dpi<72 or dpi>768:raise OverlayUnavailable('dpi_unavailable')
        return {'viewport':viewport,'workArea':work,'uiScale':min(3,max(.75,dpi/96)),'captureExcluded':True}

    def paint(self, hwnd, tile: Tile):
        self.require_exclusion(hwnd)
        width,height=tile.image.size
        data=tile.bgra()
        if width<1 or height<1 or width*height>262144 or len(data)!=width*height*4:
            raise OverlayUnavailable('invalid_bitmap')
        dc=self.api.GetDC(None)
        if not dc:raise OverlayUnavailable('screen_dc_unavailable')
        memory=bitmap=old=None
        try:
            memory=self.api.CreateCompatibleDC(dc)
            if not memory:raise OverlayUnavailable('memory_dc_unavailable')
            info=BITMAPINFO();info.bmiHeader=BITMAPINFOHEADER(C.sizeof(BITMAPINFOHEADER),width,-height,1,32,0,0,0,0,0,0)
            bits=C.c_void_p()
            bitmap=self.api.CreateDIBSection(memory,C.byref(info),0,C.byref(bits),None,0)
            if not bitmap or not bits.value:raise OverlayUnavailable('bitmap_unavailable')
            old=self.api.SelectObject(memory,bitmap)
            if not old or old==C.c_void_p(-1).value:raise OverlayUnavailable('bitmap_selection_failed')
            C.memmove(bits,data,len(data))
            dst=W.POINT(tile.x,tile.y);size=W.SIZE(width,height);src=W.POINT(0,0);blend=BLENDFUNCTION(0,0,255,1)
            if not self.api.UpdateLayeredWindow(hwnd,dc,C.byref(dst),C.byref(size),memory,C.byref(src),0,C.byref(blend),2):
                raise OverlayUnavailable('layered_update_failed')
            if not self.api.SetWindowPos(hwnd,C.c_void_p(-1),tile.x,tile.y,width,height,SWP_NOACTIVATE|SWP_SHOWWINDOW):
                raise OverlayUnavailable('window_position_failed')
        finally:
            if old and old!=C.c_void_p(-1).value:self.api.SelectObject(memory,old)
            if bitmap:self.api.DeleteObject(bitmap)
            if memory:self.api.DeleteDC(memory)
            self.api.ReleaseDC(None,dc)

    def require_exclusion(self, hwnd):
        affinity=W.DWORD()
        if not self.api.GetWindowDisplayAffinity(hwnd,C.byref(affinity)) or affinity.value!=WDA_EXCLUDEFROMCAPTURE:
            raise OverlayUnavailable('capture_exclusion_unavailable')

    def render(self, tiles):
        try:
            for hwnd,tile in zip(self.windows,tiles):
                if tile is None:self.api.ShowWindow(hwnd,0)
                else:self.paint(hwnd,tile)
        except Exception:
            self.hide_all();raise

    def hide_all(self):
        for hwnd in self.windows:self.api.ShowWindow(hwnd,0)

    def wake(self):
        if self.windows and not self.closed:self.api.PostMessageW(self.windows[0],WM_APP_UPDATE,0,0)

    def request_close(self):
        if self.windows and not self.closed:self.api.PostMessageW(self.windows[0],WM_CLOSE,0,0)

    def loop(self):
        msg=W.MSG()
        while not self.closed:
            status=self.api.GetMessageW(C.byref(msg),None,0,0)
            if status==0:break
            if status<0:raise OverlayUnavailable('message_loop_failed')
            self.api.TranslateMessage(C.byref(msg));self.api.DispatchMessageW(C.byref(msg))

    def close(self):
        if self.closed:return
        self.closed=True
        if self.windows:self.api.KillTimer(self.windows[0],1)
        for hwnd in reversed(self.windows):
            self.api.ShowWindow(hwnd,0);self.api.DestroyWindow(hwnd)
        self.windows=[]
        if self.registered:self.api.UnregisterClassW(self.class_name,self.instance);self.registered=False
