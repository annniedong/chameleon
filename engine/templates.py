"""Turn a design model into candidate layout trees for a target shape.

Each template answers "how should this design arrange on a canvas of this
shape?" using roles, importance and the source's reading order:

  column   everything stacked in reading order; units that shared a row in
           the source stay in a row (portrait, square, skyscraper)
  split    the hero image in one panel, everything else in a text column
           beside it (landscape)
  strip    a single row: logo, hero, headline block, badges, CTA, and the
           minor parts tucked at the end (leaderboards and thin banners)

Minor units (badges, small print, secondary images and logos) take a
`minor` scale multiplier so templates can trade their size for the
headline's when space is tight.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from engine.layout import Column, Node, Rigid, Row, Text, Wrap
from engine.understand import DesignModel, Unit

FIXED_ROLES = ("background", "decoration")


@dataclass
class Plan:
    name: str
    root: Node
    region: tuple[float, float, float, float]  # x, y, w, h the tree fills


def is_minor(u: Unit) -> bool:
    return u.role in ("badge", "legal", "image", "body") or (u.role == "logo" and not u.primary)


def content_units(model: DesignModel) -> list[Unit]:
    return [u for u in model.reading_order if u.role not in FIXED_ROLES]


def node_for(u: Unit, mult: float, gap: float, align: str = "center") -> Node:
    if u.role == "logo" and u.kind == "strip":
        heights = [l.height for l in u.logos if l.height > 0] or [1.0]
        h = median(heights)
        logos = Wrap([Rigid([l], mult * h / max(l.height, 1e-6)) for l in u.logos], gap * 0.6)
        extras = [m for m in u.members if m not in u.logos]
        if not extras:
            return logos
        return Column([Text(m, mult, align) if m.type == "text" else Rigid([m], mult) for m in extras] + [logos],
                      gap * 0.5, align)
    if len(u.members) == 1 and u.members[0].type == "text" and not u.rotation:
        short = len(u.text.split()) <= 5 and u.role in ("cta", "badge", "logo", "body", "legal") \
            or len(u.text.split()) <= 3
        return Text(u.members[0], mult, align, nowrap=short and u.role != "headline")
    return Rigid(u.members, mult, align)


def _items(units: list[Unit], model: DesignModel, minor: float, gap: float,
           align: str) -> list[tuple[list[Unit], Node]]:
    """One node per unit, except members of a repeated set share one Wrap."""
    items: list[tuple[list[Unit], Node]] = []
    done: set[str] = set()
    for u in units:
        if u.id in done:
            continue
        mult = minor if is_minor(u) else 1.0
        peers = model.sets.get(u.set_id or "", [])
        peers = [p for p in peers if p in units]
        if len(peers) >= 2:
            peers = sorted(peers, key=lambda p: (p.center[1], p.center[0]))
            items.append((peers, Wrap([Rigid(p.members, mult) for p in peers], gap * 0.6)))
            done.update(p.id for p in peers)
        else:
            items.append(([u], node_for(u, mult, gap, align)))
            done.add(u.id)
    return items


def _span(units: list[Unit]) -> tuple[float, float]:
    return min(u.box[1] for u in units), max(u.box[3] for u in units)


def _bands(items: list[tuple[list[Unit], Node]], gap: float) -> list[Node]:
    """Group items whose source rows overlap into Rows; keep the rest stacked."""
    bands: list[list[tuple[list[Unit], Node]]] = []
    for item in items:
        if bands:
            prev = [u for units, _ in bands[-1] for u in units]
            y0, y1 = _span(prev)
            a0, a1 = _span(item[0])
            v_overlap = min(y1, a1) - max(y0, a0)
            # Side by side in the source: overlapping rows, separate columns.
            x0, x1 = min(u.box[0] for u in prev), max(u.box[2] for u in prev)
            b0, b1 = min(u.box[0] for u in item[0]), max(u.box[2] for u in item[0])
            h_overlap = min(x1, b1) - max(x0, b0)
            if v_overlap > 0.3 * min(y1 - y0, a1 - a0) and h_overlap <= 0.1 * min(x1 - x0, b1 - b0):
                bands[-1].append(item)
                continue
        bands.append([item])
    out: list[Node] = []
    for band in bands:
        if len(band) == 1:
            out.append(band[0][1])
        else:
            ordered = sorted(band, key=lambda it: min(u.center[0] for u in it[0]))
            out.append(Row([node for _, node in ordered], gap))
    return out


def _margins(W: float, H: float) -> tuple[float, float]:
    short = min(W, H)
    return max(4.0, 0.05 * short), max(3.0, 0.035 * short)


def column(model: DesignModel, W: float, H: float, minor: float) -> Plan | None:
    units = content_units(model)
    if not units:
        return None
    m, g = _margins(W, H)
    root = Column(_bands(_items(units, model, minor, g, "center"), g), g)
    return Plan(f"column(minor={minor})", root, (m, m, W - 2 * m, H - 2 * m))


def split(model: DesignModel, W: float, H: float, minor: float, hero_first: bool | None = None) -> Plan | None:
    units = content_units(model)
    heroes = [u for u in units if u.role == "hero"]
    if not heroes or len(units) < 2:
        return None
    hero = max(heroes, key=lambda u: u.area)
    rest = [u for u in units if u is not hero]
    m, g = _margins(W, H)
    text_col = Column(_bands(_items(rest, model, minor, g, "center"), g), g)
    hero_node = Rigid(hero.members)
    if hero_first is None:
        rest_cx = sum(u.center[0] for u in rest) / len(rest)
        hero_first = hero.center[0] <= rest_cx
    children = [hero_node, text_col] if hero_first else [text_col, hero_node]
    side = "hero-left" if hero_first else "hero-right"
    return Plan(f"split({side}, minor={minor})", Row(children, 2 * g), (m, m, W - 2 * m, H - 2 * m))


def strip(model: DesignModel, W: float, H: float, minor: float) -> Plan | None:
    units = content_units(model)
    if not units:
        return None
    m, g = _margins(W, H)
    g = max(g, 0.012 * W)
    text_roles = ("headline", "subhead")
    texts = [u for u in units if u.role in text_roles]
    tiny = [u for u in units if u.role in ("legal", "image") or (u.role == "logo" and u.kind == "strip")]
    main = [u for u in units if u not in texts and u not in tiny]

    def x_of(u: Unit) -> float:
        # Logos lead and the CTA closes, whatever their source position.
        order = {"logo": 0, "hero": 1, "badge": 3, "body": 3, "cta": 4}
        return order.get(u.role, 2) + u.center[0] / (10 * model.width)

    children: list[Node] = []
    placed_text = False
    for units_, node in sorted(_items(main, model, minor, g, "center"), key=lambda it: min(x_of(u) for u in it[0])):
        if not placed_text and min(x_of(u) for u in units_) >= 2:
            if texts:
                children.append(Column([n for _, n in _items(texts, model, minor, g * 0.4, "center")], g * 0.4))
            placed_text = True
        children.append(node)
    if not placed_text and texts:
        children.append(Column([n for _, n in _items(texts, model, minor, g * 0.4, "center")], g * 0.4))
    if tiny:
        children.append(Column([n for _, n in _items(tiny, model, minor, g * 0.3, "center")], g * 0.3))
    return Plan(f"strip(minor={minor})", Row(children, g), (m, m, W - 2 * m, H - 2 * m))


def plans(model: DesignModel, src_w: float, src_h: float, W: float, H: float) -> list[Plan]:
    """Candidate trees worth trying for this source/target pair."""
    ratio = W / H
    builders = []
    if ratio >= 2.6:
        builders = [strip, split, column]
    elif ratio > 1.25:
        builders = [split, column, strip]
    else:
        builders = [column]
    out: list[Plan] = []
    for build in builders:
        for minor in (1.0, 0.7, 0.45):
            p = build(model, W, H, minor)
            if p is not None:
                out.append(p)
    return out
