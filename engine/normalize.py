"""Tolerant input parsing.

Turns whatever arrives into well-formed working elements, recording each
repair instead of failing. Downstream code can then assume finite numbers,
non-negative sizes and schema defaults. The raw elements are kept so the
output can carry every original field through untouched.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

DEFAULTS: dict[str, Any] = {"rotation": 0.0, "opacity": 1.0, "z_index": 0, "line_height": 1.2}
GEOMETRY = ("x", "y", "width", "height")


@dataclass
class Element:
    """A working copy of one source element, with its position in the input."""

    index: int
    raw: dict[str, Any]
    id: str
    type: str
    x: float
    y: float
    width: float
    height: float
    rotation: float
    opacity: float
    z_index: int
    font_size: float | None = None
    props: dict[str, Any] = field(default_factory=dict)

    @property
    def aspect(self) -> float | None:
        return self.width / self.height if self.width > 0 and self.height > 0 else None

    def text_props(self, font_size: float | None = None, width: float | None = None,
                   height: float | None = None) -> dict[str, Any]:
        """The fields textmetrics needs, at an optional candidate size."""
        return {
            "content": self.props.get("content", ""),
            "font_family": self.props.get("font_family", ""),
            "font_weight": self.props.get("font_weight"),
            "font_style": self.props.get("font_style"),
            "line_height": self.props.get("line_height", DEFAULTS["line_height"]),
            "font_size": font_size if font_size is not None else self.font_size,
            "width": width if width is not None else self.width,
            "height": height if height is not None else self.height,
        }


def _number(value: Any, default: float, notes: list[str], label: str) -> float:
    if isinstance(value, bool):
        value = None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            value = None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    if value is not None:
        notes.append(f"{label}: replaced {value!r} with {default}")
    return default


def normalize_canvas(canvas: Any, notes: list[str]) -> tuple[float, float]:
    canvas = canvas if isinstance(canvas, dict) else {}
    w = _number(canvas.get("width"), 1080.0, notes, "canvas.width")
    h = _number(canvas.get("height"), 1080.0, notes, "canvas.height")
    return max(w, 1.0), max(h, 1.0)


def normalize_elements(raw_elements: Any, notes: list[str]) -> list[Element]:
    out: list[Element] = []
    for i, raw in enumerate(raw_elements if isinstance(raw_elements, list) else []):
        if not isinstance(raw, dict):
            notes.append(f"element #{i}: not an object, kept as is")
            raw = {}
        eid = str(raw.get("id", f"#{i}"))
        label = f"element '{eid}'"
        x = _number(raw.get("x"), 0.0, notes, f"{label}.x")
        y = _number(raw.get("y"), 0.0, notes, f"{label}.y")
        w = _number(raw.get("width"), 0.0, notes, f"{label}.width")
        h = _number(raw.get("height"), 0.0, notes, f"{label}.height")
        if w < 0:
            notes.append(f"{label}: negative width, using its absolute value")
            x, w = x + w, -w
        if h < 0:
            notes.append(f"{label}: negative height, using its absolute value")
            y, h = y + h, -h
        rotation = _number(raw.get("rotation"), 0.0, notes, f"{label}.rotation")
        rotation = ((rotation + 180.0) % 360.0) - 180.0
        opacity = min(max(_number(raw.get("opacity"), 1.0, notes, f"{label}.opacity"), 0.0), 1.0)
        z = int(_number(raw.get("z_index"), 0.0, notes, f"{label}.z_index"))
        etype = raw.get("type") if raw.get("type") in ("text", "image", "shape") else "shape"
        el = Element(index=i, raw=raw, id=eid, type=etype, x=x, y=y, width=w, height=h,
                     rotation=rotation, opacity=opacity, z_index=z,
                     props={k: v for k, v in raw.items() if k not in GEOMETRY})
        if etype == "text":
            el.font_size = max(_number(raw.get("font_size"), 16.0, notes, f"{label}.font_size"), 1.0)
            el.props["content"] = str(raw.get("content", ""))
            el.props["line_height"] = _number(raw.get("line_height"), 1.2, notes, f"{label}.line_height") or 1.2
        out.append(el)
    return out
