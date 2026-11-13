"""工具实现层：把截屏 / 输入 / 窗口 / 安全策略组装成 Agent 可调用的原子动作。

不依赖 MCP，可被 CLI、HTTP、单测直接调用。
"""

from __future__ import annotations

import base64
import json
import subprocess
import threading
import time
from dataclasses import dataclass, field
from contextlib import nullcontext
from typing import Any, Callable

from . import capture, desktop_targets, input_ctl, platform_win, stop_hotkey, window_capture, windows
from .config import Config, load_config
from .safety import AuditRecord, EmergencyStop, SafetyGuard, SafetyViolation
from .targeting import MAX_FRAME_AGE_SECONDS, monitor_signature, number, on_monitor, window_identity

COORDINATE_MODES = ("auto", "image", "screen", "normalized")


@dataclass
class ToolResult:
    ok: bool
    action: str
    data: dict[str, Any] = field(default_factory=dict)
    image: bytes | None = None
    mime: str = "image/png"
    error: str = ""
    code: str = ""

    def payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"ok": self.ok, "action": self.action}
        body.update(self.data)
        if self.error:
            body["error"] = self.error
        if self.code:
            body["error_code"] = self.code
        return body

    def text(self) -> str:
        return json.dumps(self.payload(), ensure_ascii=False)

    def image_b64(self) -> str:
        return base64.b64encode(self.image).decode("ascii") if self.image else ""


class Session:
    """一次会话内的可变状态（最近一帧截图、操作计数）。"""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.last_frame: capture.Frame | None = None
        self.action_count = 0
        self.started_at = time.time()
        self.revision = 0
        self.input_active = False

    def set_frame(self, frame: capture.Frame, expected_revision: int | None = None,
                  capture_during_input: bool = False) -> None:
        with self.lock:
            frame.revision = self.revision if expected_revision is None else expected_revision
            if capture_during_input or self.input_active or frame.revision != self.revision:
                frame.invalid_reason = 'capture_during_input'
            self.last_frame = frame

    def frame_snapshot(self):
        with self.lock:return self.last_frame, self.revision

    def bump(self) -> int:
        with self.lock:
            self.action_count += 1
            return self.action_count


class ComputerUse:
    def __init__(self, config: Config | None = None, guard: SafetyGuard | None = None) -> None:
        # 必须早于任何 GUI 库导入，否则高缩放屏幕上坐标会整体偏移
        platform_win.setup_dpi_awareness()
        self.config = config or load_config()
        self.guard = guard or SafetyGuard(self.config)
        self.session = Session()
        self._input_lock = threading.Lock()
        self._capture_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    def _finish(self, action: str, params: dict[str, Any], fn: Callable[[], Any],
                need_input: bool = False, audit_params: dict[str, Any] | None = None) -> ToolResult:
        start = time.perf_counter()
        result = ToolResult(False, action, {}, error="unset", code="internal_error")
        locked = False
        attempted = False
        checkpoint = self.guard.checkpoint(action) if need_input else None
        def check_input():
            if stop_hotkey.should_stop():
                self.guard.stop("stop hotkey")
                raise EmergencyStop("已通过紧急停止快捷键停止操作。")
            checkpoint()
        try:
            if need_input:
                check_input()
                locked = self._input_lock.acquire(blocking=False)
                if not locked:
                    raise SafetyViolation("前一个输入操作尚未结束。", "action_busy")
                self.guard.check_rate()
            context = input_ctl.cancellation(check_input) if need_input else nullcontext()
            if need_input:
                with self.session.lock:self.session.input_active = True
                attempted = True
            with context, (stop_hotkey.input_scope() if need_input else nullcontext()):
                data = fn() or {}
            if isinstance(data, ToolResult):
                result = data
            else:
                result = ToolResult(True, action, dict(data))
            if need_input and result.ok:
                result.data.setdefault('verification', {'status':'not_verified','method':'input_call_completed',
                    'next_step':'screenshot_or_target_state_readback'})
        except SafetyViolation as exc:
            result = ToolResult(False, action, {}, error=str(exc), code=exc.code)
        except EmergencyStop as exc:
            result = ToolResult(False, action, {}, error=str(exc), code="emergency_stop")
        except capture.CaptureError as exc:
            result = ToolResult(False, action, {}, error=str(exc), code="capture_failed")
        except input_ctl.InputError as exc:
            result = ToolResult(False, action, {}, error=str(exc), code="input_unavailable")
        except windows.WindowError as exc:
            result = ToolResult(False, action, {}, error=str(exc), code=exc.code)
        except ValueError as exc:
            result = ToolResult(False, action, {}, error=str(exc), code="bad_argument")
        except Exception as exc:  # noqa: BLE001 - 工具层必须兜底，不能让 server 崩
            result = ToolResult(False, action, {}, error=f"{type(exc).__name__}: {exc}",
                                code="internal_error")
        finally:
            if attempted:
                with self.session.lock:
                    self.session.input_active = False
                    self.session.revision += 1
            if locked:
                self._input_lock.release()
            duration = (time.perf_counter() - start) * 1000
            self.guard.audit(
                AuditRecord(
                    action=action,
                    ok=result.ok,
                    params=audit_params if audit_params is not None else params,
                    error=result.error,
                    duration_ms=duration,
                )
            )
        self.session.bump()
        return result

    def _require_frame(self, frame_id=None, snapshot=None, *, observation=False):
        frame, revision = snapshot or self.session.frame_snapshot()
        if frame is None:raise SafetyViolation('请先截图。', 'frame_required')
        if frame_id is not None and frame_id != frame.frame_id:
            raise SafetyViolation('frame_id 已被新截图替换，请重新观察。', 'frame_mismatch')
        age=max(time.monotonic()-frame.captured_monotonic,time.time()-frame.captured_at)
        if age<0 or age>MAX_FRAME_AGE_SECONDS:
            raise SafetyViolation('截图超过 30 秒有效期，请重新截图。', 'stale_frame')
        if frame.invalid_reason or frame.revision!=revision:
            raise SafetyViolation('截图期间或之后发生输入，请重新截图。', 'stale_frame')
        if frame.background:
            if not observation:
                raise SafetyViolation('后台截图仅用于观察，请激活窗口后重新截图再输入。', 'background_frame')
            handle, pid = frame.capture_target
            x, y = frame.screen_origin
            w, h = frame.screen_size
            if desktop_targets.target_window(handle, pid, foreground=False)[2] != (x, y, x+w, y+h):
                raise SafetyViolation('目标窗口位置已变化，请重新截图。', 'frame_context_changed')
        if frame.context_window is not None and frame.context_window!=window_identity(windows.get_foreground_window()):
            raise SafetyViolation('前台窗口身份或位置已变化，请重新截图。','frame_context_changed')
        if frame.context_layout is not None and frame.context_layout!=monitor_signature(capture.list_monitors()):
            raise SafetyViolation('显示器布局已变化，请重新截图。','frame_context_changed')
        return frame

    def _resolve_point(self, x: float, y: float, mode: str | None, frame_id=None, snapshot=None) -> tuple[int, int, str]:
        """Strict image targeting; only explicit screen mode bypasses frame context."""
        if mode is not None and not isinstance(mode,str):raise ValueError('coordinate_mode 必须是字符串。')
        mode = (mode or 'auto').lower()
        if mode not in COORDINATE_MODES:raise ValueError(f'coordinate_mode 只能是 {COORDINATE_MODES} 之一')
        x,y=number(x),number(y)
        snapshot=snapshot or self.session.frame_snapshot()
        frame,_=snapshot
        warning=''
        if mode=='screen':
            if frame_id is not None:raise ValueError('screen 模式不接受 frame_id。')
            px,py=self.guard.check_point(x,y)
        elif frame is not None or mode=='image' or frame_id is not None:
            frame=self._require_frame(frame_id,snapshot)
            if mode=='normalized':
                if not (0<=x<=1 and 0<=y<=1):raise ValueError('normalized 坐标必须在 0~1。')
                ox,oy=frame.screen_origin;w,h=frame.screen_size
                px,py=self.guard.check_point(ox+x*(w-1),oy+y*(h-1))
            else:
                if not frame.contains_image_point(x,y):raise SafetyViolation('图片坐标越界；屏幕坐标请显式使用 screen。','image_out_of_bounds')
                px,py=self.guard.check_point(*frame.image_to_screen(x,y))
            if frame.capture_target and not desktop_targets.window_at_point(frame.capture_target[0],px,py):
                raise SafetyViolation('截图坐标已被其他窗口遮挡。','frame_context_changed')
        elif mode=='normalized':
            if not (0<=x<=1 and 0<=y<=1):raise ValueError('normalized 坐标必须在 0~1。')
            screen=platform_win.get_virtual_screen()
            px,py=self.guard.check_point(screen.left+x*(screen.width-1),screen.top+y*(screen.height-1))
        else:
            warning='No screenshot yet; auto uses absolute screen pixels. Prefer an explicit screen mode or a fresh screenshot with frame_id.'
            px,py=self.guard.check_point(x,y)
        if not on_monitor(px,py,capture.list_monitors()):
            raise SafetyViolation('目标位于显示器之间的空白区域。','out_of_bounds')
        return px,py,warning

    # ------------------------------------------------------------------ #
    # 查询类
    # ------------------------------------------------------------------ #
    def screen_info(self) -> ToolResult:
        def _impl() -> dict[str, Any]:
            # 只回报影响决策的信息：坐标怎么换算、能不能操作、权限边界。
            # 机器名、python 版本、后端实现细节这些对操作没有影响，
            # 全量返回会让每次调用都白占一份上下文（原实现约 1,500 字符）。
            sess = platform_win.get_session_info()
            safety = self.guard.status()
            info: dict[str, Any] = {
                "screen": sess["virtual_screen"],
                "dpi_scale": sess["dpi_scale"],
                "dpi_awareness": sess["dpi_awareness"],
                "secure_desktop": sess["secure_desktop"],
                "elevated": sess["elevated"],
                "input_ok": bool(input_ctl.backend_status().available),
                "read_only": safety["read_only"],
                "allow_input": safety["allow_input"],
                "allow_shell": safety["allow_shell"],
                "max_image_edge": self.config.max_image_edge,
                "default_grid": self.config.default_grid,
            }
            try:
                info["monitors"] = [m.to_dict() for m in capture.list_monitors()]
            except Exception as exc:
                info["monitors"] = []
                info["monitors_error"] = str(exc)
            fg = windows.get_foreground_window()
            if fg:
                info["active_window"] = {"title": fg.get("title"), "process": fg.get("process")}
            if self.session.last_frame is not None:
                info['last_frame_id']=self.session.last_frame.frame_id
                info["last_frame_age_seconds"] = round(
                    time.time() - self.session.last_frame.captured_at, 1
                )
            return info

        return self._finish("screen_info", {}, _impl)

    # ------------------------------------------------------------------ #
    # 截图
    # ------------------------------------------------------------------ #
    def screenshot(
        self,
        monitor: str | int = "primary",
        region: list[int] | None = None,
        scale: float = 0.0,
        grid: bool | None = None,
        _target=None,
        _background=False,
    ) -> ToolResult:
        use_grid = self.config.default_grid if grid is None else bool(grid)
        # 用局部字典持有图片，避免并发截图时实例属性互相覆盖
        captured: dict[str, Any] = {}

        def _impl() -> dict[str, Any]:
            with self._capture_lock:return _capture_impl()

        def _capture_impl() -> dict[str, Any]:
            with self.session.lock:
                revision,active=self.session.revision,self.session.input_active
            foreground=None if _background else window_identity(windows.get_foreground_window())
            bounds_check={}
            if _background:
                bounds_check["grab_window"] = lambda: window_capture.grab(*_target)
            elif _target is not None:
                bounds_check["validate_pixel"] = lambda x,y: desktop_targets.window_at_point(_target[0],x,y)
            frame = capture.capture(
                monitor=monitor,
                region=region,
                scale=float(scale or 0.0),
                grid=use_grid,
                grid_step=self.config.grid_step,
                max_edge=self.config.max_image_edge,
                **bounds_check,
            )
            data, mime = capture.encode(
                frame, fmt=self.config.image_format, quality=self.config.jpeg_quality
            )
            frame.context_window=foreground
            frame.context_layout=monitor_signature(frame.monitors)
            if _target is not None:
                handle,pid,bounds=_target
                if desktop_targets.target_window(handle,pid,foreground=not _background)[2]!=bounds:
                    raise windows.WindowError("截图期间窗口已变化。", "window_identity_changed")
                frame.capture_target=(handle,pid)
                frame.background=_background
            if (not _background and foreground!=window_identity(windows.get_foreground_window())) or frame.context_layout!=monitor_signature(capture.list_monitors()):
                frame.invalid_reason='capture_context_changed'
            self.session.set_frame(frame,revision,active)
            meta = frame.to_dict()
            meta['coordinate_context_valid']=not frame.invalid_reason and frame.revision==self.session.revision
            # 只回传模型决策需要的字段：raw_size 可由 scale 推出，
            # captured_at 是服务端做时效判断用的，coordinate_space 由 hint 隐含。
            for redundant in ("captured_at", "raw_size", "coordinate_space"):
                meta.pop(redundant, None)
            meta["format"] = mime
            meta["bytes"] = len(data)
            meta["grid"] = use_grid
            if _target is not None:
                meta["capture_backend"] = "print_window" if _background else "desktop_region"
                meta["pointer_input"] = not _background
            if self.config.save_screenshots:
                stamp = time.strftime("%Y%m%d-%H%M%S")
                filename = f"{stamp}-{int(frame.captured_at * 1000) % 1000:03d}.{self.config.image_format}"
                try:
                    meta["saved_to"] = capture.save(
                        frame,
                        f"{self.config.screenshot_dir}/{filename}",
                        fmt=self.config.image_format,
                        quality=self.config.jpeg_quality,
                    )
                except Exception as exc:
                    meta["saved_error"] = str(exc)
            captured["data"] = data
            captured["mime"] = mime
            return meta

        result = self._finish("screenshot", {"monitor": str(monitor), "region": region,
                                             "scale": scale, "grid": use_grid}, _impl)
        if result.ok and captured.get("data"):
            result.image = captured["data"]
            result.mime = captured.get("mime", "image/png")
        return result

    # ------------------------------------------------------------------ #
    # 鼠标
    # ------------------------------------------------------------------ #
    def window_screenshot(self, handle: int, expected_pid: int, scale: float = 0.0, background: bool = False) -> ToolResult:
        try:
            _, _, bounds = desktop_targets.target_window(handle, expected_pid, foreground=not background)
            left, top, right, bottom = bounds
            result = self.screenshot(monitor="all",region=[left,top,right-left,bottom-top],scale=scale,grid=False,
                                     _target=(handle,expected_pid,bounds), _background=background)
            result.action="window_screenshot"
            return result
        except (SafetyViolation, windows.WindowError, ValueError) as exc:
            return ToolResult(False,"window_screenshot",error=str(exc),code=getattr(exc,"code","bad_argument"))

    def inspect_controls(self, handle, expected_pid, name="", control_type="", limit=40, background=False):
        return self._finish("inspect_controls",{},lambda:desktop_targets.uia_controls(
            handle,expected_pid,name=name,control_type=control_type,limit=limit,background=background,
        ))

    def control_action(self, handle, expected_pid, control_id, operation, text="", background=False):
        def run():
            result=desktop_targets.uia_controls(handle,expected_pid,select=(control_id,operation,text),background=background)
            if result["verification"]["status"] == "failed":
                return ToolResult(False,"control_action",result,error="控件操作结果未通过读回核验。",code="verification_failed")
            return result
        return self._finish("control_action",{},run,need_input=True)

    def recognize_text(self, frame_id, language="en-US"):
        def run():
            if platform_win.is_secure_desktop_active():
                raise SafetyViolation("安全桌面不允许识别。","secure_desktop")
            frame=self._require_frame(frame_id, observation=True)
            words=desktop_targets.ocr_words(frame.image,language)
            self._require_frame(frame_id, observation=True)
            return {"words":words,"frame_id":frame_id,"coordinate_space":"image"}
        return self._finish("recognize_text",{},run)

    def locate_target(self, handle, expected_pid, name, frame_id=None, language="en-US", background=False):
        def run():
            if not name.strip() or len(name)>160:
                raise ValueError("请提供准确的控件名称或文字。")
            try:
                controls=desktop_targets.uia_controls(handle,expected_pid,name=name,limit=2,background=background)
                matches=controls["controls"]
            except SafetyViolation as exc:
                if exc.code!="uia_unavailable":
                    raise
                matches=[]
                controls={"truncated":False}
            if len(matches)>1:
                raise SafetyViolation("有多个同名控件，请进一步限定。","target_ambiguous")
            if controls["truncated"]:
                raise SafetyViolation("控件查询未能遍历完整目标，无法确认唯一性。","control_search_incomplete")
            if matches:
                return {"route":"uia","control":matches[0]}
            if frame_id is None:
                raise SafetyViolation("请先调用窗口截图并传入 frame_id。","frame_required")
            _,_,bounds=desktop_targets.target_window(handle,expected_pid,foreground=not background)
            frame=self._require_frame(frame_id, observation=True)
            if frame.capture_target!=(handle,expected_pid) or frame.screen_origin!=bounds[:2] or frame.screen_size!=(bounds[2]-bounds[0],bounds[3]-bounds[1]):
                raise SafetyViolation("OCR 回退需要该窗口的截图。","frame_context_changed")
            words=desktop_targets.ocr_words(frame.image,language)
            self._require_frame(frame_id, observation=True)
            matches=desktop_targets.text_match(words,name)
            if len(matches)!=1:
                raise SafetyViolation("文字目标不存在或不唯一。","target_ambiguous" if matches else "target_not_found")
            x,y=matches[0]
            if not frame.contains_image_point(x,y):
                raise SafetyViolation("识别结果不在截图范围内。","image_out_of_bounds")
            return {"route":"ocr","frame_id":frame_id,"image_point":[x,y],"pointer_input":not frame.background}
        return self._finish("locate_target",{},run)

    def batch_windows(self, steps):
        """Only actions with native readback may continue a batch."""
        allowed={"focus","maximize","minimize","restore","move","resize","topmost","untopmost"}
        if not 1<=len(steps)<=6:
            return ToolResult(False,"batch_windows",error="批次支持 1 至 6 步。",code="bad_argument")
        keys={"action","handle","expected_pid","x","y","width","height"}
        for step in steps:
            if not isinstance(step,dict) or set(step)-keys or step.get("action") not in allowed or any(
                type(step.get(key)) is not int or step[key]<=0 for key in ("handle","expected_pid")
            ):
                return ToolResult(False,"batch_windows",error="批次包含不支持或缺少目标身份的动作。",code="bad_argument")
            geometry=set(step)&{"x","y","width","height"}
            required={"move":{"x","y"},"resize":{"width","height"}}.get(step["action"],set())
            if (geometry!=required or any(type(step[key]) is not int for key in geometry) or
                    any(step[key]<=0 for key in geometry&{"width","height"})):
                return ToolResult(False,"batch_windows",error="批次中的窗口坐标或尺寸无效。",code="bad_argument")
        if len({(step["handle"],step["expected_pid"]) for step in steps})!=1:
            return ToolResult(False,"batch_windows",error="一个批次只能操作同一个窗口。",code="bad_argument")
        def run():
            completed = []
            for index, step in enumerate(steps, start=1):
                progress = {"completed": completed, "failed_step": index}
                try:
                    input_ctl.check_cancelled()
                    if index > 1:
                        self.guard.check_rate()
                    result = self._window_result("window_action", windows.window_action(**step))
                except EmergencyStop as exc:
                    return ToolResult(False, "batch_windows", progress, error=str(exc), code="emergency_stop")
                except (SafetyViolation, windows.WindowError) as exc:
                    return ToolResult(False, "batch_windows", progress, error=str(exc), code=exc.code)
                if not result.ok or result.data.get("verification", {}).get("status") != "verified":
                    progress["step_error"] = result.code or "verification_failed"
                    return ToolResult(False, "batch_windows", progress,
                                      error="批次已停止，请重新观察。", code="verification_failed")
                completed.append({"step": index, "action": step["action"], "verified": True})
            return {"completed": completed, "verification": {"status": "verified", "method": "window_readback"}}

        return self._finish("batch_windows", {}, run, need_input=True)

    def click(
        self,
        x: float,
        y: float,
        button: str = "left",
        clicks: int = 1,
        coordinate_mode: str = "auto",
        modifiers: str = "",
        frame_id: str | None = None,
    ) -> ToolResult:
        def _impl() -> dict[str, Any]:
            px, py, warning = self._resolve_point(x, y, coordinate_mode, frame_id)
            data: dict[str, Any] = {}
            parsed = input_ctl.parse_chord(modifiers) if modifiers else []
            backend = input_ctl.get_backend()
            if parsed:
                self.guard.check_shortcut(modifiers)
            with input_ctl.held_keys(backend, parsed):
                data.update(input_ctl.click(px, py, button=button, clicks=clicks))
            data.update({
                "screen_point": [px, py],
                "requested_point": [x, y],
                "coordinate_mode": coordinate_mode,
            })
            if parsed:
                data["modifiers"] = parsed
            if warning:
                data["warning"] = warning
            return data

        return self._finish(
            "click", {"x": x, "y": y, "button": button, "clicks": clicks,
                      "coordinate_mode": coordinate_mode, "modifiers": modifiers, "frame_id":frame_id},
            _impl, need_input=True,
        )

    def move(self, x: float, y: float, coordinate_mode: str = "auto",
             duration: float | None = None, frame_id: str | None = None) -> ToolResult:
        def _impl() -> dict[str, Any]:
            px, py, warning = self._resolve_point(x, y, coordinate_mode, frame_id)
            data = input_ctl.move(px, py, duration=self.config.move_duration if duration is None else float(duration))
            data.update({"screen_point": [px, py], "coordinate_mode": coordinate_mode})
            if warning:
                data["warning"] = warning
            return data

        return self._finish("move", {"x": x, "y": y, "coordinate_mode": coordinate_mode, "frame_id":frame_id},
                            _impl, need_input=True)

    def drag(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        coordinate_mode: str = "auto",
        duration: float = 0.5,
        button: str = "left",
        frame_id: str | None = None,
    ) -> ToolResult:
        def _impl() -> dict[str, Any]:
            snapshot=self.session.frame_snapshot()
            sx1, sy1, w1 = self._resolve_point(x1, y1, coordinate_mode, frame_id, snapshot)
            sx2, sy2, w2 = self._resolve_point(x2, y2, coordinate_mode, frame_id, snapshot)
            data = input_ctl.drag(sx1, sy1, sx2, sy2, duration=float(duration), button=button)
            data.update({"screen_from": [sx1, sy1], "screen_to": [sx2, sy2],
                         "coordinate_mode": coordinate_mode})
            warning = "; ".join(w for w in (w1, w2) if w)
            if warning:
                data["warning"] = warning
            return data

        return self._finish(
            "drag", {"x1": x1, "y1": y1, "x2": x2, "y2": y2,
                     "coordinate_mode": coordinate_mode, "button": button, "frame_id":frame_id},
            _impl, need_input=True,
        )

    def scroll(
        self,
        amount: int,
        x: float | None = None,
        y: float | None = None,
        coordinate_mode: str = "auto",
        horizontal: bool = False,
        frame_id: str | None = None,
    ) -> ToolResult:
        def _impl() -> dict[str, Any]:
            point: tuple[int, int] | None = None
            warning = ""
            if (x is None)!=(y is None):raise ValueError('滚动位置必须同时提供 x/y。')
            if x is not None and y is not None:
                px, py, warning = self._resolve_point(x, y, coordinate_mode, frame_id)
                point = (px, py)
            elif frame_id is not None:self._require_frame(frame_id)
            data = input_ctl.scroll(
                int(amount),
                x=point[0] if point else None,
                y=point[1] if point else None,
                horizontal=bool(horizontal),
            )
            data["direction"] = ("up" if amount > 0 else "down") if not horizontal else (
                "right" if amount > 0 else "left"
            )
            if warning:
                data["warning"] = warning
            return data

        return self._finish(
            "scroll", {"amount": amount, "x": x, "y": y, "horizontal": horizontal, "frame_id":frame_id},
            _impl, need_input=True,
        )

    # ------------------------------------------------------------------ #
    # 键盘
    # ------------------------------------------------------------------ #
    def type_text(self, text: str, press_enter: bool = False,
                  use_clipboard: str = "always", frame_id: str | None = None) -> ToolResult:
        """输入文本。

        use_clipboard 默认 "always"：走剪贴板粘贴。

        为什么不用 "auto"：auto 对纯 ASCII 且较短的文本走逐字输入，而中文输入法
        激活时会把半角标点转成全角（"7,006,652" → "7，006，652"）。这种失败是
        静默的——工具返回 ok、字符数也正确，只有内容错了。剪贴板粘贴不经过输入法，
        不改变字符。逐字输入的唯一优势是不占用剪贴板，代价不对等。
        确需逐字输入时显式传 use_clipboard="never"。
        """
        audit = {"text": self.guard.redact_text(text or ""), "press_enter": bool(press_enter)}

        def _impl() -> dict[str, Any]:
            if frame_id is not None:self._require_frame(frame_id)
            return input_ctl.type_text(
                text,
                interval=self.config.type_interval,
                press_enter=bool(press_enter),
                use_clipboard=use_clipboard,
            )

        return self._finish("type_text", {"chars": len(text or "")}, _impl,
                            need_input=True, audit_params=audit)

    def press_key(self, keys: str, repeat: int = 1, hold_ms: int = 0, frame_id: str | None = None) -> ToolResult:
        def _impl() -> dict[str, Any]:
            if frame_id is not None:self._require_frame(frame_id)
            self.guard.check_shortcut(keys)
            return input_ctl.press_key(keys, repeat=repeat, hold_ms=hold_ms)

        return self._finish("press_key", {"keys": keys, "repeat": repeat, "hold_ms": hold_ms},
                            _impl, need_input=True)

    def clipboard_get(self) -> ToolResult:
        return self._finish("clipboard_get", {}, lambda: {"text": input_ctl.clipboard_get()})

    def clipboard_set(self, text: str) -> ToolResult:
        return self._finish("clipboard_set", {"chars": len(text or "")},
                            lambda: input_ctl.clipboard_set(text),
                            need_input=True,
                            audit_params={"chars": len(text or "")})

    # ------------------------------------------------------------------ #
    # 窗口 / 进程
    # ------------------------------------------------------------------ #
    def list_windows(self, include_hidden: bool = False, limit: int = 50) -> ToolResult:
        return self._finish(
            "list_windows", {"include_hidden": include_hidden, "limit": limit},
            lambda: {"windows": windows.list_windows(include_hidden=include_hidden, limit=limit),
                     "foreground": windows.get_foreground_window()},
        )

    def _window_result(self, action, res):
        return ToolResult(bool(res.get('ok')),action,
            {key:value for key,value in res.items() if key not in {'ok','action','error','error_code'}},
            error=res.get('error',''),code=res.get('error_code',''))

    def activate_window(self, title: str | None = None, handle: int | None = None,
                        expected_pid: int | None = None, timeout: float = 2.0) -> ToolResult:
        def _impl() -> ToolResult:
            return self._window_result('activate_window',windows.activate_window(
                title=title,handle=handle,expected_pid=expected_pid,timeout=timeout))

        return self._finish("activate_window", {"title": title, "handle": handle, "expected_pid":expected_pid},
                            _impl, need_input=True)

    def window_action(
        self,
        action: str,
        title: str | None = None,
        handle: int | None = None,
        x: int | None = None,
        y: int | None = None,
        width: int | None = None,
        height: int | None = None,
        expected_pid: int | None = None,
        timeout: float = 2.0,
    ) -> ToolResult:
        def _impl() -> ToolResult:
            res = windows.window_action(action, title=title, handle=handle,
                                        x=x, y=y, width=width, height=height,
                                        expected_pid=expected_pid,timeout=timeout)
            return self._window_result('window_action',res)

        return self._finish(
            "window_action", {"action": action, "title": title, "handle": handle},
            _impl, need_input=True,
        )

    def launch_app(self, path: str, args: str = "", wait: float = 0.8) -> ToolResult:
        def _impl() -> dict[str, Any]:
            if not self.config.allow_launch:
                raise SafetyViolation("launch_app 已被配置禁用（allow_launch=false）。", "launch_disabled")
            res = windows.launch_app(path, args=args, wait=wait)
            if not res.get("ok"):
                raise ValueError(res.get("error", "启动失败"))
            return res

        return self._finish("launch_app", {"path": path, "args": args}, _impl, need_input=True)

    def run_command(self, command: str, timeout: float = 15.0,
                    cwd: str | None = None) -> ToolResult:
        def _impl() -> dict[str, Any]:
            if not self.config.allow_shell:
                raise SafetyViolation(
                    "run_command 默认禁用（allow_shell=false）。若确需执行命令，"
                    "请在 config.json 中把 allow_shell 设为 true 后重启服务。",
                    "shell_disabled",
                )
            started = time.perf_counter()
            completed = subprocess.run(
                command, shell=True, capture_output=True, timeout=float(timeout),
                cwd=cwd, text=True, errors="replace",
            )
            return {
                "returncode": completed.returncode,
                "stdout": (completed.stdout or "")[-8000:],
                "stderr": (completed.stderr or "")[-4000:],
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            }

        return self._finish("run_command", {"command": command}, _impl, need_input=True)

    # ------------------------------------------------------------------ #
    # 杂项
    # ------------------------------------------------------------------ #
    def wait(self, ms: int = 500) -> ToolResult:
        delay = max(0, min(int(ms), 30000)) / 1000.0
        time.sleep(delay)
        return ToolResult(True, "wait", {"waited_ms": int(delay * 1000)})

    def emergency_stop(self, reason: str = "agent requested") -> ToolResult:
        data=self.guard.stop(reason)
        with self.session.lock:self.session.revision+=1
        return ToolResult(True, "emergency_stop", data)

    def resume(self) -> ToolResult:
        data=self.guard.resume()
        with self.session.lock:self.session.revision+=1
        return ToolResult(True, "resume", data)

    # ------------------------------------------------------------------ #
    def diagnostics(self) -> dict[str, Any]:
        status = input_ctl.backend_status()
        return {
            "input": {"available": status.available, "backend": status.backend, "detail": status.detail},
            "session": platform_win.get_session_info(),
            "safety": self.guard.status(),
            "config_path": "config.json",
        }
