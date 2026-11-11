from __future__ import annotations

import pytest

from wdc import capture


def test_list_monitors(gui):
    monitors = capture.list_monitors()
    assert len(monitors) >= 2  # 0 = 虚拟桌面拼接
    assert monitors[0].width > 0 and monitors[0].height > 0


def test_capture_primary(gui):
    frame = capture.capture(monitor="primary", grid=False)
    sw, sh = frame.screen_size
    iw, ih = frame.image.size
    assert sw > 0 and sh > 0
    assert iw > 0 and ih > 0
    assert abs(iw / sw - frame.scale) < 0.02


def test_capture_scales_down(gui):
    frame = capture.capture(monitor="primary", max_edge=320, grid=False)
    assert max(frame.image.size) <= 320


def test_coordinate_roundtrip(gui):
    frame = capture.capture(monitor="primary", scale=0.5, grid=False)
    sx, sy = frame.image_to_screen(100, 100)
    ix, iy = frame.screen_to_image(sx, sy)
    assert abs(ix - 100) <= 1 and abs(iy - 100) <= 1
    assert frame.contains_image_point(0, 0)
    assert not frame.contains_image_point(-1, 0)


def test_grid_draws(gui):
    plain = capture.capture(monitor="primary", grid=False, scale=0.5)
    grid = capture.capture(monitor="primary", grid=True, scale=0.5)
    assert plain.image.size == grid.image.size
    # 叠加网格后像素必然变化
    assert plain.image.tobytes() != grid.image.tobytes()


def test_encode_png_and_jpeg(gui):
    frame = capture.capture(monitor="primary", scale=0.25, grid=False)
    png, mime = capture.encode(frame, "png")
    jpg, mime2 = capture.encode(frame, "jpeg")
    assert png.startswith(b"\x89PNG") and mime == "image/png"
    assert jpg.startswith(b"\xff\xd8") and mime2 == "image/jpeg"


def test_region_validation(gui):
    with pytest.raises(ValueError):
        capture.capture(region=[0, 0, 0, 10])
    with pytest.raises(ValueError):
        capture.capture(region=[0, 0, 10])


def test_bad_monitor(gui):
    with pytest.raises(ValueError):
        capture.capture(monitor=99)


def test_frame_meta(gui):
    frame = capture.capture(monitor="primary", grid=False)
    meta = frame.to_dict()
    assert meta["coordinate_space"] == "image"
    assert meta["screen_region"]["width"] == frame.screen_size[0]
    assert "coordinate_hint" in meta
