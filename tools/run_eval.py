"""Run an engine over the generated tasks and report what breaks.

    python -m tools.run_eval                      # our engine, no renders
    python -m tools.run_eval --engine stretch     # a baseline
    python -m tools.run_eval --render             # also render PNGs + contact sheet
    python -m tools.run_eval --only level2        # task ids containing "level2"

Writes build/eval/<engine>/report.md (and PNGs with --render). A crash or
a timeout counts as a failed task, exactly as it would at scoring time.
"""

from __future__ import annotations

import argparse
import copy
import importlib
import json
import signal
import time
import traceback
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from tools import baselines, tasks
from engine import reflow
from tools.validate import Issue, errors, validate

ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "brand_kit" / "assets"
TIMEOUT_S = 60


class _Timeout(Exception):
    pass


def _on_alarm(signum, frame):
    raise _Timeout()


def load_engine(name: str) -> Callable[[dict, dict], dict]:
    if name in baselines.ENGINES:
        return baselines.ENGINES[name]
    module, _, func = (name if ":" in name else "engine.resize:resize").partition(":")
    return getattr(importlib.import_module(module), func)


def run_task(engine, task: dict[str, Any]) -> tuple[Any, list[Issue], float, str | None]:
    source = task["source"]
    pristine = copy.deepcopy(source)
    signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(TIMEOUT_S)
    t0 = time.perf_counter()
    try:
        output = engine(copy.deepcopy(source), dict(task["target_canvas"]))
        crash = None
    except _Timeout:
        output, crash = None, f"timeout after {TIMEOUT_S}s"
    except Exception:
        output, crash = None, traceback.format_exc(limit=3)
    finally:
        signal.alarm(0)
    elapsed = time.perf_counter() - t0
    if crash:
        return None, [Issue("error", "-", "crash", crash.strip().splitlines()[-1])], elapsed, crash
    try:
        json.dumps(output, allow_nan=False)
    except (TypeError, ValueError) as exc:
        return output, [Issue("error", "-", "json", f"output is not valid JSON: {exc}")], elapsed, None
    return output, validate(pristine, output, task["target_canvas"]), elapsed, None


def contact_sheet(rows: list[tuple[str, Path]], path: Path, thumb: int = 220) -> None:
    from PIL import Image, ImageDraw

    cols = 6
    label_h = 28
    sheet = Image.new("RGB", (cols * thumb, ((len(rows) + cols - 1) // cols) * (thumb + label_h)), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (label, png) in enumerate(rows):
        x, y = (i % cols) * thumb, (i // cols) * (thumb + label_h)
        try:
            im = Image.open(png).convert("RGB")
            im.thumbnail((thumb - 8, thumb - 8))
            sheet.paste(im, (x + (thumb - im.width) // 2, y + (thumb - im.height) // 2))
        except Exception:
            draw.rectangle([x + 4, y + 4, x + thumb - 4, y + thumb - 4], outline="red")
        draw.text((x + 4, y + thumb + 2), label[:36], fill="black")
    sheet.save(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="engine.resize:resize")
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--out", type=Path, help="separate run directory; includes task/output JSON and metrics")
    ap.add_argument("--only", default="")
    ap.add_argument("--no-mutations", action="store_true")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    engine = load_engine(args.engine)
    label = args.engine.split(":")[0].replace("engine.", "").replace(".", "_")
    out_dir = args.out or ROOT / "build" / "eval" / label
    out_dir.mkdir(parents=True, exist_ok=True)

    all_tasks = [t for t in tasks.generate(include_mutations=not args.no_mutations) if args.only in t["task_id"]]
    err_codes: Counter[str] = Counter()
    warn_codes: Counter[str] = Counter()
    failed: list[tuple[str, list[Issue]]] = []
    crashes = 0
    times: list[float] = []
    renders: list[tuple[str, Path]] = []

    metrics = []
    (out_dir / "outputs").mkdir(exist_ok=True)
    for task in all_tasks:
        reflow.last_stats = {}
        output, issues, elapsed, crash = run_task(engine, task)
        times.append(elapsed)
        record = {"task_id": task["task_id"], "seconds": elapsed,
                  "errors": [str(i) for i in issues if i.severity == "error"],
                  "warnings": [str(i) for i in issues if i.severity == "warn"],
                  "search": dict(getattr(reflow, "last_stats", {})), "render_error": None}
        metrics.append(record)
        (out_dir / "outputs" / (task["task_id"] + ".json")).write_text(
            json.dumps({"task": task, "output": output}, indent=2, allow_nan=False))
        crashes += crash is not None
        errs = errors(issues)
        err_codes.update(i.code for i in errs)
        warn_codes.update(i.code for i in issues if i.severity == "warn")
        if errs:
            failed.append((task["task_id"], errs))
        if args.verbose or errs:
            mark = "FAIL" if errs else "ok  "
            print(f"{mark} {task['task_id']}  ({elapsed * 1000:.0f} ms)")
            for i in issues if args.verbose else errs[:4]:
                print(f"       {i}")
        if args.render and output is not None and task["mutation"] is None:
            from renderer.converter.convert import render_design_to_png

            png = out_dir / f"{task['task_id']}.png"
            try:
                render_design_to_png(output, ASSETS_DIR, png)
                (out_dir / "sources").mkdir(exist_ok=True)
                render_design_to_png(task["source"], ASSETS_DIR, out_dir / "sources" / png.name)
                renders.append((task["task_id"], png))
            except Exception as exc:
                record["render_error"] = str(exc)
                print(f"       render failed: {exc}")

    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    n = len(all_tasks)
    lines = [
        f"# Eval: {args.engine}",
        "",
        f"- tasks: {n}",
        f"- passed (no errors): {n - len(failed)} / {n}",
        f"- crashes or timeouts: {crashes}",
        f"- time per task: mean {sum(times) / max(n, 1) * 1000:.0f} ms, max {max(times, default=0) * 1000:.0f} ms",
        "",
        "## Errors by type",
        *[f"- {code}: {count}" for code, count in err_codes.most_common()],
        "",
        "## Warnings by type",
        *[f"- {code}: {count}" for code, count in warn_codes.most_common()],
        "",
        "## Failed tasks",
        *[f"- {tid}: " + "; ".join(f"{i.element} {i.code}" for i in errs[:5]) for tid, errs in failed],
    ]
    report = "\n".join(lines) + "\n"
    (out_dir / "report.md").write_text(report)
    print()
    print("\n".join(lines[:6]))
    print("errors:", dict(err_codes.most_common()))
    print("warnings:", dict(warn_codes.most_common()))
    if renders:
        contact_sheet(renders, out_dir / "contact_sheet.png")
        print(f"contact sheet: {out_dir / 'contact_sheet.png'}")
    print(f"report: {out_dir / 'report.md'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
