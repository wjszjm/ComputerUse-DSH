from __future__ import annotations

import json


from wdc import selftest
from wdc.config import Config


def test_summarize_counts():
    checks = [
        selftest.Check("a", "A", "pass", "-"),
        selftest.Check("b", "B", "warn", "-"),
        selftest.Check("c", "C", "fail", "-"),
        selftest.Check("d", "D", "skip", "-"),
    ]
    counts = selftest.summarize(checks)
    assert counts == {"pass": 1, "warn": 1, "fail": 1, "skip": 1, "total": 4}


def test_format_text_contains_conclusion():
    checks = [selftest.Check("a", "检查项", "pass", "一切正常")]
    text = selftest.format_text(checks, selftest.summarize(checks))
    assert "全部通过" in text
    assert "检查项" in text


def test_format_text_with_failure():
    checks = [selftest.Check("a", "坏项", "fail", "炸了", "修一下")]
    text = selftest.format_text(checks, selftest.summarize(checks))
    assert "存在失败项" in text
    assert "建议：修一下" in text


def test_format_markdown_rows():
    checks = [selftest.Check("a", "项 | 带竖线", "pass", "ok")]
    md = selftest.format_markdown(checks, selftest.summarize(checks))
    assert "| 状态 | 检查项 | 结果 | 建议 |" in md
    assert "项 \\| 带竖线" in md


def test_check_python_and_os():
    assert selftest.check_python().status == "pass"
    assert selftest.check_os().status in {"pass", "warn"}


def test_check_dependencies():
    result = selftest.check_dependencies()
    assert result.status == "pass"
    assert "mss" in result.data["versions"]


def test_run_all_without_protocol():
    checks = selftest.run_all(Config(audit_log=False), safe=True, include_protocol=False)
    assert len(checks) >= 15
    ids = {c.id for c in checks}
    assert "python" in ids and "deps" in ids and "mcp_build" in ids
    assert not any(c.status == "fail" for c in checks)


def test_write_reports(tmp_path):
    checks = [selftest.Check("a", "A", "pass", "ok")]
    counts = selftest.summarize(checks)
    jpath = tmp_path / "r.json"
    mpath = tmp_path / "r.md"
    selftest.write_reports(checks, counts, str(jpath), str(mpath))
    payload = json.loads(jpath.read_text(encoding="utf-8"))
    assert payload["counts"]["pass"] == 1
    assert payload["version"]
    assert "兼容性自检报告" in mpath.read_text(encoding="utf-8")


def test_capture_check_on_gui(gui):
    result = selftest.check_capture()
    assert result.status in {"pass", "warn"}
    assert result.data["mime"] == "image/png"
