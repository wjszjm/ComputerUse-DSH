import struct
import subprocess
import sys
from pathlib import Path
from threading import Event

import pytest
from wdc import window_capture
from wdc.capture import CaptureError
from wdc.read_worker import ReadWorker, ReadWorkerError
from wdc.safety import SafetyViolation


def test_watchdog_reuses_healthy_reader_and_retires_only_hung_reader(monkeypatch):
    source = Path(__file__).resolve().parents[1] / "src"
    fixture = (
        f"import sys; sys.path.insert(0,{str(source)!r}); "
        "from time import sleep; from wdc.read_worker import serve; "
        "serve(lambda command: sleep(10) if command == 'hang' else b'ok')"
    )
    spawn = subprocess.Popen.__mro__[1]  # The sandbox blocks any process except the explicit fixture below.
    command = [sys.executable,"-I","-B","-c",fixture]

    def fixture_popen(args, **kwargs):
        assert args == command
        return spawn(args,**kwargs)

    monkeypatch.setattr(subprocess,"Popen",fixture_popen)
    worker = ReadWorker(None,32,command)
    independent = ReadWorker(None,32,command)
    try:
        assert worker.call("one",3) == b"ok"
        original = worker.process
        assert worker.call("two",1) == b"ok" and worker.process is original
        assert independent.call("other",3) == b"ok"
        with pytest.raises(ReadWorkerError,match="deadline"):
            worker.call("hang",0.1)
        assert original.poll() is not None
        assert independent.call("still healthy",1) == b"ok"
        assert worker.call("recovered",3) == b"ok" and worker.process is not original
    finally:
        worker._retire()
        independent._retire()


def test_background_capture_fallback_crops_in_worker_and_respects_protection(monkeypatch,tmp_path):
    calls = []
    bounds = -400,15,600,1015
    region = -300,70,2,1
    monkeypatch.setattr(window_capture,"_eligible",lambda *args: calls.append(("eligible",args)))
    helper = tmp_path / "capture-helper"
    helper.touch()
    monkeypatch.setattr(window_capture,"_wgc_path",helper)
    first = b""  # The PrintWindow worker rejected the whole surface before cropping.
    second = struct.pack("<II",2,1)+bytes([0,0,240,0,0,140,0,0])

    def print_window(request, timeout, check):
        calls.append(("print",request))
        assert timeout < 3
        check()
        return first

    def wgc(request, timeout, check):
        calls.append(("wgc",request))
        check()
        return second

    monkeypatch.setattr(window_capture._print_worker,"call",print_window)
    monkeypatch.setattr(window_capture._wgc_worker,"call",wgc)
    image = window_capture.grab(1,2,bounds,region)
    assert image.size == (2,1) and image.getpixel((1,0)) == (0,140,0)
    assert image.info["capture_backend"] == "windows_graphics_capture"
    assert calls[1][1][-1] == (-300,70,-298,71)
    assert calls[3][1].split()[-4:] == ["-300","70","-298","71"]
    cancel = Event()
    cancel.set()
    prior = len(calls)
    with pytest.raises(SafetyViolation,match="截图已取消"):
        window_capture.grab(1,2,bounds,region,cancel)
    assert len(calls) == prior
    monkeypatch.setattr(window_capture,"_eligible",lambda *_: (_ for _ in ()).throw(CaptureError("protected")))
    with pytest.raises(CaptureError,match="protected"):
        window_capture.grab(1,2,bounds,region)
    assert len(calls) == prior
