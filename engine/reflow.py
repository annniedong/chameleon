"""The primary layout layer: reflow the design for the new shape.

For each candidate plan from engine/templates.py it fits the layout tree to
the canvas, places backgrounds and decorations around it, repairs anything
that slipped (clamp, text fit), and keeps the best-scoring result that
passes the engine's own hard-rule check.
"""

from __future__ import annotations

import copy
import math
import os
import sys

from engine import check
from engine.fit import fit_text
from engine.geometry import clamp_into, keeps_aspect
from engine.layout import fit_scale
from engine.normalize import Element
from engine.score import score
from engine.solve import refine
from engine.templates import plans
from engine.understand import DesignModel, understand


def _place_background(model: DesignModel, out: list[Element], W: float, H: float) -> None:
    for u in model.by_role("background"):
        for m in u.members:
            el = out[m.index]
            if keeps_aspect(el) and m.width > 0 and m.height > 0:
                s = min(W / m.width, H / m.height)
                el.width, el.height = m.width * s, m.height * s
                el.x, el.y = (W - el.width) / 2, (H - el.height) / 2
            else:
                el.x, el.y, el.width, el.height = 0.0, 0.0, W, H


def _place_decorations(model: DesignModel, out: list[Element], src_w: float, src_h: float,
                       W: float, H: float) -> None:
    """Decorations keep their relative position and scale with the short side,
    a little larger the more the shape changes, to help fill new space."""
    shape_change = abs(math.log((W / H) / (src_w / src_h)))
    s = min(W / src_w, H / src_h) * (1 + 0.15 * min(1.0, shape_change / math.log(2)))
    for u in model.by_role("decoration"):
        for m in u.members:
            el = out[m.index]
            cx = (m.x + m.width / 2) / src_w * W
            cy = (m.y + m.height / 2) / src_h * H
            el.width, el.height = m.width * s, m.height * s
            el.x, el.y = cx - el.width / 2, cy - el.height / 2


def _proportional(model: DesignModel, elements: list[Element], src_w: float, src_h: float,
                  W: float, H: float) -> list[Element]:
    """Keep the designer's arrangement: every unit keeps its relative position
    and scales uniformly with the short side. Right for same or similar shapes."""
    out = copy.deepcopy(elements)
    s = min(W / src_w, H / src_h)
    for u in model.units:
        if u.role in ("background", "decoration"):
            continue
        cx, cy = u.center
        ncx, ncy = cx / src_w * W, cy / src_h * H
        for m in u.members:
            el = out[m.index]
            el.width, el.height = m.width * s, m.height * s
            el.x = ncx + (m.x + m.width / 2 - cx) * s - el.width / 2
            el.y = ncy + (m.y + m.height / 2 - cy) * s - el.height / 2
            if m.font_size is not None:
                el.font_size = max(m.font_size * s, 1.0)
    return out


def _finish(model: DesignModel, out: list[Element], src_w: float, src_h: float, W: float, H: float) -> None:
    _place_background(model, out, W, H)
    _place_decorations(model, out, src_w, src_h, W, H)
    for el in out:
        clamp_into(el, W, H)
        fit_text(el, W, H)
        clamp_into(el, W, H)


def candidates(elements: list[Element], src_w: float, src_h: float, W: float, H: float,
               model: DesignModel | None = None):
    """Yield (plan name, laid-out elements, score breakdown) for each plan
    that fits and passes the hard-rule check."""
    model = model or understand(elements, src_w, src_h)
    ref = math.sqrt((W * H) / (src_w * src_h))
    shape_change = abs(math.log((W / H) / (src_w / src_h)))
    if shape_change < math.log(1.35):
        out = _proportional(model, elements, src_w, src_h, W, H)
        _finish(model, out, src_w, src_h, W, H)
        if not check.problems(out, elements, W, H):
            result = score(model, out, src_w, src_h, W, H)
            if shape_change < 0.02:
                result["total"] += 10.0  # same shape: the source layout is the answer
            yield "proportional", out, result
    for plan in plans(model, src_w, src_h, W, H):
        x, y, w, h = plan.region
        # Grow with the canvas, but not far past it: oversized type looks shouty.
        k = fit_scale(plan.root, w, h, k_max=1.2 * ref)
        if k is None:
            continue
        out = copy.deepcopy(elements)
        plan.root.place(out, x, y, w, h, k)
        _finish(model, out, src_w, src_h, W, H)
        if check.problems(out, elements, W, H):
            continue
        yield plan.name, out, score(model, out, src_w, src_h, W, H)


REFINE_TOP = 3
# Name of the plan the last reflow_layout() call chose, for tools and reports.
last_choice: str | None = None


def _debug(msg: str) -> None:
    if os.environ.get("CHAMELEON_DEBUG"):
        print(f"[reflow] {msg}", file=sys.stderr)


def reflow_layout(elements: list[Element], src_w: float, src_h: float, W: float, H: float) -> None:
    """Replace `elements` in place with the best reflowed layout.
    Raises if no plan works, so resize() falls through to a safer layer.

    Every template candidate is scored; the best few are then refined with
    z3 (engine/solve.py) and re-scored, and the overall best wins."""
    model = understand(elements, src_w, src_h)
    pool = []
    for name, out, s in candidates(elements, src_w, src_h, W, H, model):
        _debug(f"{name}: " + ", ".join(f"{k}={v:.2f}" for k, v in s.items()))
        pool.append((name, out, s))
    if not pool:
        raise RuntimeError("no reflow plan fits")

    pool.sort(key=lambda c: c[2]["total"], reverse=True)
    for name, out, s in list(pool[:REFINE_TOP]):
        if name == "proportional" and s["total"] >= 10.0:
            continue  # same shape: keep the designer's layout untouched
        try:
            refined = refine(model, out, src_w, src_h, W, H)
        except Exception as exc:  # z3 problems never cost us the candidate
            _debug(f"{name}: refine failed ({exc!r})")
            continue
        if refined is None:
            continue
        _finish(model, refined, src_w, src_h, W, H)
        if check.problems(refined, elements, W, H):
            continue
        rs = score(model, refined, src_w, src_h, W, H)
        _debug(f"{name}+solve: " + ", ".join(f"{k}={v:.2f}" for k, v in rs.items()))
        pool.append((f"{name}+solve", refined, rs))

    best = max(pool, key=lambda c: c[2]["total"])
    global last_choice
    last_choice = best[0]
    _debug(f"chose {best[0]}")
    elements[:] = best[1]
