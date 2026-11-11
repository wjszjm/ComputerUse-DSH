"""Opt-in display worker: NDJSON stdin, fixed acknowledgements stdout, no input tools."""
from __future__ import annotations

import json
import queue
import sys
import threading
import time

from .native_overlay import NativeWindows
from .overlay_scene import MAX_PACKET, render_tiles, validate_scene


class OverlayController:
    def __init__(self, backend, emit, clock=time.monotonic):
        self.backend=backend;self.emit=emit;self.clock=clock
        self.last_seen=clock();self.last_sequence=0;self.closed=False
        self.needs_refresh=False
        self.geometry=backend.geometry()
        if self.geometry.get('captureExcluded') is not True:raise ValueError('capture_exclusion_unavailable')

    def ready(self):
        self.emit({'type':'ready',**self.geometry})

    def receive(self, packet):
        if self.closed:return
        if not isinstance(packet,dict):raise ValueError('invalid_packet')
        sequence=packet.get('sequence')
        if type(sequence) is not int or not 0<sequence<2**53:raise ValueError('invalid_sequence')
        if sequence<=self.last_sequence:return  # Never refresh a stale watchdog/scene.
        kind=packet.get('type')
        if kind not in {'scene','heartbeat','close'}:raise ValueError('invalid_packet_type')
        self.last_seen=self.clock();self.last_sequence=sequence
        if kind=='close':self.close();return
        if kind=='scene':
            try:
                scene=validate_scene(packet['scene'],self.geometry['viewport'],self.geometry['workArea'])
            except ValueError as error:
                if str(error)=='stale_geometry':
                    self.geometry_changed(force=True);return
                raise
            self.backend.render(render_tiles(scene,self.geometry['viewport']))
            self.needs_refresh=False
        elif self.needs_refresh:self.emit({'type':'refresh'})
        self.emit({'type':'ack','sequence':sequence})

    def geometry_changed(self, force=False):
        if self.closed:return
        geometry=self.backend.geometry()
        if geometry==self.geometry and not force:return
        self.backend.hide_all()
        self.needs_refresh=True
        self.geometry=geometry
        self.emit({'type':'geometry',**self.geometry})
        # Acknowledge the discarded old geometry packet so the sender can send a fresh scene.
        if self.last_sequence:self.emit({'type':'ack','sequence':self.last_sequence})

    def watchdog(self):
        if self.closed:return
        age=self.clock()-self.last_seen
        if age>2.5:self.backend.hide_all();self.needs_refresh=True
        if age>5:self.close()

    def close(self):
        if self.closed:return
        self.closed=True;self.backend.close()


def run(backend_factory=NativeWindows, stdin=None, stdout=None):
    stdin=stdin or sys.stdin;stdout=stdout or sys.stdout
    def emit(value):
        stdout.write(json.dumps(value,ensure_ascii=False)+'\n');stdout.flush()
    backend=controller=None
    pending=queue.Queue(maxsize=1)
    def dispatch(event):
        if controller is None:return
        try:
            if event=='close':controller.close()
            elif event=='watchdog':controller.watchdog()
            elif event=='geometry':controller.geometry_changed()
            elif event=='update':
                try:packet=pending.get_nowait()
                except queue.Empty:return
                controller.receive(packet)
        except Exception:
            emit({'type':'disabled','reason':'render_failed'})
            controller.close()
    try:
        backend=backend_factory(on_message=dispatch)
        controller=OverlayController(backend,emit)
        controller.ready()
        def reader():
            try:
                while not controller.closed:
                    line=stdin.readline(MAX_PACKET+1)
                    if not line:break
                    if len(line.encode('utf-8'))>MAX_PACKET or not line.endswith('\n'):break
                    packet=json.loads(line)
                    pending.put_nowait(packet)
                    backend.wake()
            except Exception:
                pass
            finally:backend.request_close()
        threading.Thread(target=reader,name='overlay-stdin',daemon=True).start()
        backend.loop()
        return 0
    except Exception:
        try:emit({'type':'disabled','reason':'native_overlay_unavailable'})
        except Exception:pass
        return 1
    finally:
        if controller:controller.close()
        elif backend:backend.close()


if __name__=='__main__':
    for stream in (sys.stdout,sys.stderr):
        try:stream.reconfigure(encoding='utf-8')
        except Exception:pass
    raise SystemExit(run())
