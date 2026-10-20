"""Build evaluation tasks: every sample design x every catalogue format,
plus mutated designs for the input edge cases in the plan.

Mutations are deterministic (seeded) so a regression shows up as a change
in the report, not as noise.
"""

from __future__ import annotations

import copy
import json
import random
from pathlib import Path
from typing import Any, Callable, Iterator

from tools.catalogue import FORMATS

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = ROOT / "input"

Design = dict[str, Any]


def load_samples(samples_dir: Path = SAMPLES_DIR) -> dict[str, Design]:
    return {p.stem: json.loads(p.read_text())["source"] for p in sorted(samples_dir.glob("*.json"))}


def _texts(d: Design) -> list[dict[str, Any]]:
    return [e for e in d["elements"] if e["type"] == "text"]


def _largest_text(d: Design) -> dict[str, Any] | None:
    texts = _texts(d)
    return max(texts, key=lambda e: e.get("font_size", 0)) if texts else None


def _foreground(d: Design, kind: str | None = None) -> list[dict[str, Any]]:
    return [e for e in d["elements"] if e.get("fit") != "cover" and (kind is None or e["type"] == kind)]


# Each mutation edits a deep copy in place. Returning False means it does
# not apply to this design and the task is skipped.


def m_long_text(d: Design, rng: random.Random) -> bool:
    el = _largest_text(d)
    if not el:
        return False
    el["content"] = el["content"] + " - " + el["content"] + ", now with even more words to fit"
    return True


def m_extra_badges(d: Design, rng: random.Random) -> bool:
    groups: dict[str, list[dict[str, Any]]] = {}
    for e in d["elements"]:
        if e.get("group_id"):
            groups.setdefault(e["group_id"], []).append(e)
    pairs = [g for g in groups.values() if len(g) == 2]
    if not pairs:
        return False
    base = rng.choice(pairs)
    for n in range(2):
        gid = f"{base[0]['group_id']}_copy{n}"
        for e in base:
            c = copy.deepcopy(e)
            c["id"] = f"{e['id']}_copy{n}"
            c["group_id"] = gid
            c["y"] = e["y"] + (n + 1) * (e["height"] + 12)
            d["elements"].append(c)
    return True


def m_rotation(d: Design, rng: random.Random) -> bool:
    fg = _foreground(d)
    if not fg:
        return False
    rng.choice(fg)["rotation"] = rng.choice([-20, -8, 15, 90])
    return True


def m_missing_asset(d: Design, rng: random.Random) -> bool:
    imgs = _foreground(d, "image")
    if not imgs:
        return False
    rng.choice(imgs)["asset_id"] = "does_not_exist.png"
    return True


def m_bleed(d: Design, rng: random.Random) -> bool:
    fg = _foreground(d, "shape") or _foreground(d)
    if not fg:
        return False
    el = rng.choice(fg)
    el["x"] = d["canvas"]["width"] - el["width"] / 2
    return True


def m_zero_size(d: Design, rng: random.Random) -> bool:
    d["elements"].append(
        {"id": "hairline", "type": "shape", "shape_type": "line", "x": 40, "y": 40,
         "width": 200, "height": 0, "fill_color": "rgba(255,255,255,1)", "z_index": 1}
    )
    return True


def m_bad_style_runs(d: Design, rng: random.Random) -> bool:
    el = _largest_text(d)
    if not el:
        return False
    el["style_runs"] = [{"start": 3, "end": 999, "color": "rgba(240,178,66,1)"}, {"start": 5, "end": 2}]
    return True


def m_unicode(d: Design, rng: random.Random) -> bool:
    el = _largest_text(d)
    if not el:
        return False
    el["content"] = "☀️ " + el["content"] + " — ¡Olé! 日本語"
    return True


def m_empty_text(d: Design, rng: random.Random) -> bool:
    texts = _texts(d)
    if not texts:
        return False
    rng.choice(texts)["content"] = ""
    return True


def m_no_optional_fields(d: Design, rng: random.Random) -> bool:
    for e in d["elements"]:
        for f in ("z_index", "opacity", "rotation", "text_align", "fit", "line_height"):
            e.pop(f, None)
    return True


def m_duplicate_element(d: Design, rng: random.Random) -> bool:
    el = rng.choice(_foreground(d) or d["elements"])
    d["elements"].append(copy.deepcopy(el))  # same id on purpose
    return True


def m_extra_fields(d: Design, rng: random.Random) -> bool:
    for e in d["elements"]:
        e["_meta"] = {"layer": e["id"], "locked": False}
    return True


def m_many_elements(d: Design, rng: random.Random) -> bool:
    W, H = d["canvas"]["width"], d["canvas"]["height"]
    for i in range(12):
        d["elements"].append(
            {"id": f"dot_{i}", "type": "shape", "shape_type": "ellipse", "x": rng.uniform(0, W - 40),
             "y": rng.uniform(0, H - 40), "width": 40, "height": 40, "opacity": 0.2,
             "fill_color": "rgba(255,255,255,1)", "z_index": 1}
        )
    return True


MUTATIONS: dict[str, Callable[[Design, random.Random], bool]] = {
    "long_text": m_long_text,
    "extra_badges": m_extra_badges,
    "rotation": m_rotation,
    "missing_asset": m_missing_asset,
    "bleed": m_bleed,
    "zero_size": m_zero_size,
    "bad_style_runs": m_bad_style_runs,
    "unicode": m_unicode,
    "empty_text": m_empty_text,
    "no_optional_fields": m_no_optional_fields,
    "duplicate_element": m_duplicate_element,
    "extra_fields": m_extra_fields,
    "many_elements": m_many_elements,
}

LOGO = "solstice_logo_lockup.png"


def _base(extra: list[dict[str, Any]]) -> Design:
    """A plain 1080x1080 design (background, headline, CTA) plus `extra`."""
    return {
        "canvas": {"width": 1080, "height": 1080},
        "elements": [
            {"id": "bg", "type": "image", "x": 0, "y": 0, "width": 1080, "height": 1080, "z_index": 0,
             "asset_id": "solstice_bg_gradient.png", "fit": "cover"},
            {"id": "headline", "type": "text", "x": 140, "y": 420, "width": 800, "height": 160, "z_index": 2,
             "content": "Summer Together", "font_family": "Solstice Serif", "font_size": 64,
             "font_weight": "bold", "color": "rgba(255,255,255,1)", "text_align": "center"},
            {"id": "cta_bg", "type": "shape", "shape_type": "rectangle", "x": 330, "y": 640, "width": 420,
             "height": 90, "corner_radius": 45, "fill_color": "rgba(232,106,58,1)", "group_id": "cta",
             "z_index": 3},
            {"id": "cta_label", "type": "text", "x": 330, "y": 665, "width": 420, "height": 44, "z_index": 4,
             "content": "Shop the Collab", "font_family": "Solstice Sans", "font_size": 22,
             "font_weight": "bold", "color": "rgba(255,255,255,1)", "text_align": "center", "group_id": "cta"},
            *extra,
        ],
    }


def _logo(eid: str, x: float, y: float, w: float, h: float, **kw: Any) -> dict[str, Any]:
    return {"id": eid, "type": "image", "x": x, "y": y, "width": w, "height": h, "z_index": 5,
            "asset_id": LOGO, "fit": "contain", **kw}


def _label(eid: str, text: str, x: float, y: float, w: float, h: float, size: float) -> dict[str, Any]:
    return {"id": eid, "type": "text", "x": x, "y": y, "width": w, "height": h, "z_index": 5,
            "content": text, "font_family": "Solstice Sans", "font_size": size,
            "color": "rgba(255,255,255,1)", "text_align": "center"}


# Hand-built designs for cases the samples do not cover (co-branding).
SYNTHETIC: dict[str, Design] = {
    "cobrand_pair": _base([
        _logo("brand_a", 180, 80, 300, 100),
        _label("times", "×", 510, 95, 60, 70, 48),
        _logo("brand_b", 600, 80, 300, 100),
    ]),
    "cobrand_three": _base([
        _logo("brand_a", 90, 90, 240, 80),
        {"id": "rule_1", "type": "shape", "shape_type": "rectangle", "x": 358, "y": 95, "width": 4,
         "height": 70, "fill_color": "rgba(255,255,255,0.8)", "z_index": 5},
        _logo("brand_b", 420, 90, 240, 80),
        {"id": "rule_2", "type": "shape", "shape_type": "rectangle", "x": 718, "y": 95, "width": 4,
         "height": 70, "fill_color": "rgba(255,255,255,0.8)", "z_index": 5},
        _logo("brand_c", 750, 90, 240, 80),
    ]),
    "sponsor_strip": _base([
        _logo("hero_logo", 340, 60, 400, 133),
        _label("presented_by", "Presented by", 340, 860, 400, 40, 22),
        *[_logo(f"sponsor_{i}", 70 + i * 190, 920, 150, 50) for i in range(5)],
    ]),
    "primary_and_partner": _base([
        _logo("main_logo", 360, 60, 360, 120),
        _logo("partner_mark", 900, 990, 120, 40),
    ]),
}


# Mutated designs are crossed with a few formats that stress them, not the
# whole catalogue, to keep the run quick.
MUTATION_FORMATS = ("instagram_story", "web_banner", "leaderboard_ad", "skyscraper_ad", "medium_rectangle_ad")


def generate(seed: int = 7, include_mutations: bool = True) -> Iterator[dict[str, Any]]:
    samples = load_samples()
    for name, design in samples.items():
        short = name.replace("chameleon_sample_", "")
        for fmt, (w, h) in FORMATS.items():
            yield {"task_id": f"{short}__{fmt}", "mutation": None,
                   "source": copy.deepcopy(design), "target_canvas": {"width": w, "height": h}}
        same = design["canvas"]
        yield {"task_id": f"{short}__same_size", "mutation": None,
               "source": copy.deepcopy(design), "target_canvas": dict(same)}
    for name, design in SYNTHETIC.items():
        for fmt, (w, h) in FORMATS.items():
            yield {"task_id": f"{name}__{fmt}", "mutation": None,
                   "source": copy.deepcopy(design), "target_canvas": {"width": w, "height": h}}
    if not include_mutations:
        return
    for name, design in samples.items():
        short = name.replace("chameleon_sample_", "")
        for mname, mutate in MUTATIONS.items():
            rng = random.Random(f"{seed}:{name}:{mname}")
            mutated = copy.deepcopy(design)
            if not mutate(mutated, rng):
                continue
            for fmt in MUTATION_FORMATS:
                w, h = FORMATS[fmt]
                yield {"task_id": f"{short}__{mname}__{fmt}", "mutation": mname,
                       "source": copy.deepcopy(mutated), "target_canvas": {"width": w, "height": h}}
