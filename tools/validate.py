"""Strict validator for resize() output.

Stricter than engine/main.py's self-check: it covers every structural rule
in RULES.md plus a few quality checks the AI evaluator is likely to punish.

Severity:
  error  breaks a stated rule (ids, required fields, off-canvas, aspect,
         changed content/font, unreadable font size, text that overflows)
  warn   allowed but probably costs points (new collisions, tiny text,
         changed colours, rotated corners poking out)
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Any

from engine import textmetrics

REQUIRED_COMMON = ("id", "type", "x", "y", "width", "height")
REQUIRED_BY_TYPE = {
    "text": ("content", "font_family", "font_size", "color"),
    "image": ("asset_id",),
    "shape": ("shape_type", "fill_color"),
}
# Fields that identify an element's content. Changing one means the output
# no longer shows the same thing.
FROZEN = ("type", "content", "font_family", "asset_id", "fit", "shape_type")
# Brand styling. Changing these is not forbidden, but is unfaithful.
STYLED = ("color", "fill_color", "stroke_color", "font_weight", "font_style", "style_runs", "group_id")

EDGE_TOLERANCE = 0.5
ASPECT_TOLERANCE = 0.05
MIN_READABLE_PX = 7.0


@dataclass(frozen=True)
class Issue:
    severity: str
    element: str
    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.element}: {self.code} - {self.message}"


def _finite(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _box(el: dict[str, Any]) -> tuple[float, float, float, float]:
    return float(el["x"]), float(el["y"]), float(el["x"]) + float(el["width"]), float(el["y"]) + float(el["height"])


def _rotated_box(el: dict[str, Any]) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = _box(el)
    angle = math.radians(float(el.get("rotation", 0) or 0))
    if angle == 0:
        return x0, y0, x1, y1
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    hw, hh = (x1 - x0) / 2, (y1 - y0) / 2
    ex = abs(hw * math.cos(angle)) + abs(hh * math.sin(angle))
    ey = abs(hw * math.sin(angle)) + abs(hh * math.cos(angle))
    return cx - ex, cy - ey, cx + ex, cy + ey


def _overlap_area(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def validate(source: dict[str, Any], output: Any, target: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []

    def add(sev: str, el: str, code: str, msg: str) -> None:
        issues.append(Issue(sev, el, code, msg))

    if not isinstance(output, dict) or "canvas" not in output or "elements" not in output:
        add("error", "-", "shape", "output must be an object with 'canvas' and 'elements'")
        return issues

    canvas = output["canvas"]
    W, H = target["width"], target["height"]
    if not isinstance(canvas, dict) or canvas.get("width") != W or canvas.get("height") != H:
        add("error", "-", "canvas", f"canvas is {canvas}, expected {W}x{H}")

    src_ids = Counter(e.get("id") for e in source["elements"])
    out_ids = Counter(e.get("id") for e in output["elements"] if isinstance(e, dict))
    if src_ids != out_ids:
        missing = sorted(map(str, (src_ids - out_ids).elements()))
        extra = sorted(map(str, (out_ids - src_ids).elements()))
        add("error", "-", "ids", f"missing {missing}, unexpected {extra}")

    src_by_id = {}
    for e in source["elements"]:
        src_by_id.setdefault(e.get("id"), e)

    placed: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for el in output["elements"]:
        if not isinstance(el, dict):
            add("error", "-", "shape", "element is not an object")
            continue
        eid = str(el.get("id"))
        src = src_by_id.get(el.get("id"))

        missing = [f for f in REQUIRED_COMMON + REQUIRED_BY_TYPE.get(el.get("type"), ()) if f not in el]
        if missing:
            add("error", eid, "fields", f"missing {missing}")
            continue
        bad = [f for f in ("x", "y", "width", "height") if not _finite(el[f])]
        if bad:
            add("error", eid, "numbers", f"non-numeric or non-finite {bad}")
            continue
        if el["width"] < 0 or el["height"] < 0:
            add("error", eid, "size", "negative width or height")
            continue

        x0, y0, x1, y1 = _box(el)
        if x0 < -EDGE_TOLERANCE or y0 < -EDGE_TOLERANCE or x1 > W + EDGE_TOLERANCE or y1 > H + EDGE_TOLERANCE:
            add("error", eid, "off-canvas", f"box ({x0:.0f},{y0:.0f})-({x1:.0f},{y1:.0f}) outside {W}x{H}")
        else:
            rx0, ry0, rx1, ry1 = _rotated_box(el)
            if rx0 < -EDGE_TOLERANCE or ry0 < -EDGE_TOLERANCE or rx1 > W + EDGE_TOLERANCE or ry1 > H + EDGE_TOLERANCE:
                add("warn", eid, "rotated-corner", "rotated corners extend past the canvas")

        if src is None:
            continue
        for f in FROZEN:
            if src.get(f) != el.get(f):
                add("error", eid, "changed", f"'{f}' changed from {src.get(f)!r} to {el.get(f)!r}")
        for f in STYLED:
            if src.get(f) != el.get(f):
                add("warn", eid, "restyled", f"'{f}' changed")

        if el["type"] == "image" and el.get("fit", "contain") != "cover":
            sw, sh, ow, oh = src["width"], src["height"], el["width"], el["height"]
            if sw > 0 and sh > 0:
                if ow <= 0 or oh <= 0:
                    add("error", eid, "aspect", "image collapsed to zero size")
                else:
                    drift = abs((ow / oh) / (sw / sh) - 1)
                    if drift > ASPECT_TOLERANCE:
                        add("error", eid, "aspect", f"aspect ratio off by {drift:.1%}")

        if el["type"] == "text":
            size = el["font_size"]
            if not _finite(size) or size < 1:
                add("error", eid, "font-size", f"font_size {size!r} below 1")
            else:
                if size < MIN_READABLE_PX and str(el.get("content", "")).strip():
                    add("warn", eid, "tiny-text", f"font_size {size:.1f}px is hard to read")
                if el["width"] > 0 and not textmetrics.fits(el):
                    box = textmetrics.measure(el)
                    add(
                        "error",
                        eid,
                        "text-overflow",
                        f"{box.lines} line(s) need {box.max_line_width:.0f}x{box.height:.0f}, "
                        f"box is {el['width']:.0f}x{el['height']:.0f}",
                    )
        placed.append((el, src))

    # New collisions: text that overlaps another text element in the output
    # but did not overlap it in the source. Same-group pairs are intended.
    texts = [(el, src) for el, src in placed if el["type"] == "text"]
    for i, (a, sa) in enumerate(texts):
        for b, sb in texts[i + 1 :]:
            if a.get("group_id") and a.get("group_id") == b.get("group_id"):
                continue
            if _overlap_area(_box(a), _box(b)) > 1 and _overlap_area(_box(sa), _box(sb)) <= 1:
                add("warn", str(a["id"]), "collision", f"text overlaps '{b['id']}'")
    return issues


def errors(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == "error"]
