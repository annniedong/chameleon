"""Safe layouts used when nothing better is available.

`safe_layout` keeps the design's arrangement and scales it uniformly to fit
(letterboxed and centred), then repairs anything off-canvas or overflowing.
It never looks good on a very different shape, but it is always valid.

`last_resort` does no text measurement at all, so it survives even if the
font stack is broken: it only moves and shrinks boxes into the canvas.
"""

from __future__ import annotations

from engine.fit import fit_text
from engine.geometry import clamp_into
from engine.normalize import Element


def safe_layout(elements: list[Element], src_w: float, src_h: float, W: float, H: float) -> None:
    s = min(W / src_w, H / src_h)
    ox, oy = (W - src_w * s) / 2, (H - src_h * s) / 2
    for el in elements:
        if el.type == "image" and el.props.get("fit") == "cover":
            el.x, el.y, el.width, el.height = 0.0, 0.0, W, H
            continue
        el.x, el.y = ox + el.x * s, oy + el.y * s
        el.width, el.height = el.width * s, el.height * s
        if el.font_size is not None:
            el.font_size = max(el.font_size * s, 1.0)
    for el in elements:
        clamp_into(el, W, H)
        fit_text(el, W, H)
        clamp_into(el, W, H)


def last_resort(elements: list[Element], W: float, H: float) -> None:
    for el in elements:
        clamp_into(el, W, H)
        if el.font_size is not None:
            el.font_size = max(el.font_size, 1.0)
