"""Score a finished layout. Higher is better.

A stand-in for the AI evaluator until the vision-model reranker exists.
It rewards what the rubric names and penalises what it punishes:

  size        important units keep (or gain) their share of the canvas
  overlap     foreground units that collide (decorations may overlap)
  legibility  text below a readable size, weighted by importance
  hierarchy   another text ending up larger than the headline
  balance     content huddled in a small or off-centre part of the canvas
  drift       units moving far from their relative place in the source
  order       reading order changed (top-to-bottom may become left-to-right,
              but what came first should still come first)
"""

from __future__ import annotations

import math

from engine.normalize import Element
from engine.understand import DesignModel, Unit

READABLE_PX = 9.0


def _box(els: list[Element], u: Unit) -> tuple[float, float, float, float]:
    ms = [els[m.index] for m in u.members]
    return (min(m.x for m in ms), min(m.y for m in ms),
            max(m.x + m.width for m in ms), max(m.y + m.height for m in ms))


def _area(b: tuple[float, float, float, float]) -> float:
    return max(b[2] - b[0], 0.0) * max(b[3] - b[1], 0.0)


def _overlap(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def score(model: DesignModel, els: list[Element], src_w: float, src_h: float, W: float, H: float) -> dict[str, float]:
    content = [u for u in model.units if u.role not in ("background", "decoration")]
    if not content:
        return {"total": 0.0}
    ref = math.sqrt((W * H) / (src_w * src_h))
    boxes = {u.id: _box(els, u) for u in content}

    weight = sum(u.importance for u in content) or 1.0
    size = sum(u.importance * min(math.sqrt(_area(boxes[u.id]) / max(u.area, 1.0)) / ref, 1.2)
               for u in content) / weight

    overlap = 0.0
    for i, a in enumerate(content):
        for b in content[i + 1:]:
            inter = _overlap(boxes[a.id], boxes[b.id])
            if inter > 1:
                smaller = max(min(_area(boxes[a.id]), _area(boxes[b.id])), 1.0)
                overlap += (inter / smaller) * max(a.importance, b.importance)

    legibility = 0.0
    for u in content:
        for m in u.texts:
            f = els[m.index].font_size or 0.0
            if f < READABLE_PX and str(m.props.get("content", "")).strip():
                legibility += (READABLE_PX - f) / READABLE_PX * u.importance

    hierarchy = 0.0
    headlines = [u for u in content if u.role == "headline"]
    if headlines:
        top = max(els[m.index].font_size or 0.0 for m in headlines[0].texts)
        for u in content:
            if u.role not in ("headline",) and u.texts:
                if max(els[m.index].font_size or 0.0 for m in u.texts) > top * 1.05:
                    hierarchy += 0.3

    xs0, ys0, xs1, ys1 = zip(*boxes.values())
    union = (min(xs0), min(ys0), max(xs1), max(ys1))
    coverage = _area(union) / (W * H)
    off_centre = abs((union[0] + union[2]) / 2 - W / 2) / W + abs((union[1] + union[3]) / 2 - H / 2) / H
    balance = max(0.0, 0.55 - coverage) + 0.5 * off_centre

    drift = sum(u.importance * math.hypot((boxes[u.id][0] + boxes[u.id][2]) / 2 / W - u.center[0] / src_w,
                                          (boxes[u.id][1] + boxes[u.id][3]) / 2 / H - u.center[1] / src_h)
                for u in content) / weight

    order = _order_flips(model, content, boxes, H)

    total = size - 2.0 * overlap - 0.6 * legibility - hierarchy - balance - 0.4 * drift - 0.3 * order
    return {"total": total, "size": size, "overlap": overlap, "legibility": legibility,
            "hierarchy": hierarchy, "balance": balance, "drift": drift, "order": order}


def _order_flips(model: DesignModel, content: list[Unit], boxes: dict, H: float) -> float:
    """Share of unit pairs whose reading order flipped, importance-weighted."""
    src_rank = {u.id: i for i, u in enumerate(model.reading_order)}
    band = max(H * 0.04, 1.0)

    def out_key(u: Unit) -> tuple[float, float]:
        x0, y0, x1, y1 = boxes[u.id]
        return round(((y0 + y1) / 2) / band), (x0 + x1) / 2

    flips = total = 0.0
    for i, a in enumerate(content):
        for b in content[i + 1:]:
            if a.id not in src_rank or b.id not in src_rank:
                continue
            w = min(a.importance, b.importance)
            total += w
            if (src_rank[a.id] < src_rank[b.id]) != (out_key(a) < out_key(b)):
                flips += w
    return flips / total if total else 0.0
