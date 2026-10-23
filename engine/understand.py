"""Work out what each part of a design is, from geometry, style and text.

The input carries no semantic labels, only type, geometry, grouping and
styling. This module rebuilds the structure a designer would see:

  units          elements that travel together: an explicit group_id, a text
                 sitting inside a shape (a button or a pill), or a row of
                 logos with its separators and label
  roles          background, hero, logo, headline, subhead, body, cta,
                 badge, legal, decoration, image
  logo kinds     a single logo, a co-brand "lockup" (equal partners, often
                 split by "x", "+" or "|"), or a sponsor "strip"; one logo
                 or lockup is the primary brand mark
  importance     0..1, who keeps space when space runs out
  sets           repeated siblings, such as a row of badges
  reading order  top-to-bottom, left-to-right
  alignment      whether the design is a centred column or left-aligned

Everything here is a heuristic. A wrong role only changes soft preferences
in the layout, never whether the output is valid.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from engine.normalize import Element

ASSETS_DIR = Path(__file__).resolve().parent.parent / "brand_kit" / "assets"

ROLES = ("background", "hero", "logo", "headline", "subhead", "body", "cta",
         "badge", "legal", "decoration", "image")

IMPORTANCE = {
    "headline": 1.0, "hero": 0.9, "cta": 0.85, "logo": 0.8, "subhead": 0.6,
    "body": 0.5, "badge": 0.45, "image": 0.35, "legal": 0.2, "decoration": 0.1,
    "background": 0.0,
}
# Logos that are not the primary brand mark, and sponsor strips.
SECONDARY_LOGO_IMPORTANCE = 0.5
STRIP_IMPORTANCE = 0.45

ACTION_WORDS = re.compile(
    r"^\s*(shop|buy|get|order|learn|sign|join|try|start|book|download|discover|explore|see|view|"
    r"register|subscribe|call|claim|save|reserve|watch|read|find|grab|apply|donate|play|install|"
    r"contact|visit|browse)\b",
    re.IGNORECASE,
)
LEGAL_WORDS = re.compile(
    r"terms|conditions|responsibly|prohibited|rights reserved|©|copyright|void where|"
    r"privacy|disclaimer|restrictions apply|see .* for details|\*",
    re.IGNORECASE,
)
SEPARATOR_TEXT = re.compile(r"^\s*([×xX+|&/·•:]|\+\+|—|–|-)\s*$")
LOGO_LABEL = re.compile(
    r"^\s*(presented by|sponsored by|in partnership with|in association with|powered by|"
    r"brought to you by|with support from|supported by|official partners?|our partners|partners?:?|"
    r"in collaboration with|co-presented by|as seen (on|in))\b",
    re.IGNORECASE,
)


@dataclass
class Unit:
    """One or more elements that move as a single piece."""

    id: str
    members: list[Element]
    role: str = "body"
    confidence: float = 0.5
    importance: float = 0.5
    set_id: str | None = None
    why: str = ""
    # For logos: "single", "lockup" or "strip", and whether it is the brand's
    # primary mark. `logos` lists the logo images in left-to-right order.
    kind: str = "single"
    primary: bool = False
    logos: list[Element] = field(default_factory=list)

    @property
    def box(self) -> tuple[float, float, float, float]:
        return (min(m.x for m in self.members), min(m.y for m in self.members),
                max(m.x + m.width for m in self.members), max(m.y + m.height for m in self.members))

    @property
    def width(self) -> float:
        x0, _, x1, _ = self.box
        return x1 - x0

    @property
    def height(self) -> float:
        _, y0, _, y1 = self.box
        return y1 - y0

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.box
        return (x0 + x1) / 2, (y0 + y1) / 2

    @property
    def texts(self) -> list[Element]:
        return [m for m in self.members if m.type == "text"]

    @property
    def text(self) -> str:
        return " ".join(str(m.props.get("content", "")) for m in self.texts).strip()

    @property
    def font_size(self) -> float:
        return max((m.font_size or 0.0 for m in self.texts), default=0.0)

    @property
    def rotation(self) -> float:
        return max((abs(m.rotation) for m in self.members), default=0.0)


@dataclass
class DesignModel:
    width: float
    height: float
    units: list[Unit]
    sets: dict[str, list[Unit]] = field(default_factory=dict)
    alignment: str = "center"  # "center" or "left"

    def by_role(self, role: str) -> list[Unit]:
        return [u for u in self.units if u.role == role]

    def unit_of(self, element_index: int) -> Unit | None:
        for u in self.units:
            if any(m.index == element_index for m in u.members):
                return u
        return None

    @property
    def primary_logo(self) -> Unit | None:
        return next((u for u in self.units if u.role == "logo" and u.primary), None)

    @property
    def reading_order(self) -> list[Unit]:
        """Top to bottom in bands, then left to right within a band."""
        band = max(self.height * 0.04, 1.0)
        fg = [u for u in self.units if u.role != "background"]
        return sorted(fg, key=lambda u: (round(u.center[1] / band), u.center[0]))


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------


def _contains(outer: Element, inner: Element, slack: float = 0.15) -> bool:
    """True when `inner` sits (almost) entirely inside `outer`."""
    pad_x, pad_y = outer.width * slack, outer.height * slack
    return (inner.x >= outer.x - pad_x and inner.y >= outer.y - pad_y
            and inner.x + inner.width <= outer.x + outer.width + pad_x
            and inner.y + inner.height <= outer.y + outer.height + pad_y)


def build_units(elements: list[Element], W: float, H: float) -> list[Unit]:
    units: dict[str, Unit] = {}
    claimed: set[int] = set()

    for el in elements:
        gid = el.props.get("group_id")
        if isinstance(gid, str) and gid:
            units.setdefault(f"group:{gid}", Unit(id=gid, members=[])).members.append(el)
            claimed.add(el.index)

    # Implicit buttons and pills: an ungrouped text inside a smallish shape.
    # Some exporters (Crello) flatten button shapes into images, so a small,
    # non-background image can host a text too.
    shapes = [e for e in elements if e.index not in claimed and e.width * e.height < 0.25 * W * H
              and (e.type == "shape" or (e.type == "image" and e.props.get("fit") != "cover"))]
    shapes.sort(key=lambda e: e.width * e.height)  # the tightest host wins
    for text in (e for e in elements if e.type == "text" and e.index not in claimed):
        if SEPARATOR_TEXT.match(str(text.props.get("content", ""))):
            continue  # a "x" between logos is not a button label
        host = next((s for s in shapes if s.index not in claimed and _contains(s, text)), None)
        if host is not None:
            units[f"pair:{host.id}"] = Unit(id=f"{host.id}+{text.id}", members=[host, text])
            claimed.update({host.index, text.index})

    for el in elements:
        if el.index not in claimed:
            units[f"el:{el.index}"] = Unit(id=el.id, members=[el])
    return list(units.values())


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _asset_is_missing(el: Element) -> bool:
    asset = el.props.get("asset_id")
    return not isinstance(asset, str) or not (ASSETS_DIR / asset).is_file()


def _named_logo(el: Element) -> bool:
    return "logo" in str(el.props.get("asset_id", "")).lower()


def _caps_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    return sum(c.isupper() for c in letters) / len(letters) if letters else 0.0


def _alpha(color: object) -> float:
    m = re.search(r"rgba\([^)]*,\s*([\d.]+)\s*\)", str(color or ""))
    return float(m.group(1)) if m else 1.0


def _kinds(u: Unit) -> set[str]:
    return {m.type for m in u.members}


def _same_row(a: Unit, b: Unit) -> bool:
    h = max(a.height, b.height, 1.0)
    return abs(a.center[1] - b.center[1]) <= 0.5 * h


# --------------------------------------------------------------------------
# Logos
# --------------------------------------------------------------------------


def _logo_candidates(units: list[Unit], W: float, H: float) -> list[Unit]:
    """Small, visible, single images: anything that could be a brand mark."""
    canvas = W * H
    return [u for u in units
            if _kinds(u) == {"image"} and len(u.members) == 1
            and u.area < 0.06 * canvas and u.members[0].opacity >= 0.6
            and u.members[0].props.get("fit") != "cover"]


def _merge_logo_sets(units: list[Unit], W: float, H: float) -> list[Unit]:
    """Join rows of similar-sized logos, with any separators between them and
    a label next to them, into one lockup or strip unit."""
    candidates = sorted(_logo_candidates(units, W, H), key=lambda u: u.center[0])
    if len(candidates) < 2:
        return units

    # Chain neighbours on the same row with similar heights and modest gaps.
    rows: list[list[Unit]] = []
    for u in candidates:
        for row in rows:
            last = row[-1]
            gap = u.box[0] - last.box[2]
            ratio = max(u.height, last.height) / max(min(u.height, last.height), 1.0)
            if _same_row(u, last) and ratio <= 1.8 and gap <= 3 * max(u.height, last.height):
                row.append(u)
                break
        else:
            rows.append([u])

    texts = [u for u in units if _kinds(u) == {"text"} and len(u.members) == 1]
    thin_shapes = [u for u in units if _kinds(u) == {"shape"} and len(u.members) == 1
                   and (u.width <= 0.2 * u.height or u.height <= 0.2 * u.width)]
    merged_ids: set[int] = set()
    new_units: list[Unit] = []

    for row in (r for r in rows if len(r) >= 2):
        x0 = row[0].box[0]
        x1 = row[-1].box[2]
        row_h = max(u.height for u in row)
        top = min(u.box[1] for u in row)
        between = [u for u in texts + thin_shapes
                   if x0 < u.center[0] < x1 and _same_row(u, row[0])
                   and (u in thin_shapes or SEPARATOR_TEXT.match(u.text))]
        label = next((u for u in texts if LOGO_LABEL.match(u.text) and (
            (_same_row(u, row[0]) and 0 <= x0 - u.box[2] <= 3 * row_h)
            or (0 <= top - u.box[3] <= 1.5 * row_h and u.box[0] < x1 and u.box[2] > x0))), None)
        kind = "lockup" if between and len(row) <= 3 and not label else "strip"
        members = [m for u in row + between + ([label] if label else []) for m in u.members]
        logos = [u.members[0] for u in row]
        why = (f"{len(row)} logos with a separator" if kind == "lockup"
               else f"{len(row)} logos in a row" + (" under a label" if label else ""))
        new_units.append(Unit(id="+".join(u.id for u in row), members=members, role="logo",
                              confidence=0.8, kind=kind, logos=logos, why=why))
        merged_ids.update(id(u) for u in row + between + ([label] if label else []))

    if not new_units:
        return units
    return [u for u in units if id(u) not in merged_ids] + new_units


def _pick_primary_logo(units: list[Unit]) -> None:
    logos = [u for u in units if u.role == "logo"]
    if not logos:
        return
    lockups = [u for u in logos if u.kind == "lockup"]
    pool = lockups or [u for u in logos if u.kind == "single"] or logos

    def rank(u: Unit) -> tuple:
        named = any(_named_logo(m) for m in (u.logos or u.members))
        return (named, u.area / max(len(u.logos), 1), -u.center[1])

    best = max(pool, key=rank)
    for u in logos:
        u.primary = u is best and u.kind != "strip"


# --------------------------------------------------------------------------
# Roles
# --------------------------------------------------------------------------


def _classify(units: list[Unit], W: float, H: float) -> None:
    canvas = W * H
    pending: list[Unit] = []

    for u in units:
        if u.role == "logo" and u.kind in ("lockup", "strip"):
            continue  # already decided by _merge_logo_sets
        kinds = _kinds(u)
        if (kinds == {"image"} or kinds == {"shape"}) and u.area >= 0.85 * canvas:
            u.role, u.confidence, u.why = "background", 0.95, "covers the canvas"
            continue
        if kinds == {"shape"}:
            u.role, u.confidence, u.why = "decoration", 0.8, "shape with no text"
            continue
        pending.append(u)

    # Images: the largest becomes the hero. A single image counts as a logo
    # only with evidence: named like one, or small, wide, near an edge, and
    # actually present and visible (rules out watermarks and placeholders).
    images = sorted((u for u in pending if _kinds(u) == {"image"}), key=lambda u: u.area, reverse=True)
    for rank, u in enumerate(images):
        el = u.members[0]
        cx, cy = u.center
        aspect = el.aspect or 1.0
        near_edge = cy < 0.25 * H or cy > 0.8 * H or cx < 0.2 * W or cx > 0.8 * W
        visible = not _asset_is_missing(el) and el.opacity >= 0.6
        if _named_logo(el) and visible:
            u.role, u.confidence, u.why = "logo", 0.85, "named like a logo"
        elif aspect >= 1.8 and u.area < 0.08 * canvas and near_edge and visible:
            u.role, u.confidence, u.why = "logo", 0.6, "small wide image near an edge"
        elif rank == 0 and u.area >= 0.04 * canvas:
            u.role, u.confidence, u.why = "hero", 0.8, "largest image"
        else:
            u.role, u.confidence, u.why = "image", 0.5, "secondary image"
            if not visible:
                u.why += " (asset missing or faint)"

    texts = [u for u in pending if u.texts]
    pills = [u for u in texts if any(m.type == "shape" for m in u.members)]
    plain = [u for u in texts if u not in pills]

    # Buttons and pills: an action phrase makes a CTA, otherwise a badge.
    for u in pills:
        words = len(u.text.split())
        if ACTION_WORDS.search(u.text):
            u.role, u.confidence, u.why = "cta", 0.9, "action phrase on a button"
        elif u.rotation or _caps_ratio(u.text) > 0.7 or words <= 3:
            u.role, u.confidence, u.why = "badge", 0.8, "short label on a pill"
        else:
            u.role, u.confidence, u.why = "cta", 0.5, "text on a button"
    if pills and not any(u.role == "cta" for u in pills):
        # No action phrase anywhere: the biggest, lowest pill is likely the button.
        best = max(pills, key=lambda u: (u.area, u.center[1]))
        if best.area > 1.5 * min(p.area for p in pills):
            best.role, best.confidence, best.why = "cta", 0.5, "largest pill, no action phrase found"

    if not plain:
        return
    sizes = sorted({u.font_size for u in plain}, reverse=True)
    smallest = sizes[-1]
    headline = max(plain, key=lambda u: (u.font_size, u.area))
    for u in plain:
        text = u.text
        el = u.texts[0]
        dim = el.opacity < 0.85 or _alpha(el.props.get("color")) < 0.85
        low = u.center[1] > 0.85 * H
        if u is headline and len(plain) > 1 or (u is headline and u.font_size >= 0.03 * min(W, H)):
            u.role, u.confidence, u.why = "headline", 0.9, "largest text"
        elif ACTION_WORDS.search(text) and len(text.split()) <= 6:
            u.role, u.confidence, u.why = "cta", 0.7, "short action phrase"
        elif LEGAL_WORDS.search(text) or (u.font_size == smallest and len(sizes) > 2 and (dim or low)):
            u.role, u.confidence, u.why = "legal", 0.8 if LEGAL_WORDS.search(text) else 0.55, "small print"
        elif u.font_size >= sizes[min(1, len(sizes) - 1)] and u is not headline:
            u.role, u.confidence, u.why = "subhead", 0.6, "second-largest text"
        elif _caps_ratio(text) > 0.8 and len(text.split()) <= 4:
            u.role, u.confidence, u.why = "badge", 0.5, "short all-caps label"
        else:
            u.role, u.confidence, u.why = "body", 0.5, "other text"
    if headline.role != "headline":
        headline.role, headline.confidence, headline.why = "headline", 0.6, "only text"


# --------------------------------------------------------------------------
# Sets, importance, alignment
# --------------------------------------------------------------------------


def _find_sets(units: list[Unit]) -> dict[str, list[Unit]]:
    """Units with the same role and a similar height on a shared row or column."""
    sets: dict[str, list[Unit]] = {}
    candidates = [u for u in units if u.role in ("badge", "image", "body", "cta") and not u.rotation]
    used: set[str] = set()
    for u in candidates:
        if u.id in used:
            continue
        peers = [v for v in candidates if v.role == u.role and v.id not in used
                 and abs(v.height - u.height) <= 0.2 * max(u.height, 1)
                 and (abs(v.center[1] - u.center[1]) <= 0.5 * u.height
                      or abs(v.center[0] - u.center[0]) <= 0.5 * u.width)]
        if len(peers) >= 2:
            sid = f"set:{u.role}:{len(sets)}"
            for v in peers:
                v.set_id = sid
                used.add(v.id)
            sets[sid] = peers
    return sets


def _alignment(units: list[Unit], W: float) -> str:
    fg = [u for u in units if u.role not in ("background", "decoration")]
    if not fg:
        return "center"
    centred = sum(abs(u.center[0] - W / 2) <= 0.06 * W for u in fg)
    return "center" if centred >= len(fg) / 2 else "left"


def _importance(u: Unit, largest: float) -> float:
    if u.role == "background":
        return 0.0
    if u.role == "logo" and u.kind == "strip":
        base = STRIP_IMPORTANCE
    elif u.role == "logo" and not u.primary:
        base = SECONDARY_LOGO_IMPORTANCE
    else:
        base = IMPORTANCE.get(u.role, 0.4)
    # Within a role, bigger things matter a little more.
    return round(base * (0.85 + 0.15 * min(u.area / largest, 1.0)) * (0.7 + 0.3 * u.confidence), 3)


def understand(elements: list[Element], W: float, H: float) -> DesignModel:
    units = build_units(elements, W, H)
    units = _merge_logo_sets(units, W, H)
    _classify(units, W, H)
    _pick_primary_logo(units)
    largest = max((u.area for u in units if u.role != "background"), default=1.0) or 1.0
    for u in units:
        u.importance = _importance(u, largest)
    model = DesignModel(width=W, height=H, units=units)
    model.sets = _find_sets(units)
    model.alignment = _alignment(units, W)
    return model
