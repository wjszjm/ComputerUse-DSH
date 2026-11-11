from __future__ import annotations

import json

import pytest
from PIL import Image

from wdc.capture import Frame
from wdc.config import Config
from wdc.tools import ComputerUse


def make_frame(image_size=(800, 600), origin=(0, 0), screen_size=(1600, 1200), scale=0.5):
    return Frame(
        image=Image.new("RGB", image_size, "black"),
        screen_origin=origin,
        screen_size=screen_size,
        raw_size=screen_size,
        scale=scale,
        monitor="primary",
    )


@pytest.fixture()
def app(temp_config, fake_backend):
    # 必须依赖 fake_backend：否则调用 type_text / click 的用例会真的操作
    # 用户当前桌面（例如把测试字符串输入到前台窗口里）。
    return ComputerUse(temp_config)


# --------------------------------------------------------------------------- #
# 坐标换算
# --------------------------------------------------------------------------- #
def test_click_uses_image_coordinates(app, fake_backend):
    app.session.set_frame(make_frame())
    result = app.click(400, 300)
    assert result.ok, result.text()
    assert result.data["screen_point"] == [800, 600]
    assert ("click", 800, 600, "left", 1) in fake_backend.calls


def test_click_with_origin_offset(app, fake_backend):
    app.session.set_frame(make_frame(image_size=(400, 300), origin=(100, 50),
                                     screen_size=(800, 600), scale=0.5))
    result = app.click(200, 200)
    assert result.ok, result.text()
    assert result.data["screen_point"] == [500, 450]


def test_click_without_frame_uses_screen_coordinates(app, fake_backend):
    result = app.click(300, 200)
    assert result.ok
    assert result.data["screen_point"] == [300, 200]


def test_click_screen_mode_bypasses_frame(app, fake_backend):
    app.session.set_frame(make_frame())
    result = app.click(300, 200, coordinate_mode="screen")
    assert result.data["screen_point"] == [300, 200]


def test_click_normalized(app, fake_backend):
    app.session.set_frame(make_frame())
    result = app.click(0.25, 0.5, coordinate_mode="normalized")
    assert result.ok, result.text()
    assert result.data["screen_point"] == [400, 600]


def test_click_normalized_out_of_range(app, fake_backend):
    result = app.click(1.5, 0.5, coordinate_mode="normalized")
    assert result.ok is False
    assert result.code == "bad_argument"


def test_click_rejects_point_outside_image(app, fake_backend):
    app.session.set_frame(make_frame(image_size=(800, 600), screen_size=(1920, 1080),
                                     scale=0.4166))
    result = app.click(1500, 900)          # 超出图片 800x600
    assert not result.ok and result.code=='image_out_of_bounds'
    assert fake_backend.calls==[]


def test_click_out_of_screen_rejected(app, fake_backend):
    result = app.click(-50000, 10)
    assert result.ok is False
    assert result.code == "out_of_bounds"


def test_drag_converts_both_points(app, fake_backend):
    app.session.set_frame(make_frame())
    result = app.drag(100, 100, 200, 200)
    assert result.ok
    assert result.data["screen_from"] == [200, 200]
    assert result.data["screen_to"] == [400, 400]


def test_scroll_without_coordinates(app, fake_backend):
    result = app.scroll(-3)
    assert result.ok
    assert result.data["direction"] == "down"


def test_scroll_with_coordinates(app, fake_backend):
    app.session.set_frame(make_frame())
    result = app.scroll(2, x=100, y=100)
    assert result.ok
    assert ("scroll", 2, 200, 200, False) in fake_backend.calls


def test_click_with_modifiers(app, fake_backend):
    result = app.click(100, 100, modifiers="ctrl+shift")
    assert result.ok
    assert ("key_down", "ctrl") in fake_backend.calls
    assert ("key_up", "shift") in fake_backend.calls


# --------------------------------------------------------------------------- #
# 安全策略
# --------------------------------------------------------------------------- #
def test_read_only_blocks_click(temp_config, fake_backend):
    cfg = Config(read_only=True, audit_log=False)
    app = ComputerUse(cfg)
    result = app.click(10, 10)
    assert result.ok is False
    assert result.code == "read_only"


def test_blocked_shortcut(app, fake_backend):
    result = app.press_key("win+l")
    assert result.ok is False
    assert result.code == "blocked_shortcut"


def test_emergency_stop_then_resume(app, fake_backend):
    assert app.emergency_stop("test").ok
    blocked = app.click(10, 10)
    assert blocked.ok is False
    assert blocked.code == "emergency_stop"
    assert app.resume().ok
    assert app.click(10, 10).ok


def test_run_command_disabled_by_default(app):
    result = app.run_command("echo hi")
    assert result.ok is False
    assert result.code == "shell_disabled"


# --------------------------------------------------------------------------- #
# 截图工具 / 审计
# --------------------------------------------------------------------------- #
def test_screenshot_returns_image(app, gui):
    result = app.screenshot(grid=False)
    assert result.ok, result.text()
    assert result.image and result.image.startswith(b"\x89PNG")
    assert result.data["image_size"][0] > 0
    assert app.session.last_frame is not None


def test_screenshot_updates_coordinate_space(app, gui, fake_backend):
    shot = app.screenshot(grid=False, scale=0.5)
    assert shot.ok
    iw, ih = shot.data["image_size"]
    result = app.click(iw // 2, ih // 2)
    assert result.ok
    assert result.data["coordinate_mode"] == "auto"


def test_audit_records_action(app, fake_backend, tmp_path):
    app.click(10, 10)
    log = tmp_path / "audit.log"
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    record = json.loads(lines[-1])
    assert record["action"] == "click"
    assert record["ok"] is True
    assert "duration_ms" in record


def test_type_text_audit_hides_plaintext(app, tmp_path):
    app.type_text("super-secret-password")
    log = tmp_path / "audit.log"
    text = log.read_text(encoding="utf-8")
    assert "super-secret-password" not in text
    assert "sha256_8" in text


def test_screen_info(app, gui):
    result = app.screen_info()
    assert result.ok
    assert "monitors" in result.data
    # 精简后安全状态改为扁平字段，不再嵌套在 safety 里
    assert result.data["read_only"] is False
    assert result.data["allow_input"] is True
    assert result.data["screen"]["width"] > 0


def test_wait(app):
    result = app.wait(1)
    assert result.ok and result.data["waited_ms"] == 1
