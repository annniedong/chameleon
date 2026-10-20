"""Print what engine/understand.py makes of a design.

    python -m tools.show_roles                     # the three samples
    python -m tools.show_roles input/foo.json      # any task file
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from engine.normalize import normalize_canvas, normalize_elements
from engine.understand import understand

ROOT = Path(__file__).resolve().parent.parent


def show(path: Path) -> None:
    task = json.loads(path.read_text())
    source = task.get("source", task)
    notes: list[str] = []
    W, H = normalize_canvas(source.get("canvas"), notes)
    model = understand(normalize_elements(source.get("elements"), notes), W, H)
    print(f"\n{path.name}  ({W:.0f}x{H:.0f}, {model.alignment}-aligned)")
    print(f"  {'unit':28} {'role':11} {'imp':>5} {'conf':>5}  {'set':16} why")
    for u in sorted(model.units, key=lambda u: -u.importance):
        print(f"  {u.id[:28]:28} {u.role:11} {u.importance:5.2f} {u.confidence:5.2f}  {(u.set_id or ''):16} {u.why}")
    print("  reading order:", " > ".join(u.id for u in model.reading_order))


def main() -> None:
    paths = [Path(p) for p in sys.argv[1:]] or sorted((ROOT / "input").glob("*.json"))
    for p in paths:
        show(p)


if __name__ == "__main__":
    main()
