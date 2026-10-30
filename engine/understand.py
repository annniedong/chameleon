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

Everything here is a heuristic. Roles affect placement as well as soft preferences; uncertainty is explored
through bounded alternative interpretations before validation.
"""

from __future__ import annotations

import re
import copy
import math
import os
import sys
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

    role_scores: dict[str, float] = field(default_factory=dict)
    expected_importance: float = 0.5

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

    log_plausibility: float = 0.0

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

    for cluster in _compound_texts([e for e in elements if e.type == "text" and e.index not in claimed]):
        units[f"text:{cluster[0].index}"] = Unit(id="+".join(e.id for e in cluster), members=cluster)
        claimed.update(e.index for e in cluster)

    for el in elements:
        if el.index not in claimed:
            units[f"el:{el.index}"] = Unit(id=el.id, members=[el])
    return list(units.values())


def _words(el: Element) -> int:
    return len(str(el.props.get("content", "")).split())


NUMBER = re.compile(r"^[\s$€£¥₹+\-−~]*\d[\d.,:/\s]*[kKmM]?\s*$")
UNIT = re.compile(r"^\s*(%|‰|°|\$|€|£|¥|₹|x|×|\+|pts?|off|%\s*off|kg|g|ml|l|km|mi|hrs?|min|days?|for\s+\d+|"
                  r"st|nd|rd|th)\s*$", re.IGNORECASE)
CONNECTOR_GLYPH = re.compile(r"^\s*[&+×x/·|—–-]\s*$")
TRAILING_CONNECTOR = re.compile(
    r"\b(a|an|the|of|and|or|to|for|in|on|at|with|by|from|is|are|your|our|my|this|that|up|until|than|&|\+)\s*$",
    re.IGNORECASE)
LEADING_CONNECTOR = re.compile(r"^\s*(of|and|or|to|for|in|on|at|with|by|from|the|a|an|&|\+|until|than)\b",
                               re.IGNORECASE)
TERMINAL = re.compile(r"[.!?:;…]\s*$")


def _content(el: Element) -> str:
    return str(el.props.get("content", ""))


def _text_link(first: Element, second: Element) -> bool:
    """Do the WORDS say `first` continues into `second` (in reading order)?

    Cheap language cues, no model: a number followed by its unit ("50" "%"),
    a currency sign before a number, a lone "&" or "+" between words, or an
    unfinished phrase ("THIS IS A" -> "HOUSE", "HOUSE" -> "of the").
    """
    a, b = _content(first).strip(), _content(second).strip()
    if not a or not b:
        return False
    if NUMBER.match(a) and UNIT.match(b):
        return True
    if UNIT.match(a) and NUMBER.match(b) and re.match(r"^[$€£¥₹]", a.strip()):
        return True
    # A lone "&" or "+" joins words ("GET WELL" & "FEEL BETTER"), not numbers.
    if CONNECTOR_GLYPH.match(a) and re.search(r"[^\W\d_]", b):
        return True
    if CONNECTOR_GLYPH.match(b) and re.search(r"[^\W\d_]", a):
        return True
    if TRAILING_CONNECTOR.search(a) or LEADING_CONNECTOR.match(b):
        return True
    # No sentence end, and the next piece starts lowercase: still mid-sentence.
    return not TERMINAL.search(a) and b[:1].islower()


def _joined(a: Element, b: Element) -> bool:
    """Do two texts form one phrase as the designer composed it?

    Designers often build one phrase from several text elements: "50" plus a
    smaller "%", a large "&" layered behind two words, or "THIS IS A" /
    "HOUSE" / "of the" / "FUTURE" stacked in mixed sizes. Moving those
    pieces independently splits numbers from units and scrambles sentences.

    Geometry is the main evidence (how close the pieces sit); the words
    (`_text_link`) widen the allowed gap when they say the phrase continues.
    """
    ax0, ay0, ax1, ay1 = a.x, a.y, a.x + a.width, a.y + a.height
    bx0, by0, bx1, by1 = b.x, b.y, b.x + b.width, b.y + b.height
    h_overlap = min(ax1, bx1) - max(ax0, bx0)
    v_overlap = min(ay1, by1) - max(ay0, by0)
    small = min(a.font_size or 1.0, b.font_size or 1.0)
    # Layered: the boxes overlap noticeably (a glyph behind words).
    if h_overlap > 0 and v_overlap > 0 and h_overlap * v_overlap >= 0.1 * min(a.width * a.height, b.width * b.height):
        return True
    same_line = v_overlap >= 0.5 * min(a.height, b.height)
    stacked = h_overlap >= 0.3 * min(a.width, b.width)
    if same_line:
        left, right = (a, b) if a.x <= b.x else (b, a)
        linked = _text_link(left, right)
        # Touching: a number and its unit, a word and its accent. Further
        # apart only when the words say the phrase continues.
        return -h_overlap <= (2.0 if linked else 0.8) * small
    if stacked:
        top, bottom = (a, b) if a.y <= b.y else (b, a)
        # Words only widen the gap for pieces that really share a column.
        linked = h_overlap >= 0.5 * min(a.width, b.width) and _text_link(top, bottom)
        large = max(a.font_size or 1.0, b.font_size or 1.0)
        # Mixed sizes ("HOUSE" over "of the") are spaced to the larger type.
        gap_ok = -v_overlap <= (max(0.9 * small, 0.5 * large) if linked else 0.35 * small)
        short = max(_words(a), _words(b)) <= (6 if linked else 3)
        return gap_ok and short
    return False


def _compound_texts(texts: list[Element]) -> list[list[Element]]:
    """Clusters of two or more texts that read as one phrase (union-find)."""
    parent = list(range(len(texts)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            if not texts[i].rotation and not texts[j].rotation and _joined(texts[i], texts[j]):
                left = [k for k in range(len(texts)) if find(k) == find(i)]
                right = [k for k in range(len(texts)) if find(k) == find(j)]
                first, second = sorted((texts[i], texts[j]), key=lambda e: (e.y, e.x))
                # A true phrase may span several lines. Geometry alone needs
                # complete linkage so A-near-B-near-C cannot merge arbitrary text.
                if (_text_link(first, second) or
                        all(_joined(texts[a], texts[b]) for a in left for b in right)):
                    parent[find(i)] = find(j)
    groups: dict[int, list[Element]] = {}
    for i, el in enumerate(texts):
        groups.setdefault(find(i), []).append(el)
    return [sorted(g, key=lambda e: e.index) for g in groups.values() if len(g) >= 2]


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


def _softmax(logits: dict[str, float]) -> dict[str, float]:
    peak = max(logits.values())
    weights = {r: math.exp(v - peak) for r, v in logits.items()}
    total = sum(weights.values())
    return {r: v / total for r, v in weights.items()}


def _carries_content(u: Unit, units: list[Unit]) -> bool:
    """True when another unit is drawn above `u` and sits inside it."""
    def depth(el: Element) -> tuple[int, int]:
        return (el.z_index, el.index)
    top = max(depth(m) for m in u.members)
    return any(depth(m) > top and any(_contains(b, m) for b in u.members)
               for o in units if o is not u for m in o.members)


PROMO = re.compile(r"[$\u20ac\u00a3\u00a5\u20b9]|\d\s*%|\b(off|sale|free|only|save)\b", re.I)


def _classify(units: list[Unit], W: float, H: float) -> None:
    """Add independent feature evidence in log space, then normalize.

    Scores are heuristic probabilities, not calibrated estimates. Explicit
    groups determine membership; roles never split or discard their members.
    """
    canvas = max(W * H, 1)
    plain = [u for u in units if _kinds(u) == {"text"}]
    headline = max(plain, key=lambda u: (u.font_size, u.area), default=None)
    sizes = sorted({u.font_size for u in plain}, reverse=True)
    images = sorted((u for u in units if _kinds(u) == {"image"} and u.area < .85 * canvas
                     and u.kind == "single"), key=lambda u: -u.area)
    for u in units:
        kinds = _kinds(u)
        area = u.area / canvas
        names = " ".join(str(m.props.get("asset_id", "")) for m in u.members).lower()
        opacity = min(m.opacity for m in u.members)
        cx, cy = u.center
        edge = cy < .25 * H or cy > .8 * H or cx < .2 * W or cx > .8 * W
        full = (u.box[0] <= .02 * W and u.box[1] <= .02 * H
                and u.box[2] >= .98 * W and u.box[3] >= .98 * H)
        if u.kind in ("lockup", "strip"):
            logits = {"logo": 5.0, "image": 0.0}
        elif not u.texts:
            shape = kinds == {"shape"}
            logits = {"decoration": 3.5 if shape else -0.5, "image": 0.0 if shape else 1.2}
            if kinds == {"image"}:
                largest = bool(images and u is images[0])
                logits["hero"] = (1.4 if largest else -.4) + min(area * 5, 1.4)
                logits["logo"] = -2.0
                if opacity >= .6 and not _asset_is_missing(u.members[0]):
                    logits["logo"] += 1.1 * (area < .08) + .8 * edge + .9 * (u.width >= 1.8 * u.height)
                    if "logo" in names:
                        logits["logo"] += 5
                logits["decoration"] += 1.4 * (opacity < .6)
                if any(word in names for word in ("deco", "ornament", "texture", "pattern")):
                    logits["decoration"] += 3.0
                if opacity < .6:
                    logits["hero"] -= 2
            # Large size alone cannot exclude an element from the layout, but
            # a large panel with other content drawn on top of it is a backdrop.
            if area >= .85:
                logits["background"] = (6.0 if _carries_content(u, units) else 1.0) + 2.5 * full
                logits["background"] += 2 * any(m.props.get("fit") == "cover" for m in u.members)
                logits["background"] += 1.5 * any(w in names for w in ("bg", "background"))
                if shape and full:
                    logits["background"] += 4
        else:
            words = len(u.text.split())
            action = bool(ACTION_WORDS.search(u.text)) and words <= 6
            promo = bool(PROMO.search(u.text))
            legal = bool(LEGAL_WORDS.search(u.text))
            pill = bool(kinds & {"shape", "image"})
            relative = u.font_size / max(sizes[0] if sizes else u.font_size, 1)
            logits = {"headline": -1.0, "subhead": -.5, "body": .8,
                      "cta": -1.2, "badge": -.8, "legal": -2.0}
            logits["headline"] += 3.8 * (u is headline) + .8 * relative - 1.6 * promo - 3 * legal
            logits["subhead"] += 2.8 * (u is not headline and bool(sizes) and
                                       u.font_size >= sizes[min(1, len(sizes)-1)])
            logits["body"] += 1.0 * (words > 6)
            logits["cta"] += 4.8 * action + 1.0 * pill
            logits["badge"] += 1.2 * (words <= 4) + .6 * (_caps_ratio(u.text) > .8) + 2.0 * promo
            logits["legal"] += 6.0 * legal
            if sizes and u.font_size == sizes[-1] and len(sizes) > 2:
                logits["legal"] += 2.8 * (cy > .85 * H or opacity < .85)
            if pill:
                logits["headline"] -= 3
                logits["subhead"] -= 3
                logits["badge"] += 2.5
                if action and not promo:
                    logits["cta"] += 3.5
            u.why = "text size, wording, position and grouping"
        scores = _softmax(logits)
        # Special placement needs strong evidence. Keep weak hypotheses as
        # foreground choices; the search may still explore a credible alternative.
        for special, threshold in (("background", .85),):
            if special in scores and scores[special] < threshold:
                del logits[special]
        scores = _softmax(logits)
        if max(scores, key=scores.get) == "decoration" and scores["decoration"] < .55:
            # A conservative prior breaks weak decoration/foreground ties.
            # Keep the competing hypothesis, but do not give it special
            # placement in the most likely model on weak evidence alone.
            logits["decoration"] = max(v for r, v in logits.items() if r != "decoration") - 1e-6
        u.role_scores = _softmax(logits)
        u.role = max(u.role_scores, key=u.role_scores.get)
        u.confidence = u.role_scores[u.role]
        u.why = u.why or "area, aspect, visibility, position and asset name"
        if os.environ.get("CHAMELEON_DEBUG"):
            print("[understand] " + u.id + ": " + " ".join(
                f"{r}={v:.2f}" for r, v in sorted(u.role_scores.items(), key=lambda x: -x[1])),
                file=sys.stderr)


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


def _refresh(model: DesignModel) -> DesignModel:
    for u in model.units:
        u.primary = False
        u.set_id = None
        u.confidence = u.role_scores.get(u.role, u.confidence)
    _pick_primary_logo(model.units)
    largest = max((u.area for u in model.units if u.role != "background"), default=1) or 1
    for u in model.units:
        u.expected_importance = sum(p * IMPORTANCE[r] for r, p in u.role_scores.items())
        assigned = _importance(u, largest)
        # Retain the role chosen by this interpretation while damping brittle
        # changes in priority. Brand conventions retain their existing priority.
        u.importance = (assigned if u.role in ("logo", "background")
                        else .75 * assigned + .25 * u.expected_importance)
    model.sets = _find_sets(model.units)
    model.alignment = _alignment(model.units, model.width)
    model.log_plausibility = sum(math.log(max(u.role_scores.get(u.role, 1), 1e-12))
                                 for u in model.units)
    return model


def understand(elements: list[Element], W: float, H: float) -> DesignModel:
    units = _merge_logo_sets(build_units(elements, W, H), W, H)
    _classify(units, W, H)
    return _refresh(DesignModel(width=W, height=H, units=units))


def interpretations(model: DesignModel, limit: int = 4, ambiguity_margin: float = .30) -> list[DesignModel]:
    """Bounded beam search over uncertain roles, with stable lexical ties.

    Preserve the original model as candidate zero. Only uncertain units branch;
    explicit membership is shared unchanged, and every model owns its metadata.
    At most one primary hero/headline is allowed in alternative assignments.
    """
    limit = max(1, min(int(limit), 8))
    baseline = tuple(u.role for u in model.units)
    beam = [(0.0, ())]
    for u in model.units:
        ranked = sorted(u.role_scores.items(), key=lambda item: (-item[1], item[0]))
        options = [(u.role, u.role_scores.get(u.role, 1.0))]
        if len(ranked) > 1 and ranked[0][1] - ranked[1][1] <= ambiguity_margin:
            options = [(r, p) for r, p in ranked[:3] if p >= .10 and (r != "decoration" or p >= .25)]
            if u.role not in dict(options):
                options.append((u.role, u.role_scores[u.role]))
        expanded = []
        for logp, roles in beam:
            for role, prob in options:
                if role in ("hero", "headline") and role in roles:
                    continue
                expanded.append((logp + math.log(max(prob, 1e-12)), roles + (role,)))
        beam = sorted(expanded, key=lambda x: (-x[0], x[1]))[:max(16, limit * 4)]
    result = [model]
    for _, assignment in beam:
        if assignment == baseline or len(result) >= limit:
            continue
        alt = copy.deepcopy(model)
        for u, role in zip(alt.units, assignment):
            u.role = role
        result.append(_refresh(alt))
    if os.environ.get("CHAMELEON_DEBUG"):
        print(f"[interpret] generated {len(result)} interpretations", file=sys.stderr)
        for i, alt in enumerate(result):
            print(f"[interpret {i}] " + " ".join(f"{u.id}={u.role}" for u in alt.units), file=sys.stderr)
    return result
