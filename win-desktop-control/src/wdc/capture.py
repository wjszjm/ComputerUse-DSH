"""截屏：多显示器枚举、区域裁剪、等比缩放、坐标网格叠加、PNG/JPEG 编码。"""

from __future__ import annotations

import base64
import io
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import platform_win

try:  # Pillow 是硬依赖，但保持导入失败时给出可读错误
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore


class CaptureError(RuntimeError):
    """截图失败（锁屏、无显示、驱动异常等）。"""


@dataclass
class Monitor:
    index: int
    left: int
    top: int
    width: int
    height: int
    primary: bool = False
    name: str = ""
    dpi_scale: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
            "primary": self.primary,
            "name": self.name,
            "dpi_scale": self.dpi_scale,
        }


@dataclass
class Frame:
    """一次截图的结果 + 坐标换算所需的全部信息。"""

    image: Any
    screen_origin: tuple[int, int]
    screen_size: tuple[int, int]
    raw_size: tuple[int, int]
    scale: float
    monitor: str
    captured_at: float = field(default_factory=time.time)
    monitors: list[Monitor] = field(default_factory=list)
    frame_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    captured_monotonic: float = field(default_factory=time.monotonic)
    revision: int = 0
    context_window: tuple | None = None
    context_focus: tuple | None = None
    context_layout: tuple | None = None
    invalid_reason: str = ""
    capture_target: tuple[int, int] | None = None
    target_bounds: tuple[int, int, int, int] | None = None
    background: bool = False

    # -- 坐标换算 ------------------------------------------------------- #
    def image_to_screen(self, x: float, y: float) -> tuple[int, int]:
        ox, oy = self.screen_origin
        width, height = self.image.size
        sw, sh = self.screen_size
        return (ox + min(sw-1, max(0, round(x*sw/width))),
                oy + min(sh-1, max(0, round(y*sh/height))))

    def screen_to_image(self, x: float, y: float) -> tuple[int, int]:
        ox, oy = self.screen_origin
        width, height = self.image.size
        sw, sh = self.screen_size
        return int(round((x-ox)*width/sw)), int(round((y-oy)*height/sh))

    def contains_image_point(self, x: float, y: float) -> bool:
        w, h = self.image.size
        return 0 <= x < w and 0 <= y < h

    def to_dict(self) -> dict[str, Any]:
        return {
            "monitor": self.monitor,
            "frame_id": self.frame_id,
            "image_size": list(self.image.size),
            "raw_size": list(self.raw_size),
            "scale": round(self.scale, 4),
            "scale_axes": [self.image.size[0]/self.screen_size[0], self.image.size[1]/self.screen_size[1]],
            "screen_region": {
                "x": self.screen_origin[0],
                "y": self.screen_origin[1],
                "width": self.screen_size[0],
                "height": self.screen_size[1],
            },
            "captured_at": round(self.captured_at, 3),
            "coordinate_space": "image",
            "coordinate_hint": "Background window image pixels are for observation and OCR only." if self.background else (
                f"image {self.image.size[0]}x{self.image.size[1]}, scale {self.scale:.3f}, "
                f"screen origin {self.screen_origin[0]},{self.screen_origin[1]}. "
                "Pass image pixels and frame_id to click/drag; the server converts each axis. "
                "Take a fresh screenshot after input; stale or invalid image coordinates are rejected."
            ),
        }


def _mss_module():
    try:
        import mss  # noqa: PLC0415

        return mss
    except Exception as exc:  # pragma: no cover
        raise CaptureError(f"截图后端 mss 不可用：{exc}") from exc


def _mss_factory():
    mss = _mss_module()
    # mss 2.x/10.x 把 mss.mss 改名成 mss.MSS，1.x 只有 mss.mss，两者都兼容
    return getattr(mss, "MSS", None) or getattr(mss, "mss")


def list_monitors() -> list[Monitor]:
    """枚举显示器。index=0 表示拼接后的整个虚拟桌面。"""
    try:
        factory = _mss_factory()
        with factory() as sct:
            raw = list(sct.monitors)
    except Exception as error:
        raw = platform_win.enumerate_displays()
        if not raw:
            raise CaptureError("无法读取显示器布局。") from error
    monitors: list[Monitor] = []
    for i, m in enumerate(raw):
        is_primary = bool(m.get("is_primary", False))
        monitors.append(
            Monitor(
                index=i,
                left=int(m.get("left", 0)),
                top=int(m.get("top", 0)),
                width=int(m.get("width", 0)),
                height=int(m.get("height", 0)),
                primary=is_primary,
                name=str(m.get("output", "") or ""),
                dpi_scale=platform_win.monitor_dpi_scale(int(m.get("left",0)),int(m.get("top",0))) if i else None,
            )
        )
    # 后端未标注主屏时，用「包含原点」的显示器兜底
    if not any(m.primary for m in monitors[1:]):
        for m in monitors[1:]:
            if m.left <= 0 < m.left + m.width and m.top <= 0 < m.top + m.height:
                m.primary = True
                break
    return monitors


def _resolve_region(
    monitor: str | int,
    region: Iterable[int] | None,
    monitors: list[Monitor],
) -> tuple[tuple[int, int], tuple[int, int], list[Monitor]]:
    """算出要截取的屏幕区域 (left, top, width, height)。"""
    virtual = monitors[0] if monitors else Monitor(0, 0, 0, 1920, 1080)

    if region is not None:
        values = list(region)
        if len(values) != 4:
            raise ValueError("region 需要 4 个整数：[x, y, width, height]")
        x, y, w, h = (int(v) for v in values)
        if w <= 0 or h <= 0:
            raise ValueError("region 的 width / height 必须为正数")
        return (x, y), (w, h), monitors

    target = monitor
    if isinstance(target, str):
        key = target.strip().lower()
        if key in {"all", "virtual", "desktop"}:
            return (virtual.left, virtual.top), (virtual.width, virtual.height), monitors
        if key in {"primary", "main", "0", ""}:
            primary = next((m for m in monitors[1:] if m.primary), None)
            if primary is None:
                primary = monitors[1] if len(monitors) > 1 else virtual
            return (primary.left, primary.top), (primary.width, primary.height), monitors
        try:
            index = int(key)
        except ValueError as exc:
            raise ValueError(f"未知的 monitor 取值：{monitor!r}") from exc
    else:
        index = int(target)

    match = next((m for m in monitors if m.index == index), None)
    if match is None and 0 < index < len(monitors):
        match = monitors[index]
    if match is None:
        raise ValueError(f"显示器索引 {index} 不存在，可用 0..{max(0, len(monitors) - 1)}")
    return (match.left, match.top), (match.width, match.height), monitors


def _grab_raw(left: int, top: int, width: int, height: int):
    try:
        factory = _mss_factory()
        with factory() as sct:
            shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
            return Image.frombytes("RGB", shot.size, shot.rgb)
    except Exception as error:
        if not platform_win.IS_WINDOWS:
            raise CaptureError("截图后端不可用。") from error
        try:
            from PIL import ImageGrab
            return ImageGrab.grab(bbox=(left,top,left+width,top+height),all_screens=True).convert("RGB")
        except Exception as fallback:
            raise CaptureError("两个截图后端均无法捕获指定区域。") from fallback


def _load_font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        try:
            return ImageFont.load_default()
        except Exception:
            return None


def draw_grid(image, step: int = 100, alpha: int = 70):
    """在图上叠加网格与多重坐标参照，降低模型估算坐标的误差。

    为什么不止于"边缘刻度 + 数格子"：模型收到的图片可能被上游再次缩放
    （视觉编码器有尺寸上限），于是"我看到的像素"和"图片真实像素"不是一回事。
    按格子估算会累积误差，越到图片中部越偏。

    因此在图上放置三重参照，让模型可以「读数字」而不是「估位置」：
      1. 边缘刻度：上边、左边每隔 2 格标数字
      2. 内部交叉点：每隔 4 格在图中直接标出 (x, y)
      3. 中心准星：标出图片中心坐标，提供一个中部锚点
    """
    if Image is None or step < 20:
        return image
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    width, height = image.size
    minor = (255, 60, 60, max(40, alpha - 25))
    major = (255, 60, 60, min(150, alpha + 30))
    edge = (255, 60, 60, min(200, alpha + 90))

    # 次要线 1 格、主要线 2 格，主次分明才数得清
    for x in range(0, width, step):
        draw.line([(x, 0), (x, height)], fill=minor, width=1)
    for y in range(0, height, step):
        draw.line([(0, y), (width, y)], fill=minor, width=1)
    for x in range(0, width, step * 2):
        draw.line([(x, 0), (x, height)], fill=major, width=1)
    for y in range(0, height, step * 2):
        draw.line([(0, y), (width, y)], fill=major, width=1)

    font = _load_font(14)

    def _label(text, x, y):
        box_w = 7 + 7 * len(text)
        draw.rectangle([x, y, x + box_w, y + 17], fill=(0, 0, 0, 190))
        draw.text((x + 3, y + 2), text, fill=(255, 255, 255, 245), font=font)

    if font is not None:
        # 边缘刻度
        for x in range(0, width, step * 2):
            _label(str(x), x + 1, 1)
        for y in range(step * 2, height, step * 2):
            _label(str(y), 1, y + 1)

        # 内部交叉点：给出中部可直接读数的锚点
        for x in range(step * 4, width, step * 4):
            for y in range(step * 4, height, step * 4):
                _label(f"{x},{y}", x + 2, y + 2)

        # 中心准星：图片几何中心，中部定位的基准
        cx, cy = width // 2, height // 2
        arm = 18
        draw.line([(cx - arm, cy), (cx + arm, cy)], fill=(0, 255, 180, 220), width=2)
        draw.line([(cx, cy - arm), (cx, cy + arm)], fill=(0, 255, 180, 220), width=2)
        _label(f"中心 {cx},{cy}", cx + arm + 4, cy - 9)

        # 右下角标注图片总尺寸，便于换算
        _label(f"图片 {width}x{height}", width - (7 + 7 * len(f"图片 {width}x{height}")) - 2, height - 20)

    draw.rectangle([0, 0, width - 1, height - 1], outline=edge, width=1)
    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


def capture(
    monitor: str | int = "primary",
    region: Iterable[int] | None = None,
    scale: float = 0.0,
    grid: bool = True,
    grid_step: int = 100,
    max_edge: int = 1600,
    validate_pixel=None,
    grab_window=None,
) -> Frame:
    """截屏并返回 Frame。

    scale=0 表示自动按 max_edge 缩放；scale=1 表示原尺寸。
    """
    if Image is None:
        raise CaptureError("Pillow 未安装，无法处理截图")

    platform_win.setup_dpi_awareness()
    if platform_win.is_secure_desktop_active():
        raise CaptureError(
            "当前处于锁屏 / UAC 安全桌面，无法截屏。请先解锁桌面或关闭提权弹窗。"
        )

    monitors = list_monitors()
    origin, size, monitors = _resolve_region(monitor, region, monitors)
    def validate_target():
        if validate_pixel is None:
            return
        x,y=origin
        width,height=size
        if not all(validate_pixel(px,py) for px,py in (
            (x+width*i//4,y+height*j//4) for i in (1,2,3) for j in (1,2,3)
        )):
            raise CaptureError("目标窗口在采集位置被遮挡。")

    validate_target()
    captured_at, captured_monotonic = time.time(), time.monotonic()
    raw = _grab_raw(origin[0], origin[1], size[0], size[1]) if grab_window is None else grab_window()
    validate_target()
    raw_size = raw.size
    if raw_size != size:
        raise CaptureError("截图后端返回了与请求范围不符的图像尺寸。")

    if scale and scale > 0:
        factor = float(scale)
    elif max_edge and max(raw_size) > max_edge:
        factor = max_edge / float(max(raw_size))
    else:
        factor = 1.0
    factor = max(0.05, min(factor, 4.0))

    if abs(factor - 1.0) > 1e-6:
        new_size = (max(1, int(round(raw_size[0] * factor))), max(1, int(round(raw_size[1] * factor))))
        image = raw.resize(new_size, Image.LANCZOS)
        real_scale = new_size[0] / raw_size[0]
    else:
        image = raw
        real_scale = 1.0

    if grid:
        image = draw_grid(image, step=grid_step)

    return Frame(
        image=image,
        screen_origin=origin,
        screen_size=size,
        raw_size=raw_size,
        scale=real_scale,
        monitor=str(monitor),
        monitors=monitors,
        captured_at=captured_at,
        captured_monotonic=captured_monotonic,
    )


def encode(frame: Frame, fmt: str = "png", quality: int = 80) -> tuple[bytes, str]:
    """把 Frame 编码为字节流，返回 (data, mime)。"""
    buffer = io.BytesIO()
    fmt = (fmt or "png").lower()
    if fmt in {"jpg", "jpeg"}:
        frame.image.convert("RGB").save(buffer, format="JPEG", quality=int(quality), optimize=True)
        return buffer.getvalue(), "image/jpeg"
    frame.image.save(buffer, format="PNG", optimize=True, compress_level=6)
    return buffer.getvalue(), "image/png"


def to_base64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def save(frame: Frame, path: str, fmt: str = "png", quality: int = 80) -> str:
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data, _ = encode(frame, fmt=fmt, quality=quality)
    target.write_bytes(data)
    return str(target)
