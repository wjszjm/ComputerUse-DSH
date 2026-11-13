"""Small, side-effect-free identity and geometry helpers for screenshot targeting."""
import math

MAX_FRAME_AGE_SECONDS = 30.0


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('坐标必须是有限数字。')
    return float(value)


def window_identity(window):
    if window is None:return ()
    rect=window.get('rect') or {}
    return (window.get('handle'),window.get('pid'),
            *(rect.get(key) for key in ('left','top','right','bottom')))


def monitor_signature(monitors):
    return tuple(sorted((m.index,m.left,m.top,m.width,m.height,m.primary,getattr(m,"dpi_scale",None)) for m in monitors))


def on_monitor(x,y,monitors):
    return any(m.index!=0 and m.left<=x<m.left+m.width and m.top<=y<m.top+m.height for m in monitors)
