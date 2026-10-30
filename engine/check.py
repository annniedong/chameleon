"""The engine's own gate: every layout must pass this before it is returned.

It checks the hard rules only (canvas bounds, image proportions, font size,
text fit). It is deliberately small; the LiquidHaskell core will replace it.
"""

from __future__ import annotations

from engine import textmetrics
from engine.geometry import keeps_aspect
from engine.normalize import Element

EDGE_TOLERANCE = 0.5
ASPECT_TOLERANCE = 0.04  # stricter than the 5% rule, to leave rounding room


def problems(layout: list[Element], source: list[Element], W: float, H: float,
             check_text: bool = True) -> list[str]:
    found: list[str] = []
    if len(layout) != len(source):
        found.append("element count changed")
    if [e.id for e in layout] != [e.id for e in source]:
        found.append("element IDs or order changed")
    for el, src in zip(layout, source):
        if (el.x < -EDGE_TOLERANCE or el.y < -EDGE_TOLERANCE
                or el.x + el.width > W + EDGE_TOLERANCE or el.y + el.height > H + EDGE_TOLERANCE):
            found.append(f"{el.id}: off-canvas")
        if keeps_aspect(el) and src.aspect and el.aspect is not None:
            if abs(el.aspect / src.aspect - 1) > ASPECT_TOLERANCE:
                found.append(f"{el.id}: aspect drift")
        elif keeps_aspect(el) and src.aspect and el.aspect is None:
            found.append(f"{el.id}: image collapsed")
        if el.type == "text":
            if el.font_size is None or el.font_size < 1:
                found.append(f"{el.id}: font_size below 1")
            elif check_text and el.props.get("content") and not textmetrics.fits(el.text_props()):
                found.append(f"{el.id}: text overflow")
    return found
