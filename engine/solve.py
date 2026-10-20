"""Refine a candidate layout with z3 (MaxSMT).

Templates produce a good arrangement, but sizes come from one global scale,
so a headline cannot grow into free space and small print cannot claim a
readable size. This pass treats each unit as a box with its own position
and scale and asks z3 for the best layout near the template's:

  hard   inside the canvas; units that did not touch in the source do not
         touch now; each unit scales between a floor and a growth cap
  soft   readable size for each unit's text (tracked assumptions). When the
         canvas cannot satisfy all of them, the unsat core names the
         conflict and the least important unit's floor is dropped, then we
         solve again: principled degradation instead of a silent squeeze.
  goal   maximise importance-weighted size, minus drift of each unit's
         centre from where the template put it

Units scale uniformly, so text keeps its line breaks and images their
proportions. Any failure (timeout, no model) leaves the candidate as it was.
"""

from __future__ import annotations

import copy
import math

import z3

from engine import textmetrics
from engine.normalize import Element
from engine.understand import DesignModel, Unit

READABLE_PX = 9.0
TIMEOUT_MS = 2500
GROWTH_CAP = 1.6
SHRINK_FLOOR = 0.35


def _box(out: list[Element], u: Unit) -> tuple[float, float, float, float]:
    ms = [out[m.index] for m in u.members]
    return (min(m.x for m in ms), min(m.y for m in ms),
            max(m.x + m.width for m in ms), max(m.y + m.height for m in ms))


def _overlaps(a: tuple[float, ...], b: tuple[float, ...], pad: float = 0.0) -> bool:
    return (min(a[2], b[2]) - max(a[0], b[0]) > -pad) and (min(a[3], b[3]) - max(a[1], b[1]) > -pad)


def _allowance(u: Unit, out: list[Element]) -> float:
    """Fixed width (px) in a lone text box that does not scale with the font."""
    if len(u.members) != 1 or u.members[0].type != "text" or u.rotation:
        return 0.0
    el = out[u.members[0].index]
    box = textmetrics.measure(el.text_props(font_size=el.font_size, width=el.width, height=el.height))
    return textmetrics.HINTING_ALLOWANCE_PX * box.max_line_chars


def _num(v: z3.ArithRef, model: z3.ModelRef) -> float:
    r = model.eval(v, model_completion=True)
    return float(r.numerator_as_long()) / float(r.denominator_as_long()) if z3.is_rational_value(r) \
        else float(r.as_decimal(12).rstrip("?"))


def refine(model: DesignModel, out: list[Element], src_w: float, src_h: float,
           W: float, H: float) -> list[Element] | None:
    units = [u for u in model.units if u.role not in ("background", "decoration")]
    if len(units) < 1:
        return None
    ref = math.sqrt((W * H) / (src_w * src_h))
    margin = max(4.0, 0.05 * min(W, H))
    gap = max(2.0, 0.012 * min(W, H))
    boxes = {u.id: _box(out, u) for u in units}

    opt = z3.Optimize()
    opt.set("timeout", TIMEOUT_MS)
    xs, ys, ss = {}, {}, {}
    allowance: dict[str, float] = {}
    floors: dict[str, z3.BoolRef] = {}
    objective = []
    for u in units:
        x0, y0, x1, y1 = boxes[u.id]
        bw, bh = max(x1 - x0, 1e-3), max(y1 - y0, 1e-3)
        x, y, s = z3.Real(f"x_{u.id}"), z3.Real(f"y_{u.id}"), z3.Real(f"s_{u.id}")
        xs[u.id], ys[u.id], ss[u.id] = x, y, s
        # A lone text's width is glyphs (which scale with s) plus a fixed
        # per-glyph allowance (which does not); see textmetrics._char_widths.
        # Width(s) = bw*s + A*(1 - s) keeps its line breaks at any scale.
        allowance[u.id] = _allowance(u, out)
        # Margins: never demand more than the candidate already gave this unit.
        m = max(0.0, min(margin, x0, y0, W - x1, H - y1))
        w_expr = bw * s + allowance[u.id] * (1 - s)
        opt.add(x >= m, y >= m, x + w_expr <= W - m, y + bh * s <= H - m)
        # Growth is capped relative to the source, like the template scale.
        current = math.sqrt((bw * bh) / max(u.area, 1.0))
        cap = max(1.0, min(GROWTH_CAP, 1.2 * ref / max(current, 1e-6)))
        opt.add(s >= SHRINK_FLOOR, s <= cap)
        fonts = [out[m_.index].font_size or 0.0 for m_ in u.texts]
        if fonts and min(fonts) < READABLE_PX:
            lit = z3.Bool(f"readable_{u.id}")
            floors[u.id] = lit
            opt.add(z3.Implies(lit, s >= min(cap, READABLE_PX / max(min(fonts), 1e-3))))
        # Objective terms: size by importance, drift of the centre.
        cx0, cy0 = (x0 + x1) / 2, (y0 + y1) / 2
        dx, dy = z3.Real(f"dx_{u.id}"), z3.Real(f"dy_{u.id}")
        cx, cy = x + w_expr / 2, y + bh * s / 2
        opt.add(dx >= cx - cx0, dx >= cx0 - cx, dy >= cy - cy0, dy >= cy0 - cy)
        objective.append(100 * u.importance * s - 40 * u.importance * (dx / W + dy / H))

    # No new collisions: pairs apart in the source stay apart, on the same
    # side the template put them. Fixing the side keeps the arrangement and
    # turns each "left or right or above or below" choice into one linear
    # constraint, so the whole problem is a linear program z3 solves quickly.
    for i, a in enumerate(units):
        for b in units[i + 1:]:
            if _overlaps(a.box, b.box) or _overlaps(boxes[a.id], boxes[b.id]):
                continue
            ax0, ay0, ax1, ay1 = boxes[a.id]
            bx0, by0, bx1, by1 = boxes[b.id]
            aw, ah, bw_, bh_ = ax1 - ax0, ay1 - ay0, bx1 - bx0, by1 - by0
            xa, ya, sa, xb, yb, sb = xs[a.id], ys[a.id], ss[a.id], xs[b.id], ys[b.id], ss[b.id]
            g = min(gap, max(bx0 - ax1, ax0 - bx1, by0 - ay1, ay0 - by1, 0.0))
            wa = aw * sa + allowance[a.id] * (1 - sa)
            wb = bw_ * sb + allowance[b.id] * (1 - sb)
            sides = [(bx0 - ax1, xa + wa + g <= xb), (ax0 - bx1, xb + wb + g <= xa),
                     (by0 - ay1, ya + ah * sa + g <= yb), (ay0 - by1, yb + bh_ * sb + g <= ya)]
            opt.add(max(sides, key=lambda side: side[0])[1])

    opt.maximize(z3.Sum(objective))

    # Readability floors are assumptions: drop the least important on conflict.
    active = dict(floors)
    importance = {u.id: u.importance for u in units}
    for _ in range(len(floors) + 1):
        result = opt.check(*active.values())
        if result == z3.sat:
            break
        if result != z3.unsat or not active:
            return None
        core = {str(c) for c in opt.unsat_core()}
        blamed = [uid for uid, lit in active.items() if str(lit) in core] or list(active)
        active.pop(min(blamed, key=lambda uid: importance[uid]))
    else:
        return None
    zm = opt.model()

    refined = copy.deepcopy(out)
    for u in units:
        x0, y0, _, _ = boxes[u.id]
        nx, ny, s = _num(xs[u.id], zm), _num(ys[u.id], zm), _num(ss[u.id], zm)
        for m_ in u.members:
            src, el = out[m_.index], refined[m_.index]
            el.x = nx + (src.x - x0) * s
            el.y = ny + (src.y - y0) * s
            el.width, el.height = src.width * s + allowance[u.id] * (1 - s), src.height * s
            if src.font_size is not None:
                el.font_size = max(src.font_size * s, 1.0)
    return refined
