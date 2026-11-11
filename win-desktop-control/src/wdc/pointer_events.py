"""Opt-in sanitized action positions; no global cursor watcher or input text."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import math
import os
import sys
import threading

_operation = ContextVar('pointer_operation', default=None)
_lock = threading.Lock()

@contextmanager
def operation(request_id):
    token = _operation.set({'id': str(request_id), 'sequence': 0} if request_id is not None else None)
    try:
        yield
    finally:
        _operation.reset(token)

def enabled():
    return os.environ.get('WDC_FEEDBACK_EVENTS') == '1' and _operation.get() is not None

def position(x, y):
    if not enabled(): return
    try:
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (x,y)): return
        item = _operation.get()
        item['sequence'] += 1
        packet = {'id': item['id'], 'sequence': item['sequence'], 'point': [x,y]}
        with _lock:
            sys.stderr.write('WDC_POINTER ' + json.dumps(packet, separators=(',', ':')) + '\n')
            sys.stderr.flush()
    except Exception:
        pass  # Feedback failure never changes input outcome.
