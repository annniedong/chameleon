import pytest

from engine import check
from engine.normalize import normalize_canvas, normalize_elements
from engine.reflow import _finish, candidates
from engine.score import score
from engine.solve import refine
from engine.understand import understand
from tools import tasks

CASES = ["level2_core__web_banner", "level3_advanced__leaderboard_ad",
         "level3_advanced__skyscraper_ad", "level1_warmup__youtube_thumbnail",
         "cobrand_three__skyscraper_ad", "sponsor_strip__leaderboard_ad"]


def setup(task_id):
    task = next(t for t in tasks.generate() if t["task_id"] == task_id)
    src_w, src_h = normalize_canvas(task["source"]["canvas"], [])
    W, H = task["target_canvas"]["width"], task["target_canvas"]["height"]
    elements = normalize_elements(task["source"]["elements"], [])
    model = understand(elements, src_w, src_h)
    pool = sorted(candidates(elements, src_w, src_h, W, H, model), key=lambda c: -c[2]["total"])
    return model, elements, pool, (src_w, src_h, W, H)


@pytest.mark.parametrize("task_id", CASES)
def test_refined_layout_is_valid_and_adds_no_collisions(task_id):
    model, elements, pool, (src_w, src_h, W, H) = setup(task_id)
    name, out, before = pool[0]
    refined = refine(model, out, src_w, src_h, W, H)
    assert refined is not None, f"solver gave up on {name}"
    _finish(model, refined, src_w, src_h, W, H)
    assert check.problems(refined, elements, W, H) == []
    after = score(model, refined, src_w, src_h, W, H)
    assert after["overlap"] <= before["overlap"] + 1e-6


def test_refine_grows_text_into_free_space():
    model, _, pool, (src_w, src_h, W, H) = setup("level1_warmup__youtube_thumbnail")
    _, out, _ = pool[0]
    refined = refine(model, out, src_w, src_h, W, H)
    headline = next(u for u in model.units if u.role == "headline").members[0].index
    assert refined[headline].font_size > out[headline].font_size


def test_unreadable_floors_degrade_least_important_first():
    # A 728x90 leaderboard cannot give every text a readable size. The
    # solver must still answer, and the headline must not lose to small print.
    model, _, pool, (src_w, src_h, W, H) = setup("level3_advanced__leaderboard_ad")
    _, out, _ = pool[0]
    refined = refine(model, out, src_w, src_h, W, H)
    assert refined is not None
    by_role = {u.role: u for u in model.units}
    headline = refined[by_role["headline"].members[0].index].font_size
    legal = refined[by_role["legal"].members[0].index].font_size
    assert headline >= legal
