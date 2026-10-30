"""CHAMELEON engine entry point.

resize() must never raise: engine/main.py has no try/except around it, so
one exception would abort every remaining task. It runs layout layers from
best to safest and returns the first result that passes the engine's own
check (engine/check.py):

  1. reflow           rearrange for the new shape (engine/reflow.py)
  2. safe layout      uniform scale-to-fit, then repair
  3. last resort      boxes clamped into the canvas, no text measurement
  4. pass-through     source elements unchanged, only the canvas replaced

Set CHAMELEON_DEBUG=1 to print which layer won and every input repair.
"""

from __future__ import annotations

import copy
import math
import os
import sys
from typing import Any, Callable

from engine import check, reflow
from engine.fallback import last_resort, safe_layout
from engine.normalize import Element, normalize_canvas, normalize_elements
from engine.reflow import reflow_layout

Layer = Callable[[list[Element], float, float, float, float], None]


def _layers() -> list[tuple[str, Layer, bool]]:
    """(name, layout function, whether the check measures text)."""
    return [
        ("reflow", reflow_layout, True),
        ("safe", safe_layout, True),
        ("last-resort", lambda els, sw, sh, W, H: last_resort(els, W, H), False),
    ]


def _round(v: float) -> float:
    return round(float(v), 2)


def _quantize(layout: list[Element], W: float, H: float) -> None:
    """Round to the 2 decimals we emit, in the safe direction: boxes up and
    font sizes down, so text that fit before rounding still fits after.
    The check then runs on exactly the values that will be written out."""
    for el in layout:
        el.width = math.ceil(el.width * 100) / 100
        el.height = math.ceil(el.height * 100) / 100
        el.x = min(round(el.x, 2), math.floor((W - el.width) * 100) / 100)
        el.y = min(round(el.y, 2), math.floor((H - el.height) * 100) / 100)
        el.x, el.y = max(el.x, 0.0), max(el.y, 0.0)
        if el.font_size is not None:
            el.font_size = max(math.floor(el.font_size * 100) / 100, 1.0)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _emit(layout: list[Element], target: dict[str, Any], W: float, H: float) -> dict[str, Any]:
    """Write the layout back onto copies of the raw elements, so every field
    we did not deliberately change (content, colours, unknown extras) passes
    through exactly as it came in."""
    elements = []
    for el in layout:
        out = copy.deepcopy(el.raw)
        out["x"], out["y"] = _round(el.x), _round(el.y)
        out["width"], out["height"] = _round(el.width), _round(el.height)
        if el.type == "text" and el.font_size is not None:
            out["font_size"] = _round(el.font_size)
        for key, value in (("rotation", el.rotation), ("opacity", el.opacity), ("z_index", el.z_index)):
            if key in out and not _is_number(out[key]):
                out[key] = value
        elements.append(out)
    width = target.get("width") if isinstance(target.get("width"), int) else int(W)
    height = target.get("height") if isinstance(target.get("height"), int) else int(H)
    return {"canvas": {"width": width, "height": height}, "elements": elements}


def _debug(msg: str) -> None:
    if os.environ.get("CHAMELEON_DEBUG"):
        print(f"[resize] {msg}", file=sys.stderr)


def resize(source: dict[str, Any], target_canvas: dict[str, Any]) -> dict[str, Any]:
    reflow.last_choice = None
    reflow.last_stats = {}
    notes: list[str] = []
    try:
        source = source if isinstance(source, dict) else {}
        target_canvas = target_canvas if isinstance(target_canvas, dict) else {}
        src_w, src_h = normalize_canvas(source.get("canvas"), notes)
        W, H = normalize_canvas(target_canvas, notes)
        original = normalize_elements(source.get("elements"), notes)
    except Exception as exc:  # pragma: no cover - parsing itself is defensive
        _debug(f"could not parse input ({exc!r}); passing it through")
        return {"canvas": dict(target_canvas), "elements": list(source.get("elements") or [])}

    for note in notes:
        _debug(note)

    fallback: dict[str, Any] | None = None
    for name, layer, check_text in _layers():
        layout = copy.deepcopy(original)
        try:
            layer(layout, src_w, src_h, W, H)
            _quantize(layout, W, H)
            found = check.problems(layout, original, W, H, check_text=check_text)
            result = _emit(layout, target_canvas, W, H)
        except Exception as exc:
            _debug(f"layer '{name}' raised {exc!r}")
            continue
        if not found:
            _debug(f"layer '{name}' passed")
            return result
        _debug(f"layer '{name}' rejected: {found[:5]}")
        fallback = fallback or result

    if fallback is not None:
        return fallback
    _debug("every layer failed; passing the source through")
    return {"canvas": {"width": int(W), "height": int(H)}, "elements": copy.deepcopy(source.get("elements") or [])}
