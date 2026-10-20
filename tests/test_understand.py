import json
from pathlib import Path

import pytest

from engine.normalize import normalize_canvas, normalize_elements
from engine.understand import understand
from tools import tasks

ROOT = Path(__file__).resolve().parent.parent


def model_for(name: str):
    source = json.loads((ROOT / "input" / f"chameleon_sample_{name}.json").read_text())["source"]
    W, H = normalize_canvas(source["canvas"], [])
    return understand(normalize_elements(source["elements"], []), W, H)


def roles(model) -> dict[str, str]:
    return {u.id: u.role for u in model.units}


def test_level1_roles():
    assert roles(model_for("level1_warmup")) == {
        "bg": "background", "logo": "logo", "headline": "headline", "cta": "cta",
    }


def test_level2_roles_and_groups():
    r = roles(model_for("level2_core"))
    assert r["bg"] == "background"
    assert r["product"] == "hero"
    assert r["headline"] == "headline"
    assert r["subhead"] == "subhead"
    assert r["cta"] == "cta"          # the cta_bg + cta_label group
    assert r["badge"] == "badge"      # the badge_bg + badge_label group
    assert r["deco_circle_1"] == r["deco_circle_2"] == "decoration"


def test_level3_roles_sets_and_single_logo():
    model = model_for("level3_advanced")
    r = roles(model)
    assert r["logo"] == "logo"
    assert r["holiday_watermark"] == "image"
    assert r["legal_text"] == "legal"
    assert r["ribbon"] == "badge"
    badge_sets = [s for s in model.sets.values() if {u.id for u in s} == {"badge1", "badge2", "badge3"}]
    assert len(badge_sets) == 1
    assert len(model.by_role("logo")) == 1


def test_importance_ranks_headline_above_small_print():
    model = model_for("level3_advanced")
    imp = {u.id: u.importance for u in model.units}
    assert imp["headline"] > imp["cta"] > imp["legal_text"] > imp["bg"]


@pytest.mark.parametrize("task", list(tasks.generate()), ids=lambda t: t["task_id"])
def test_every_element_lands_in_exactly_one_unit(task):
    source = task["source"]
    W, H = normalize_canvas(source["canvas"], [])
    elements = normalize_elements(source["elements"], [])
    model = understand(elements, W, H)
    seen = sorted(m.index for u in model.units for m in u.members)
    assert seen == list(range(len(elements)))
    assert all(u.role for u in model.units)
