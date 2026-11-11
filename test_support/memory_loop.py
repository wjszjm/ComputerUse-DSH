"""Async tests without Windows' loopback socketpair; all fd I/O is rejected.

The managed sandbox cannot complete asyncio's native loopback self-pipe setup.
An Event wakes the test loop for call_soon_threadsafe, including AnyIO workers.
This is a test-only scheduler, never a transport or desktop backend.
"""
import asyncio
import selectors
import threading


class MemorySelector(selectors.BaseSelector):
    def __init__(self): self.wakeup = threading.Event()
    def register(self, *args, **kwargs): raise RuntimeError('Socket/file descriptor I/O forbidden in memory loop')
    def unregister(self, *args, **kwargs): raise KeyError(args)
    def select(self, timeout=None):
        self.wakeup.wait(timeout)
        self.wakeup.clear()
        return []
    def get_map(self): return {}
    def close(self): self.wakeup.set()


class MemoryLoop(asyncio.SelectorEventLoop):
    def __init__(self): super().__init__(selector=MemorySelector())
    def _make_self_pipe(self): pass
    def _close_self_pipe(self): pass
    def _write_to_self(self): self._selector.wakeup.set()


class MemoryPolicy(asyncio.DefaultEventLoopPolicy):
    def new_event_loop(self): return MemoryLoop()
