from __future__ import annotations

import json

import pytest

from wdc.config import Config
from wdc.safety import AuditRecord, EmergencyStop, SafetyGuard, SafetyViolation


def make_guard(tmp_path, **kwargs):
    cfg = Config(audit_path=str(tmp_path / "audit.log"), screenshot_dir=str(tmp_path / "shots"),
                 max_actions_per_second=5, **kwargs)
    return SafetyGuard(cfg)


def test_read_only_blocks(tmp_path):
    guard = make_guard(tmp_path, read_only=True)
    with pytest.raises(SafetyViolation) as exc:
        guard.require_input("click")
    assert exc.value.code == "read_only"


def test_blocked_shortcut(tmp_path):
    guard = make_guard(tmp_path)
    with pytest.raises(SafetyViolation) as exc:
        guard.check_shortcut("Win+L")
    assert exc.value.code == "blocked_shortcut"
    guard.check_shortcut("ctrl+c")  # 正常快捷键不拦


def test_blocked_shortcut_prefix(tmp_path):
    guard = make_guard(tmp_path)
    with pytest.raises(SafetyViolation):
        guard.check_shortcut("win+l+shift")


def test_point_out_of_bounds(tmp_path):
    guard = make_guard(tmp_path)
    with pytest.raises(SafetyViolation) as exc:
        guard.check_point(-99999, -99999)
    assert exc.value.code == "out_of_bounds"
    assert guard.check_point(10, 10) == (10, 10)


def test_rate_limit(tmp_path):
    guard = make_guard(tmp_path)
    for _ in range(5):
        guard.check_rate()
    with pytest.raises(SafetyViolation) as exc:
        guard.check_rate()
    assert exc.value.code == "rate_limited"


def test_emergency_stop_blocks_input(tmp_path):
    guard = make_guard(tmp_path)
    guard.stop("test")
    assert guard.stopped is True
    with pytest.raises(EmergencyStop):
        guard.require_input("click")
    guard.resume()
    assert guard.stopped is False
    guard.require_input("click")


def test_stop_file(tmp_path, monkeypatch):
    stop = tmp_path / "STOP"
    monkeypatch.setenv("WDC_STOP_FILE", str(stop))
    guard = make_guard(tmp_path)
    guard.require_input("click")
    stop.write_text("1")
    with pytest.raises(EmergencyStop):
        guard.require_input("click")


def test_audit_written_and_redacted(tmp_path):
    guard = make_guard(tmp_path)
    guard.audit(AuditRecord(action="type_text", ok=True,
                            params={"text": guard.redact_text("hunter2-秘密")}))
    lines = (tmp_path / "audit.log").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["action"] == "type_text"
    assert record["params"]["text"]["chars"] == 10
    assert "sha256_8" in record["params"]["text"]
    assert "preview" not in record["params"]["text"]   # 默认不落明文


def test_audit_rotate(tmp_path):
    guard = make_guard(tmp_path)
    log = tmp_path / "audit.log"
    log.write_text("x" * (6 * 1024 * 1024), encoding="utf-8")
    guard.audit(AuditRecord(action="click", ok=True))
    assert (tmp_path / "audit.log.1").exists()
    assert log.exists() and log.stat().st_size < 1024 * 1024


def test_status_snapshot(tmp_path):
    status = make_guard(tmp_path).status()
    for key in ("read_only", "allow_input", "stopped", "audit_log", "blocked_shortcuts"):
        assert key in status
