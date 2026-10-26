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


def synthetic_model(name: str):
    design = tasks.SYNTHETIC[name]
    return understand(normalize_elements(design["elements"], []), 1080, 1080)


def logo_units(model):
    return [u for u in model.units if u.role == "logo"]


def test_cobrand_pair_is_one_primary_lockup_with_its_separator():
    model = synthetic_model("cobrand_pair")
    [lockup] = logo_units(model)
    assert lockup.kind == "lockup" and lockup.primary
    assert [l.id for l in lockup.logos] == ["brand_a", "brand_b"]
    assert "times" in {m.id for m in lockup.members}
    assert model.unit_of(next(m.index for m in lockup.members if m.id == "times")) is lockup


def test_three_way_lockup_keeps_partner_order_and_rule_separators():
    [lockup] = logo_units(synthetic_model("cobrand_three"))
    assert lockup.kind == "lockup"
    assert [l.id for l in lockup.logos] == ["brand_a", "brand_b", "brand_c"]
    assert {"rule_1", "rule_2"} <= {m.id for m in lockup.members}


def test_sponsor_strip_takes_its_label_and_is_not_primary():
    model = synthetic_model("sponsor_strip")
    strips = [u for u in logo_units(model) if u.kind == "strip"]
    assert len(strips) == 1
    strip = strips[0]
    assert [l.id for l in strip.logos] == [f"sponsor_{i}" for i in range(5)]
    assert "presented_by" in {m.id for m in strip.members}
    assert not strip.primary
    assert model.primary_logo.id == "hero_logo"
    assert model.primary_logo.importance > strip.importance


def test_partner_mark_is_a_secondary_logo():
    model = synthetic_model("primary_and_partner")
    by_id = {u.id: u for u in model.units}
    assert by_id["main_logo"].primary
    assert by_id["partner_mark"].role == "logo" and not by_id["partner_mark"].primary
    assert by_id["main_logo"].importance > by_id["partner_mark"].importance


def test_missing_asset_watermark_is_not_a_logo():
    model = model_for("level3_advanced")
    assert roles(model)["holiday_watermark"] == "image"
    assert model.primary_logo.id == "logo"


def _text(eid, content, x, y, w, h, size):
    return {"id": eid, "type": "text", "x": x, "y": y, "width": w, "height": h, "content": content,
            "font_family": "Solstice Sans", "font_size": size, "color": "rgba(255,255,255,1)"}


COMPOUND = {
    "canvas": {"width": 1080, "height": 1080},
    "elements": [
        {"id": "bg", "type": "image", "x": 0, "y": 0, "width": 1080, "height": 1080,
         "asset_id": "solstice_bg_gradient.png", "fit": "cover"},
        # "50" with a smaller "%" right after it, then "OFF"
        _text("num", "50", 300, 120, 220, 200, 200),
        _text("pct", "%", 525, 130, 90, 100, 90),
        _text("off", "OFF", 525, 235, 140, 70, 60),
        # a big "&" layered behind two words
        _text("amp", "&", 470, 430, 140, 220, 200),
        _text("left", "GET WELL", 250, 500, 220, 50, 40),
        _text("right", "FEEL BETTER", 600, 500, 260, 50, 40),
        # one sentence stacked as fragments in mixed sizes
        _text("s1", "THIS IS A", 380, 760, 320, 60, 50),
        _text("s2", "HOUSE", 380, 822, 320, 90, 80),
        _text("s3", "of the", 380, 916, 320, 40, 32),
        _text("s4", "FUTURE", 380, 958, 320, 90, 80),
    ],
}


def test_compound_texts_group_as_the_designer_composed_them():
    model = understand(normalize_elements(COMPOUND["elements"], []), 1080, 1080)
    groups = [sorted(m.id for m in u.members) for u in model.units if len(u.members) > 1]
    assert sorted(["num", "off", "pct"]) in groups
    assert sorted(["amp", "left", "right"]) in groups
    assert sorted(["s1", "s2", "s3", "s4"]) in groups


@pytest.mark.parametrize("fmt", ["leaderboard_ad", "skyscraper_ad", "instagram_story", "web_banner"])
def test_compound_texts_keep_their_arrangement_after_resize(fmt):
    from engine.resize import resize
    from tools.catalogue import FORMATS

    w, h = FORMATS[fmt]
    out = {e["id"]: e for e in resize(json.loads(json.dumps(COMPOUND)), {"width": w, "height": h})["elements"]}
    # the % stays immediately right of its number
    assert out["pct"]["x"] >= out["num"]["x"] + out["num"]["width"] * 0.9
    assert abs(out["pct"]["y"] - out["num"]["y"]) < out["num"]["height"]
    # the sentence keeps its reading order, top to bottom
    ys = [out[k]["y"] for k in ("s1", "s2", "s3", "s4")]
    assert ys == sorted(ys)
    # the & stays between its two words
    assert out["left"]["x"] < out["amp"]["x"] < out["right"]["x"]
