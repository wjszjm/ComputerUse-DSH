from __future__ import annotations

import json


from wdc import cli


def run(argv, capsys):
    code = cli.main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_version(capsys):
    code, out, _ = run(["version"], capsys)
    assert code == 0
    assert "wdc 1.0.0" in out


def test_no_args_prints_help(capsys):
    code, out, _ = run([], capsys)
    assert code == 0
    assert "usage" in out.lower()


def test_tools_markdown(capsys):
    code, out, _ = run(["tools"], capsys)
    assert code == 0
    assert "| `screenshot` |" in out


def test_tools_names(capsys):
    code, out, _ = run(["tools", "--format", "names"], capsys)
    assert code == 0
    names = out.strip().splitlines()
    assert "click" in names and len(names) == 18


def test_tools_openai_json(capsys):
    code, out, _ = run(["tools", "--format", "openai"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload[0]["type"] == "function"


def test_config_init(tmp_path, capsys):
    target = tmp_path / "config.json"
    code, _, _ = run(["config", "--init", str(target)], capsys)
    assert code == 0
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["allow_shell"] is False
    # 二次执行不覆盖
    code2, _, err = run(["config", "--init", str(target)], capsys)
    assert code2 == 1 and "已存在" in err


def test_call_unknown_tool(capsys):
    code, _, err = run(["call", "nope"], capsys)
    assert code == 2
    assert "未知工具" in err


def test_call_bad_json(capsys):
    code, _, err = run(["call", "click", "--args", "{oops"], capsys)
    assert code == 2
    assert "JSON" in err


def test_call_screen_info(capsys, gui):
    code, out, _ = run(["call", "screen_info"], capsys)
    assert code == 0
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["ok"] is True


def test_call_click_blocked(capsys, gui):
    code, out, _ = run(["call", "click", "--args", '{"x": -99999, "y": 0}'], capsys)
    assert code == 1
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["ok"] is False and payload["error_code"] == "out_of_bounds"


def test_shot_writes_file(tmp_path, capsys, gui):
    target = tmp_path / "shot.png"
    code, out, _ = run(["shot", "-o", str(target), "--no-grid", "--scale", "0.2"], capsys)
    assert code == 0
    assert target.exists() and target.stat().st_size > 0
    meta = json.loads(out)
    assert meta["saved_to"].endswith("shot.png")


def test_shot_bad_monitor(tmp_path, capsys, gui):
    code, _, err = run(["shot", "-o", str(tmp_path / "x.png"), "--monitor", "99"], capsys)
    assert code == 1
    assert "截屏失败" in err


def test_selftest_safe_runs(capsys, tmp_path):
    json_out = tmp_path / "report.json"
    code, out, _ = run(["selftest", "--safe", "--no-protocol", "--json", str(json_out)], capsys)
    assert code == 0
    assert "[PASS]" in out
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["counts"]["fail"] == 0
    assert len(payload["checks"]) >= 15


def test_demo_blocked_in_read_only(monkeypatch, capsys):
    monkeypatch.setenv("WDC_READ_ONLY", "true")
    code, _, err = run(["demo"], capsys)
    assert code == 1
    assert "只读" in err
