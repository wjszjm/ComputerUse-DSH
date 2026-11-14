"""Bounded stdio for owned, read-only workers. EOF also stops an idle orphan."""
from __future__ import annotations

import json
import os
import queue
import struct
import subprocess
import sys
import threading
import time


class ReadWorkerError(RuntimeError):
    pass


def _receive(stream, maximum):
    header = stream.read(4)
    if not header:
        raise EOFError
    size, = struct.unpack("<I",header)
    if size > maximum:
        raise ValueError("worker packet too large")
    data = stream.read(size)
    if len(data) != size:
        raise EOFError
    return data


def _send(stream, data):
    stream.write(struct.pack("<I",len(data)))
    stream.write(data)
    stream.flush()


class ReadWorker:
    def __init__(self, module, maximum, command=None, parent_argument=False):
        self.command = command or [sys.executable,"-I","-B","-m",module]
        self.maximum = maximum
        self.parent_argument = parent_argument
        self.lock = threading.Lock()
        self.process = None
        self.replies = None
        self.retiring = False

    def _start(self):
        command = [*self.command,str(os.getpid())] if self.parent_argument else self.command
        process = subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        replies = queue.Queue(maxsize=1)
        self.process,self.replies = process,replies
        self.retiring = False

        def read():
            try:
                while True:
                    packet = _receive(process.stdout,self.maximum)
                    replies.put_nowait(packet)
            except (OSError,EOFError,ValueError,struct.error,queue.Full):
                try:
                    replies.put_nowait(None)
                except queue.Full:
                    pass
            finally:
                process.stdout.close()

        threading.Thread(target=read,daemon=True).start()

    def _retire(self):
        process,self.process = self.process,None
        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()
            except OSError:
                pass
            finally:
                process.stdin.close()
            try:
                process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                # Keep the handle so a second worker cannot overlap this one.
                self.process = process
                self.retiring = True

    def call(self, request, timeout, check=lambda: None):
        deadline = time.monotonic()+timeout
        while not self.lock.acquire(timeout=min(0.03,max(0,deadline-time.monotonic()))):
            check()
            if time.monotonic() >= deadline:
                raise ReadWorkerError("worker busy")
        try:
            check()
            if time.monotonic() >= deadline:
                raise ReadWorkerError("worker busy")
            payload = json.dumps(request,separators=(",",":")).encode("utf-8")
            if len(payload) > 2048:
                raise ValueError("worker request too large")
            try:
                if self.process is not None and self.process.poll() is not None:
                    self._retire()
                if self.process is not None and self.retiring:
                    raise ReadWorkerError("worker has not terminated")
                if self.process is None:
                    self._start()
                # One in-flight request and a payload smaller than the pipe buffer.
                _send(self.process.stdin,payload)
                while True:
                    check()
                    remaining = deadline-time.monotonic()
                    if remaining <= 0:
                        raise ReadWorkerError("worker deadline exceeded")
                    try:
                        reply = self.replies.get(timeout=min(remaining,0.03))
                    except queue.Empty:
                        continue
                    if reply is None:
                        raise ReadWorkerError("worker exited")
                    check()
                    return reply
            except BaseException:
                self._retire()
                raise
        finally:
            self.lock.release()


def serve(handler):
    """Input reader can terminate even while the provider is blocked in native code."""
    requests = queue.Queue(maxsize=1)
    def read():
        try:
            while True:
                requests.put_nowait(json.loads(_receive(sys.stdin.buffer,2048)))
        except (EOFError,OSError,ValueError,struct.error,queue.Full):
            os._exit(0)

    threading.Thread(target=read,daemon=True).start()
    while True:
        _send(sys.stdout.buffer,handler(requests.get()))
