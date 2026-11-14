"""Read-only UIA subprocess and watchdog adapter."""
from __future__ import annotations

import json

from . import desktop_targets, input_ctl, windows
from .read_worker import ReadWorker, ReadWorkerError, serve
from .safety import SafetyViolation


class UiaReader:
    def __init__(self):
        self.worker = ReadWorker("wdc.uia_reader",128*1024)

    def query(self, handle, pid, *, cancelled=None, timeout=3.0, **filters):
        def check():
            input_ctl.check_cancelled()
            if cancelled is not None and cancelled():
                raise SafetyViolation("控件读取已取消。","operation_cancelled")
        try:
            packet = self.worker.call({"handle":handle,"pid":pid,**filters},timeout,check)
            reply = json.loads(packet)
        except (OSError,ValueError,ReadWorkerError) as exc:
            raise SafetyViolation("UI Automation 读取超时或服务已退出，可重新观察。","uia_read_unavailable") from exc
        if "error_code" in reply:
            code = reply["error_code"]
            if code.startswith("window_"):
                raise windows.WindowError(reply["error"],code)
            raise SafetyViolation(reply["error"],code)
        return reply


def _read(request):
    try:
        fields = {key:request[key] for key in ("name","control_type","limit","background") if key in request}
        result = desktop_targets.uia_controls(request["handle"],request["pid"],cached=True,**fields)
    except (SafetyViolation,windows.WindowError,ValueError) as exc:
        result = {"error":str(exc),"error_code":getattr(exc,"code","bad_argument")}
    except Exception:
        result = {"error":"UI Automation 无法读取目标控件。","error_code":"uia_read_unavailable"}
    return json.dumps(result,ensure_ascii=False,separators=(",",":")).encode("utf-8")


if __name__ == "__main__":
    serve(_read)
