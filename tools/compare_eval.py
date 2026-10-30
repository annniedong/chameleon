"""Compare saved runs without using the engine's changing heuristic score.

python -m tools.compare_eval build/comparisons/baseline-replay build/comparisons/after \
  --before-images build/comparisons/before/evaluation
"""
import argparse
import html
import json
import statistics
from collections import Counter
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("before", type=Path)
    ap.add_argument("after", type=Path)
    ap.add_argument("--before-images", type=Path)
    args = ap.parse_args()
    old = {r["task_id"]: r for r in json.loads((args.before / "metrics.json").read_text())}
    new = {r["task_id"]: r for r in json.loads((args.after / "metrics.json").read_text())}
    if old.keys() != new.keys():
        raise SystemExit("Run task IDs differ; use exactly the same benchmark.")
    # Validate source/target identity as well, not just friendly task names.
    for tid in old:
        a = json.loads((args.before / "outputs" / (tid + ".json")).read_text())
        b = json.loads((args.after / "outputs" / (tid + ".json")).read_text())
        if a["task"] != b["task"]:
            raise SystemExit(f"Task changed: {tid}")
    def warnings(rows):
        return Counter(w.split(": ", 1)[1].split(" - ", 1)[0] for r in rows.values() for w in r["warnings"])
    def timing(rows):
        times = sorted(r["seconds"] * 1000 for r in rows.values())
        return f"median {statistics.median(times):.0f}, p95 {times[min(len(times)-1, int(.95*len(times)))]:.0f}, max {max(times):.0f} ms"
    regressions = [t for t in old if not old[t]["errors"] and new[t]["errors"]]
    fixes = [t for t in old if old[t]["errors"] and not new[t]["errors"]]
    old_w, new_w = warnings(old), warnings(new)
    lines = [f"# Comparison: {len(old)} identical tasks", "",
             f"- Before valid: {sum(not r['errors'] for r in old.values())}/{len(old)}",
             f"- After valid: {sum(not r['errors'] for r in new.values())}/{len(new)}",
             f"- New failures: {len(regressions)}; fixed failures: {len(fixes)}",
             f"- Before timing: {timing(old)}", f"- After timing: {timing(new)}",
             f"- Before warnings: {dict(old_w)}", f"- After warnings: {dict(new_w)}",
             f"- Winning alternative interpretations: {sum((r['search'].get('winner') or 0) > 0 for r in new.values())}",
             "", "Scores are not compared because the scorer changed. Inspect rendered pairs for visual quality.",
             "Baseline timings from a replay may differ from the original saved run."]
    (args.after / "comparison.md").write_text("\n".join(lines) + "\n")
    import os
    before_images = args.before_images or args.before
    cards = []
    for tid in old:
        a, b = before_images / (tid + ".png"), args.after / (tid + ".png")
        if not a.exists() or not b.exists():
            continue
        src = args.after / "sources" / (tid + ".png")
        def img(path, label):
            href = Path(os.path.relpath(path.resolve(), args.after.resolve())).as_posix()
            return f'<figure><figcaption>{label}</figcaption><a href="{html.escape(href)}"><img loading="lazy" src="{html.escape(href)}"></a></figure>'
        cards.append("<article><h2>" + html.escape(tid) + "</h2><div>" +
                     (img(src, "Source") if src.exists() else "") +
                     img(a, "Before") + img(b, "After") + "</div></article>")
    page = """<!doctype html><meta charset="utf-8"><title>CHAMELEON comparison</title>
<style>body{font:16px system-ui;margin:24px;background:#eee}article{background:white;padding:16px;margin:16px 0}
h2{font-size:16px}article div{display:flex;gap:16px}figure{margin:0;flex:1;min-width:0}
img{max-width:100%;height:350px;object-fit:contain}figcaption{margin-bottom:8px}</style>
<h1>Source, before and after</h1><p>Inspect readability, hierarchy, grouping and composition. Click an image for full size.</p>"""
    (args.after / "comparison.html").write_text(page + "".join(cards))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
