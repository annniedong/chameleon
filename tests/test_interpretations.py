import copy
import math

import pytest

from engine import check, reflow
from engine.normalize import normalize_elements
from engine.resize import resize
from engine.score import comparative_score, scoring_reference
from engine.search_config import SearchConfig
from engine.understand import understand, interpretations
from tests.test_understand import _text, model_for, synthetic_model
from tools import tasks


def image(eid, x, y, w, h, asset="solstice_bottle.png", **props):
    return dict(id=eid, type="image", x=x, y=y, width=w, height=h,
                asset_id=asset, fit="contain", **props)


def model(elements):
    return understand(normalize_elements(elements, []), 1000, 1000)


def ambiguous():
    return model([
        image("large", 100, 200, 500, 500, "decorative_pattern.png"),
        image("product", 700, 300, 180, 300),
        _text("heading", "A summer together", 100, 20, 800, 80, 60),
    ])


def test_large_decoration_and_smaller_hero_are_real_alternatives():
    base = ambiguous()
    large = next(u for u in base.units if u.id == "large")
    assert large.role_scores["hero"] > .1
    assert large.role_scores["decoration"] > .1
    variants = interpretations(base, 8, .5)
    assignments = [{u.id: u.role for u in m.units} for m in variants]
    assert any(a["large"] == "hero" for a in assignments)
    assert any(a["large"] == "decoration" for a in assignments)
    assert len({tuple(a.items()) for a in assignments}) == len(assignments)


def test_small_wide_image_has_logo_and_illustration_hypotheses():
    base = model([image("mark", 20, 20, 160, 60)])
    scores = base.units[0].role_scores
    assert scores["logo"] > .1 and scores["image"] > .1
    assert len(interpretations(base)) > 1


@pytest.mark.parametrize("text", ["50% OFF", "$99", "SAVE 50%"])
def test_largest_promotional_text_can_be_badge(text):
    base = model([_text("price", text, 100, 100, 500, 180, 120),
                  _text("title", "Summer together", 100, 500, 500, 80, 50)])
    price = next(u for u in base.units if u.id == "price")
    assert price.role_scores["badge"] > price.role_scores["headline"]
    assert price.role_scores["headline"] > 0


def test_cta_badge_ambiguity_and_strong_button_evidence():
    base = model([dict(id="pill", type="shape", x=100, y=100, width=400, height=100, group_id="button"),
                  dict(_text("label", "Save 50%", 110, 110, 380, 80, 40), group_id="button")])
    scores = base.units[0].role_scores
    assert scores["cta"] > .1 and scores["badge"] > .1
    clear = model_for("level1_warmup")
    assert len(interpretations(clear)) == 1


@pytest.mark.parametrize("base", [ambiguous(), model_for("level3_advanced"),
                                 synthetic_model("sponsor_strip"), synthetic_model("cobrand_pair")])
def test_probabilities_membership_metadata_and_determinism(base):
    assert all(u.role == max(u.role_scores, key=u.role_scores.get) for u in base.units)
    before = copy.deepcopy(base)
    variants = interpretations(base, 5, .5)
    again = interpretations(base, 5, .5)
    assert base == before
    assert [[u.role for u in m.units] for m in variants] == [[u.role for u in m.units] for m in again]
    members = [[m.index for m in u.members] for u in base.units]
    for alt in variants:
        assert [[m.index for m in u.members] for u in alt.units] == members
        assert len([u for u in alt.units if u.primary]) <= 1
        assert len(alt.by_role("hero")) <= 1
        for u in alt.units:
            assert all(math.isfinite(v) and v >= 0 for v in u.role_scores.values())
            assert sum(u.role_scores.values()) == pytest.approx(1)
            assert u.expected_importance >= 0
            if u.set_id:
                assert any(peer is u for peer in alt.sets[u.set_id])


@pytest.mark.parametrize("target", [(728, 90), (160, 600), (1080, 1920)])
def test_all_interpretations_generate_valid_candidates(target):
    base = ambiguous()
    els = sorted([m for u in base.units for m in u.members], key=lambda m: m.index)
    for alt in interpretations(base, 5, .5):
        valid = list(reflow.candidates(els, 1000, 1000, *target, alt))
        assert valid
        for _, out, _ in valid:
            assert check.problems(out, els, *target) == []
            assert [e.raw for e in out] == [e.raw for e in els]


def test_output_preserves_content_and_is_deterministic():
    source = tasks.SYNTHETIC["sponsor_strip"]
    first = resize(source, {"width": 728, "height": 90})
    assert first == resize(source, {"width": 728, "height": 90})
    assert len(first["elements"]) == len(source["elements"])
    for a, b in zip(source["elements"], first["elements"]):
        for key in ("id", "content", "font_family", "asset_id", "opacity", "z_index", "group_id"):
            assert a.get(key) == b.get(key)


def test_global_budgets_and_fallback(monkeypatch):
    monkeypatch.setenv("CHAMELEON_CANDIDATES", "5")
    monkeypatch.setenv("CHAMELEON_REFINE_TOP", "1")
    resize(tasks.SYNTHETIC["sponsor_strip"], {"width": 728, "height": 90})
    assert reflow.last_stats["layouts_generated"] <= 5
    assert reflow.last_stats["refinements"] <= 1
    monkeypatch.setattr(reflow, "candidates", lambda *a, **kw: iter(()))
    result = resize(tasks.SYNTHETIC["sponsor_strip"], {"width": 728, "height": 90})
    assert len(result["elements"]) == len(tasks.SYNTHETIC["sponsor_strip"]["elements"])
    assert reflow.last_choice is None


def test_invalid_settings_are_bounded(monkeypatch):
    monkeypatch.setenv("CHAMELEON_INTERPRETATIONS", "9999")
    monkeypatch.setenv("CHAMELEON_CANDIDATES", "nan")
    monkeypatch.setenv("CHAMELEON_PRIOR_WEIGHT", "invalid")
    config = SearchConfig.from_env()
    assert config.interpretations == 8
    assert config.candidates == 40
    assert config.prior_weight == .08


def test_geometry_alone_does_not_merge_a_transitive_text_chain():
    base = model([_text(str(i), "LABEL", 100, 100 + i * 45, 200, 40, 30) for i in range(5)])
    assert max(len(u.members) for u in base.units) <= 2


def test_weak_background_evidence_cannot_remove_foreground():
    base = model([image("large", 100, 100, 950, 950)])
    assert base.units[0].role != "background"


def test_large_panel_with_content_on_top_is_background():
    base = model([image("panel", 30, 30, 940, 940),
                  image("photo", 300, 300, 400, 400),
                  _text("heading", "Fleur de sel", 100, 60, 800, 80, 60)])
    assert next(u for u in base.units if u.id == "panel").role == "background"


def test_contain_background_keeps_aspect():
    base = model([image("bg", 0, 0, 1000, 1000, "background.png")])
    els = copy.deepcopy(base.units[0].members)
    reflow._place_background(base, els, 728, 90)
    assert els[0].aspect == 1
    assert els[0].width <= 728 and els[0].height <= 90


def test_comparative_visual_terms_do_not_depend_on_assigned_roles():
    base = ambiguous()
    reference = scoring_reference(base)
    variants = interpretations(base, 5, .5)
    els = sorted([m for u in base.units for m in u.members], key=lambda e: e.index)
    results = [comparative_score(reference, m, els, 1000, 1000, 1000, 1000, SearchConfig())
               for m in variants]
    for term in ("size", "overlap", "legibility", "hierarchy", "balance", "drift", "order", "relationships"):
        assert len({r[term] for r in results}) == 1


def test_new_text_collision_is_penalized_even_when_unit_boxes_overlap():
    from engine.score import score
    base = model([_text("a", "First", 100, 100, 200, 50, 30),
                  _text("b", "Second", 100, 300, 200, 50, 30)])
    source = sorted([m for u in base.units for m in u.members], key=lambda e: e.index)
    colliding = copy.deepcopy(source)
    colliding[1].y = 110
    assert score(base, source, 1000, 1000, 1000, 1000)["text_overlap"] == 0
    assert score(base, colliding, 1000, 1000, 1000, 1000)["text_overlap"] == 1


def test_interpretation_importance_depends_on_assignment():
    base = ambiguous()
    variants = interpretations(base, 8, .5)
    priorities = {u.role: u.importance for m in variants for u in m.units if u.id == "large"}
    assert priorities["hero"] > priorities["decoration"]
    assert any({u.id: u.role for u in m.units}.get("product") == "hero" and
               {u.id: u.role for u in m.units}.get("large") == "decoration" for m in variants)


@pytest.mark.parametrize("target", [(728, 90), (160, 600), (1200, 628)])
def test_ambiguous_resize_is_repeatable_offline(target, monkeypatch):
    import socket
    def no_network(*args, **kwargs):
        raise AssertionError("resize must not access the network")
    monkeypatch.setattr(socket, "socket", no_network)
    base = ambiguous()
    els = sorted([m for u in base.units for m in u.members], key=lambda e: e.index)
    source = {"canvas": {"width": 1000, "height": 1000}, "elements": [e.raw for e in els]}
    canvas = dict(zip(("width", "height"), target))
    before = copy.deepcopy(source)
    assert resize(source, canvas) == resize(source, canvas)
    assert source == before


@pytest.mark.parametrize("text", ["\u20ac99", "\u00a399", "\u00a599", "\u20b999"])
def test_currency_symbols_are_promotional_evidence(text):
    from engine.understand import PROMO
    assert PROMO.search(text)
    assert not PROMO.search("Summer together?")
