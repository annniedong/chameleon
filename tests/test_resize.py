import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from engine.resize import resize
from tools import tasks
from tools.run_eval import run_task
from tools.validate import errors

ALL_TASKS = list(tasks.generate())


@pytest.mark.parametrize("task", ALL_TASKS, ids=lambda t: t["task_id"])
def test_output_passes_validator(task):
    _, issues, _, crash = run_task(resize, task)
    assert crash is None
    assert errors(issues) == []


def test_same_size_keeps_geometry():
    for task in (t for t in ALL_TASKS if t["task_id"].endswith("__same_size")):
        out = resize(task["source"], task["target_canvas"])
        for src, el in zip(task["source"]["elements"], out["elements"]):
            # Rotated elements may move slightly so their corners stay on canvas.
            inside = (not src.get("rotation") and src["x"] >= 0 and src["y"] >= 0
                      and src["x"] + src["width"] <= task["source"]["canvas"]["width"]
                      and src["y"] + src["height"] <= task["source"]["canvas"]["height"])
            if inside:
                assert (el["x"], el["y"], el["width"], el["height"]) == pytest.approx(
                    (src["x"], src["y"], src["width"], src["height"]), abs=0.01)


json_values = st.recursive(
    st.none() | st.booleans() | st.floats(allow_nan=True) | st.integers() | st.text(max_size=20),
    lambda children: st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=8), children, max_size=4),
    max_leaves=12,
)


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(source=json_values, target=json_values)
def test_never_raises_on_garbage(source, target):
    out = resize(source, target)
    assert isinstance(out, dict) and "canvas" in out and "elements" in out


element = st.fixed_dictionaries(
    {
        "id": st.text(min_size=1, max_size=6),
        "type": st.sampled_from(["text", "image", "shape", "video", 3]),
        "x": st.one_of(st.floats(-500, 2000), st.text(max_size=4), st.none()),
        "y": st.one_of(st.floats(-500, 2000), st.just(float("nan"))),
        "width": st.floats(-100, 3000),
        "height": st.floats(-100, 3000),
    },
    optional={
        "content": st.text(max_size=60),
        "font_family": st.sampled_from(["Solstice Sans", "Solstice Serif", "Nope"]),
        "font_size": st.one_of(st.floats(-5, 300), st.just(float("nan"))),
        "rotation": st.floats(-720, 720),
        "group_id": st.sampled_from([None, "a", "b"]),
        "fit": st.sampled_from(["contain", "cover", "fill"]),
        "asset_id": st.sampled_from(["solstice_bottle.png", "missing.png"]),
        "color": st.just("rgba(255,255,255,1)"),
    },
)


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(elements=st.lists(element, max_size=10),
       W=st.integers(1, 3000), H=st.integers(1, 3000))
def test_malformed_designs_keep_element_count_and_stay_in_canvas(elements, W, H):
    source = {"canvas": {"width": 1080, "height": 1080}, "elements": elements}
    out = resize(json.loads(json.dumps(source)), {"width": W, "height": H})
    assert len(out["elements"]) == len(elements)
    assert out["canvas"] == {"width": W, "height": H}
    for el in out["elements"]:
        assert -0.5 <= el["x"] and el["x"] + el["width"] <= W + 0.5
        assert -0.5 <= el["y"] and el["y"] + el["height"] <= H + 0.5
