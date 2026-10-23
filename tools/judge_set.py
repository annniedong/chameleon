"""Build the hand-judging set: N real Crello designs, each resized to one
catalogue format, rendered before and after, plus a manifest for the
judging page.

    python -m tools.judge_set            # 100 items into build/judge/
    python -m tools.judge_set --n 40

Targets are spread evenly across four kinds of shape change, chosen per
design from the formats that produce that kind of change for its source:
  similar    aspect ratio changes by less than 1.35x
  flip       a real change in shape, e.g. portrait to landscape
  strip      very wide targets (ratio 2.6:1 or more), e.g. 728x90
  tall       very tall targets (ratio 1:2.5 or less), e.g. 160x600
"""

from __future__ import annotations

import argparse
import io
import json
import math
import random
import time
from pathlib import Path

from engine import reflow
from engine.resize import resize
from tools.catalogue import FORMATS
from tools.crello import ASSETS_DIR, DESIGNS_DIR
from tools.validate import errors, validate

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "judge"

LABELS = {
    "instagram_post": "Instagram post", "instagram_portrait": "Instagram portrait",
    "instagram_story": "Instagram story", "youtube_thumbnail": "YouTube thumbnail",
    "facebook_link": "Facebook link", "x_post": "X post", "pinterest_pin": "Pinterest pin",
    "leaderboard_ad": "Leaderboard ad", "skyscraper_ad": "Skyscraper ad",
    "medium_rectangle_ad": "Medium rectangle ad", "billboard_ad": "Billboard ad", "web_banner": "Web banner",
}
BUCKETS = {
    "similar": "Similar shape",
    "flip": "Shape change",
    "strip": "Wide strip",
    "tall": "Tall skyscraper",
}


def bucket_of(src_w: float, src_h: float, w: int, h: int) -> str:
    ratio = w / h
    if ratio >= 2.6:
        return "strip"
    if ratio <= 0.4:
        return "tall"
    return "similar" if abs(math.log(ratio / (src_w / src_h))) < math.log(1.35) else "flip"


def render(design: dict, path: Path, max_size: int) -> None:
    from PIL import Image

    from renderer.converter.convert import render_design_to_png

    tmp = path.with_suffix(".png")
    render_design_to_png(design, ASSETS_DIR, tmp, fonts_dir=ROOT / "brand_kit" / "fonts", max_size=max_size)
    Image.open(tmp).convert("RGB").save(path, quality=85)
    tmp.unlink()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--max-size", type=int, default=900)
    args = ap.parse_args()

    designs = [json.loads(p.read_text()) for p in sorted(DESIGNS_DIR.glob("*.json"))]
    rng = random.Random(args.seed)
    rng.shuffle(designs)
    (OUT / "img").mkdir(parents=True, exist_ok=True)

    order = list(BUCKETS)
    items, errs_total, t0 = [], 0, time.time()
    for design in designs:
        if len(items) >= args.n:
            break
        src = design["source"]
        sw, sh = src["canvas"]["width"], src["canvas"]["height"]
        want = order[len(items) % len(order)]
        options = [f for f, (w, h) in FORMATS.items() if bucket_of(sw, sh, w, h) == want and (w, h) != (sw, sh)]
        if not options:
            continue
        fmt = rng.choice(sorted(options))
        w, h = FORMATS[fmt]
        target = {"width": w, "height": h}

        reflow.last_choice = None
        out = resize(json.loads(json.dumps(src)), target)
        issues = validate(src, out, target)
        errs = errors(issues)
        errs_total += bool(errs)

        pid = f"p{len(items) + 1:03d}"
        render(src, OUT / "img" / f"{pid}_src.jpg", args.max_size)
        render(out, OUT / "img" / f"{pid}_out.jpg", args.max_size)
        (OUT / "outputs").mkdir(exist_ok=True)
        (OUT / "outputs" / f"{pid}.json").write_text(json.dumps({"task": {"source": src, "target_canvas": target},
                                                                 "output": out}))
        items.append({
            "id": pid, "design": design["id"], "title": design.get("title", ""),
            "elements": len(src["elements"]), "bucket": BUCKETS[want],
            "plan": (reflow.last_choice or "fallback").split("(")[0].replace("+solve", " + z3"),
            "source": {"w": sw, "h": sh}, "target": {"w": w, "h": h, "format": fmt, "label": f"{LABELS[fmt]} {w}×{h}"},
            "src_img": f"img/{pid}_src.jpg", "out_img": f"img/{pid}_out.jpg",
            "errors": [str(i) for i in errs], "warnings": sorted({i.code for i in issues if i.severity == "warn"}),
        })
        print(f"{pid} {design['id']} {sw}x{sh} -> {fmt} [{want}] {items[-1]['plan']}" + (f"  ERRORS: {errs[:2]}" if errs else ""))

    (OUT / "manifest.json").write_text(json.dumps({"seed": args.seed, "items": items}, indent=1))
    print(f"\n{len(items)} items, {errs_total} with validator errors, {time.time() - t0:.0f}s -> {OUT}")


if __name__ == "__main__":
    main()
