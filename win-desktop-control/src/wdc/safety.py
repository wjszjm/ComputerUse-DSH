"""安全护栏：只读模式、快捷键黑名单、坐标边界、速率限制、紧急停止、审计日志。

设计目标：让「模型幻觉 + 自动化输入」不至于造成不可逆破坏。
- 默认拒绝锁屏类快捷键（win+l 之后 Agent 自己回不来）
- 所有点击落点必须落在虚拟桌面内
- 审计日志默认不记录明文文本（只记长度 + 哈希前缀），避免泄露密码
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque

from . import input_ctl, platform_win
from .config import Config


class SafetyViolation(RuntimeError):
    """操作被安全策略拒绝。"""

    def __init__(self, message: str, code: str = "blocked") -> None:
        super().__init__(message)
        self.code = code


class EmergencyStop(RuntimeError):
    """已触发紧急停止。"""


STOP_FILE_ENV = "WDC_STOP_FILE"


@dataclass
class AuditRecord:
    action: str
    ok: bool
    params: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    duration_ms: float = 0.0

    def to_json(self) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds"),
            "action": self.action,
            "ok": self.ok,
            "duration_ms": round(self.duration_ms, 2),
            "params": self.params,
        }
        if self.error:
            payload["error"] = self.error
        return json.dumps(payload, ensure_ascii=False)


class SafetyGuard:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._lock = threading.RLock()
        self._timestamps: Deque[float] = deque(maxlen=256)
        self._stopped = False
        self._stop_generation = 0
        self._stop_reason = ""
        self._audit_failures = 0

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #
    @property
    def stopped(self) -> bool:
        return self._stopped or self._stop_file_present()

    @property
    def stop_reason(self) -> str:
        if self._stop_file_present() and not self._stopped:
            return "检测到停止文件"
        return self._stop_reason

    def _stop_file_present(self) -> bool:
        path = os.environ.get(STOP_FILE_ENV)
        if not path:
            return False
        try:
            Path(path).stat()
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return True  # 无法检查停止信号时禁止输入，不能当作没有停止。

    def stop(self, reason: str = "agent requested") -> dict[str, Any]:
        with self._lock:
            self._stopped = True
            self._stop_generation += 1
            self._stop_reason = reason
        self.audit(AuditRecord(action="emergency_stop", ok=True, params={"reason": reason}))
        return {"stopped": True, "reason": reason,
                "hint": "后续所有输入类工具都会返回 stopped=true；调用 resume 可恢复。"}

    def resume(self) -> dict[str, Any]:
        with self._lock:
            self._stopped = False
            self._stop_reason = ""
        return {"stopped": self.stopped}

    def checkpoint(self, action: str):
        """Bind cancellation to this action; resume must not resurrect cancelled work."""
        generation = self._stop_generation

        def check() -> None:
            if generation != self._stop_generation:
                raise EmergencyStop("操作已被停止；恢复只允许新操作，不继续旧操作。")
            self.require_input(action)
        return check

    # ------------------------------------------------------------------ #
    # 检查
    # ------------------------------------------------------------------ #
    def require_input(self, action: str) -> None:
        if self.config.read_only:
            raise SafetyViolation(
                f"当前为只读模式（config.read_only=true），禁止执行 {action}。"
                "把 config.json 中 read_only 改为 false 后重启服务。",
                code="read_only",
            )
        if not self.config.allow_input:
            raise SafetyViolation(
                f"输入总开关已关闭（config.allow_input=false），禁止执行 {action}。",
                code="input_disabled",
            )
        if self.stopped:
            raise EmergencyStop(f"已紧急停止（{self.stop_reason}），请先调用 resume。")
        if platform_win.is_secure_desktop_active():
            raise SafetyViolation(
                "当前处于锁屏 / UAC 安全桌面，模拟输入不会生效，请先解锁桌面。",
                code="secure_desktop",
            )

    def check_shortcut(self, chord: str) -> None:
        normalized = set(input_ctl.parse_chord(chord))
        for blocked in self.config.blocked_shortcuts:
            if set(input_ctl.parse_chord(blocked)).issubset(normalized):
                raise SafetyViolation(
                    f"快捷键 {chord} 在禁用列表中（会锁屏或触发系统保留界面，Agent 无法自行恢复）。"
                    "如确需使用，请修改 config.json 的 blocked_shortcuts。",
                    code="blocked_shortcut",
                )

    def check_point(self, x: float, y: float) -> tuple[int, int]:
        screen = platform_win.get_virtual_screen()
        ix, iy = int(round(float(x))), int(round(float(y)))
        if not (screen.left <= ix < screen.right and screen.top <= iy < screen.bottom):
            raise SafetyViolation(
                f"坐标 ({ix}, {iy}) 超出虚拟桌面范围 "
                f"[{screen.left},{screen.top} ~ {screen.right - 1},{screen.bottom - 1}]。",
                code="out_of_bounds",
            )
        return ix, iy

    def check_rate(self) -> None:
        limit = self.config.max_actions_per_second
        now = time.monotonic()
        with self._lock:
            self._timestamps.append(now)
            recent = [t for t in self._timestamps if now - t <= 1.0]
        if len(recent) > limit:
            raise SafetyViolation(
                f"操作频率超过限制（{limit:.0f} 次/秒），已限流。请加入 wait 降低频率。",
                code="rate_limited",
            )

    def redact_text(self, text: str) -> dict[str, Any]:
        """审计用的文本摘要：默认只留长度与哈希，绝不落明文（可能是密码）。"""
        text = text or ""
        info: dict[str, Any] = {"chars": len(text)}
        if self.config.audit_log:
            digest = hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()[:8]
            info["sha256_8"] = digest
            if self.config.record_text_preview and len(text) <= 200:
                preview = "".join(ch if ch.isprintable() else "·" for ch in text[:60])
                info["preview"] = preview
        return info

    # ------------------------------------------------------------------ #
    # 审计
    # ------------------------------------------------------------------ #
    def audit(self, record: AuditRecord) -> None:
        if not self.config.audit_log:
            return
        try:
            path = Path(self.config.audit_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._rotate_if_needed(path)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(record.to_json() + "\n")
        except Exception:
            # 审计失败绝不能影响主流程，但计数便于自检暴露问题
            self._audit_failures += 1

    @staticmethod
    def _rotate_if_needed(path: Path, max_bytes: int = 5 * 1024 * 1024) -> None:
        try:
            if path.exists() and path.stat().st_size > max_bytes:
                backup = path.with_suffix(path.suffix + ".1")
                if backup.exists():
                    backup.unlink()
                path.rename(backup)
        except Exception:
            pass

    @property
    def audit_failures(self) -> int:
        return self._audit_failures

    def status(self) -> dict[str, Any]:
        return {
            "read_only": self.config.read_only,
            "allow_input": self.config.allow_input,
            "allow_shell": self.config.allow_shell,
            "allow_launch": self.config.allow_launch,
            "stopped": self.stopped,
            "stop_reason": self.stop_reason,
            "max_actions_per_second": self.config.max_actions_per_second,
            "blocked_shortcuts": list(self.config.blocked_shortcuts),
            "audit_log": self.config.audit_log,
            "audit_path": self.config.audit_path if self.config.audit_log else "",
            "audit_failures": self._audit_failures,
        }
