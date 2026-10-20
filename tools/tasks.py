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
