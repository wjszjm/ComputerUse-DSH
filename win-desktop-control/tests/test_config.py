from __future__ import annotations

import json


from wdc.config import Config, load_config


def test_defaults():
    cfg = Config()
    assert cfg.read_only is False
    assert cfg.allow_input is True
    assert cfg.allow_shell is False          # 危险能力默认关闭
    assert cfg.max_image_edge == 1280
    assert "win+l" in cfg.blocked_shortcuts


def test_read_only_forces_input_off():
    cfg = Config(read_only=True)
    assert cfg.allow_input is False
    assert cfg.allow_shell is False
    assert cfg.allow_launch is False


def test_load_from_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"max_image_edge": 900, "allow_shell": True}), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.max_image_edge == 900
    assert cfg.allow_shell is True


def test_env_override(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"read_only": False, "max_image_edge": 900}), encoding="utf-8")
    monkeypatch.setenv("WDC_READ_ONLY", "true")
    monkeypatch.setenv("WDC_MAX_IMAGE_EDGE", "1280")
    cfg = load_config(path)
    assert cfg.read_only is True
    assert cfg.allow_input is False
    assert cfg.max_image_edge == 1280


def test_unknown_keys_ignored(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"nonsense": 1, "grid_step": 80}), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.grid_step == 80


def test_shortcuts_normalized():
    cfg = Config(blocked_shortcuts=["Ctrl + Alt + Del"])
    assert "ctrl+alt+del" in cfg.blocked_shortcuts


def test_invalid_json_falls_back(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.max_image_edge == 1280
