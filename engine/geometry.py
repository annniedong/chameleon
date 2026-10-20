"""Small geometry helpers shared by every layout layer."""

from __future__ import annotations

import math

from engine.normalize import Element


def rotated_extent(width: float, height: float, rotation: float) -> tuple[float, float]:
    """Half-width and half-height of the box's axis-aligned bounds once rotated."""
    a = math.radians(rotation)
    hw, hh = width / 2, height / 2
    return abs(hw * math.cos(a)) + abs(hh * math.sin(a)), abs(hw * math.sin(a)) + abs(hh * math.cos(a))


def keeps_aspect(el: Element) -> bool:
    """Images other than full-bleed `cover` ones must keep their proportions."""
    return el.type == "image" and el.props.get("fit", "contain") != "cover"


def scale_box(el: Element, s: float) -> None:
    """Scale an element about its own centre."""
    cx, cy = el.x + el.width / 2, el.y + el.height / 2
    el.width *= s
    el.height *= s
    el.x, el.y = cx - el.width / 2, cy - el.height / 2


def clamp_into(el: Element, W: float, H: float) -> None:
    """Shrink and move an element until its box, and its rotated bounds, sit
    inside the canvas. Aspect-locked images shrink uniformly."""
    if el.width > W or el.height > H:
        if keeps_aspect(el) and el.width > 0 and el.height > 0:
            scale_box(el, min(W / el.width, H / el.height))
        else:
            el.width, el.height = min(el.width, W), min(el.height, H)

    ex, ey = rotated_extent(el.width, el.height, el.rotation)
    if el.rotation and (2 * ex > W or 2 * ey > H):
        scale_box(el, min(W / (2 * ex), H / (2 * ey)) * 0.999)
        ex, ey = rotated_extent(el.width, el.height, el.rotation)

    cx = min(max(el.x + el.width / 2, ex), W - ex)
    cy = min(max(el.y + el.height / 2, ey), H - ey)
    el.x, el.y = cx - el.width / 2, cy - el.height / 2
    # The unrotated box must also be inside (that is what the structural
    # check looks at), even when the rotated bounds already are.
    el.x = min(max(el.x, 0.0), W - el.width)
    el.y = min(max(el.y, 0.0), H - el.height)
