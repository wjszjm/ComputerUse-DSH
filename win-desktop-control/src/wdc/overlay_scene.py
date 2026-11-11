"""Validated, display-only scene packets and small premultiplied-alpha tiles."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFont

LABELS = {'idle':'空闲', 'observing':'正在观察', 'clicking':'点击中', 'typing':'输入中',
          'scrolling':'滚动中', 'operating':'操作中', 'waiting':'等待界面响应',
          'stopping':'正在停止', 'stopped':'已停止', 'error':'需要处理'}
MAX_PACKET = 16384


def finite(value: Any, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('invalid_scene_number')
    return float(value)


def rectangle(value: dict) -> dict:
    return {'x':finite(value['x'],-65536,65536), 'y':finite(value['y'],-65536,65536),
            'width':finite(value['width'],1,32768), 'height':finite(value['height'],1,32768)}


def validate_scene(scene: dict, viewport: dict, work_area: dict) -> dict:
    """Copy only whitelisted geometry. Labels/errors/text from the sender are ignored."""
    if not isinstance(scene, dict) or not isinstance(scene.get('visible'), bool):
        raise ValueError('invalid_scene')
    if not scene['visible']:
        return {'visible':False, 'badge':None, 'rings':[], 'state':'idle'}
    if scene.get('geometryKnown') is not True or rectangle(scene['viewport']) != viewport:
        raise ValueError('stale_geometry')
    state = scene['state']
    if state not in LABELS:
        raise ValueError('invalid_state')
    badge = None
    if scene.get('badge') is not None:
        item = scene['badge']
        badge = rectangle(item)
        if badge['width'] > 1024 or badge['height'] > 256:
            raise ValueError('oversized_badge')
        if not isinstance(item.get('compact'), bool):
            raise ValueError('invalid_badge')
        badge.update(compact=item['compact'], scale=finite(item['scale'],0.75,3), opacity=finite(item['opacity'],0,1))
        if (badge['x'] < work_area['x'] or badge['y'] < work_area['y'] or
            badge['x']+badge['width'] > work_area['x']+work_area['width']+0.01 or
            badge['y']+badge['height'] > work_area['y']+work_area['height']+0.01):
            raise ValueError('badge_outside_work_area')
    rings = scene.get('rings')
    if not isinstance(rings, list) or len(rings) > 2:
        raise ValueError('invalid_rings')
    clean = []
    for ring in rings:
        point = {'x':finite(ring['x'],viewport['x'],viewport['x']+viewport['width']-0.001),
                 'y':finite(ring['y'],viewport['y'],viewport['y']+viewport['height']-0.001),
                 'radius':finite(ring['radius'],1,128), 'strokeWidth':finite(ring['strokeWidth'],0.5,12),
                 'opacity':finite(ring['opacity'],0,1)}
        clean.append(point)
    pointer = None
    if scene.get('pointer') is not None:
        item = scene['pointer']
        pointer = {'x':finite(item['x'],viewport['x'],viewport['x']+viewport['width']-0.001),
                   'y':finite(item['y'],viewport['y'],viewport['y']+viewport['height']-0.001),
                   'scale':finite(item['scale'],0.75,3)}
    return {'visible':True, 'state':state, 'badge':badge, 'rings':clean, 'pointer':pointer}


@lru_cache(maxsize=12)
def font(size: int):
    directory = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts'
    for name in ('msyh.ttc', 'simhei.ttf', 'segoeui.ttf'):
        try:
            return ImageFont.truetype(str(directory/name), size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


@dataclass
class Tile:
    x: int
    y: int
    image: Any

    def bgra(self) -> bytes:
        red, green, blue, alpha = self.image.convert('RGBA').split()
        premultiplied = Image.merge('RGBA', (ImageChops.multiply(red, alpha), ImageChops.multiply(green, alpha),
                                           ImageChops.multiply(blue, alpha), alpha))
        return premultiplied.tobytes('raw', 'BGRA')


def render_tiles(scene: dict, viewport: dict) -> list[Tile | None]:
    if not scene['visible']:
        return [None,None,None]
    tiles = [None,None,None]
    b = scene['badge']
    if b:
        width, height = math.ceil(b['width']), math.ceil(b['height'])
        image = Image.new('RGBA', (width, height))
        draw = ImageDraw.Draw(image)
        alpha = round(255*b['opacity'])
        draw.rounded_rectangle((0,0,width-1,height-1), radius=height/2,
                               fill=(250,252,255,alpha), outline=(175,190,210,alpha), width=1)
        scale = b['scale']
        dot_x = width/2 if b['compact'] else 17*scale
        color = (110,120,135) if scene['state']=='idle' else (185,60,60) if scene['state']=='error' else (
            (155,107,20) if scene['state'] in {'waiting','stopping','stopped'} else (35,115,175))
        radius = 3.5*scale
        draw.ellipse((dot_x-radius,height/2-radius,dot_x+radius,height/2+radius), fill=(*color,alpha))
        if not b['compact']:
            draw.text((30*scale,height/2), LABELS[scene['state']], anchor='lm', font=font(round(13*scale)), fill=(30,42,58,alpha))
        tiles[0] = Tile(round(b['x']),round(b['y']),image)
    for index, r in enumerate(scene['rings']):
        padding = r['strokeWidth']+2
        x = max(math.floor(r['x']-r['radius']-padding), round(viewport['x']))
        y = max(math.floor(r['y']-r['radius']-padding), round(viewport['y']))
        right = min(math.ceil(r['x']+r['radius']+padding), round(viewport['x']+viewport['width']))
        bottom = min(math.ceil(r['y']+r['radius']+padding), round(viewport['y']+viewport['height']))
        image = Image.new('RGBA',(max(1,right-x),max(1,bottom-y)))
        draw = ImageDraw.Draw(image)
        cx, cy = r['x']-x, r['y']-y
        draw.ellipse((cx-r['radius'],cy-r['radius'],cx+r['radius'],cy+r['radius']),
                     outline=(35,115,175,round(255*r['opacity'])), width=max(1,round(r['strokeWidth'])))
        tiles[index+1] = Tile(x,y,image)
    pointer = scene.get('pointer')
    if pointer:
        # Reuse the third own window; at most one ring accompanies the pointer.
        scale = pointer['scale']
        width, height = math.ceil(20*scale), math.ceil(28*scale)
        x, y = math.floor(pointer['x']), math.floor(pointer['y'])
        width = max(1, min(width, round(viewport['x']+viewport['width'])-x))
        height = max(1, min(height, round(viewport['y']+viewport['height'])-y))
        image = Image.new('RGBA', (width, height))
        points = [(round(px*scale),round(py*scale)) for px,py in [(0,0),(0,21),(6,16),(10,25),(14,23),(10,14),(18,14)]]
        draw = ImageDraw.Draw(image)
        draw.polygon(points, fill=(17,17,17,255))
        draw.line(points+[points[0]], fill=(255,255,255,255), width=max(1,round(1.4*scale)), joint='curve')
        tiles[2] = Tile(x,y,image)
    return tiles
