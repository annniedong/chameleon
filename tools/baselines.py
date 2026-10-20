"""Reference engines to compare against. Neither is a real answer."""

from __future__ import annotations

import copy
from typing import Any


def stretch(source: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """The starter repo's baseline: scale x and y independently."""
    sx = target["width"] / source["canvas"]["width"]
    sy = target["height"] / source["canvas"]["height"]
    out = []
    for el in source["elements"]:
        el = copy.deepcopy(el)
        el["x"], el["y"] = el["x"] * sx, el["y"] * sy
        el["width"], el["height"] = el["width"] * sx, el["height"] * sy
        if el["type"] == "text":
            el["font_size"] = max(1.0, el["font_size"] * min(sx, sy))
        out.append(el)
    return {"canvas": dict(target), "elements": out}


def letterbox(source: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """Uniform scale-to-fit, centred. Full-bleed backgrounds stretch to fill."""
    W, H = target["width"], target["height"]
    s = min(W / source["canvas"]["width"], H / source["canvas"]["height"])
    ox = (W - source["canvas"]["width"] * s) / 2
    oy = (H - source["canvas"]["height"] * s) / 2
    out = []
    for el in source["elements"]:
        el = copy.deepcopy(el)
        if el.get("fit") == "cover":
            el["x"], el["y"], el["width"], el["height"] = 0, 0, W, H
        else:
            el["x"], el["y"] = ox + el["x"] * s, oy + el["y"] * s
            el["width"], el["height"] = el["width"] * s, el["height"] * s
            if el["type"] == "text":
                el["font_size"] = max(1.0, el["font_size"] * s)
        out.append(el)
    return {"canvas": dict(target), "elements": out}


ENGINES = {"stretch": stretch, "letterbox": letterbox}
