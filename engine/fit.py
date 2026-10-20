"""Fit text into its box by shrinking the font, then growing the box."""

from __future__ import annotations

import math

from engine import textmetrics
from engine.normalize import Element

# Below this size text stops being readable, so we would rather give the
# box more room than shrink further.
READABLE_PX = 8.0
MIN_PX = 1.0


def _fits(el: Element, size: float, width: float, height: float) -> bool:
    return textmetrics.fits(el.text_props(font_size=size, width=width, height=height))


def largest_fitting_size(el: Element, ceiling: float, width: float, height: float) -> float | None:
    """Largest font size up to `ceiling` that fits the box, or None."""
    if _fits(el, ceiling, width, height):
        return ceiling
    lo, hi = MIN_PX, ceiling
    if not _fits(el, lo, width, height):
        return None
    while hi - lo > 0.25:
        mid = (lo + hi) / 2
        if _fits(el, mid, width, height):
            lo = mid
        else:
            hi = mid
    # Round down: rounding to nearest can land on a size that no longer fits.
    return max(MIN_PX, math.floor(lo * 4) / 4)


def fit_text(el: Element, W: float, H: float) -> None:
    """Make a text element's content fit its box inside a W x H canvas.

    Keeps the current font size when it already fits. Otherwise shrinks the
    font, but not below READABLE_PX while growing the box can still help.
    """
    if el.type != "text" or not el.props.get("content"):
        return
    size = float(el.font_size or READABLE_PX)
    fitted = largest_fitting_size(el, size, el.width, el.height)
    if fitted is not None and fitted >= min(size, READABLE_PX):
        el.font_size = fitted
        return

    # Too small to read at this box size: give it more room instead.
    target = min(size, READABLE_PX)
    box = textmetrics.measure(el.text_props(font_size=target, width=el.width))
    if box.max_line_width > el.width:
        el.width = min(W, box.max_line_width + 1)
        box = textmetrics.measure(el.text_props(font_size=target, width=el.width))
    if box.height <= H:
        el.height = max(el.height, box.height + 1)
        el.y = min(el.y, H - el.height)
        el.x = min(el.x, W - el.width)
        fitted = largest_fitting_size(el, size, el.width, el.height)

    el.font_size = fitted if fitted is not None else MIN_PX
