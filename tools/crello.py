"""Convert Crello designs (cyberagent/crello on Hugging Face, v5 parquet)
into CHAMELEON task sources.

This is the starter's renderer/converter/convert.py run in reverse:
- coordinates are already pixels and angles degrees (Crello v5)
- every non-text element (SVG, image, coloured background, mask) carries a
  rendered RGBA crop of its box; we save it as an asset file and emit an
  `image` element pointing at it (`cover` when it fills the canvas)
- Crello's 260 font families are not in the brand kit, so each maps to
  Solstice Serif or Solstice Sans; text that no longer fits its box with
  the substitute font is shrunk until it does, so every converted source
  is itself valid

    python -m tools.crello build/crello/raw/test-00000-of-00004.parquet

writes build/crello/designs/<id>.json and build/crello/assets/<id>_<n>.png.
"""

from __future__ import annotations

import io
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "build" / "crello"
ASSETS_DIR = OUT_DIR / "assets"
DESIGNS_DIR = OUT_DIR / "designs"

SERIF = re.compile(
    r"serif|playfair|abril|lora|merriweather|baskerville|cormorant|oleo|garamond|bodoni|crimson|alice|prata|"
    r"cinzel|old standard|vollkorn|arvo|rozha|yeseva|dm serif|libre caslon|spectral|frank ruhl|bitter|"
    r"noticia|domine|cardo|gelasio|marcellus|trirong|ultra|rufina|tinos|times|georgia|slab",
    re.IGNORECASE,
)
MIN_ELEMENTS, MAX_ELEMENTS = 3, 30


def _family(name: str) -> str:
    if SERIF.search(name or "") and "sans" not in (name or "").lower():
        return "Solstice Serif"
    return "Solstice Sans"


def _rgba(color: str) -> str:
    return re.sub(r"\s+", "", color or "rgba(0,0,0,1)")


def _majority(flags: list[bool]) -> bool:
    return bool(flags) and sum(bool(f) for f in flags) * 2 > len(flags)


def _style_runs(colors: list[str], base: str) -> list[dict[str, Any]]:
    """Per-character colours that differ from the base colour, as runs."""
    runs, start = [], None
    norm = [_rgba(c) for c in colors]
    for i, c in enumerate(norm + [base]):
        if c != base and start is None:
            start = i
        elif (c == base or (start is not None and c != norm[start])) and start is not None:
            runs.append({"start": start, "end": i, "color": norm[start]})
            start = i if c != base else None
    return runs


def _shrink_to_fit(el: dict[str, Any]) -> None:
    from engine import textmetrics

    size = float(el["font_size"])
    while size > 1 and not textmetrics.fits(dict(el, font_size=size)):
        size = max(1.0, round(size * 0.94, 2))
    el["font_size"] = size


def convert_row(row: dict[str, Any], names: dict[str, list[str]]) -> dict[str, Any] | None:
    from PIL import Image

    W, H = int(row["canvas_width"]), int(row["canvas_height"])
    n = int(row["length"])
    if not (MIN_ELEMENTS <= n <= MAX_ELEMENTS) or W < 50 or H < 50:
        return None
    did = row["id"]
    elements: list[dict[str, Any]] = []
    for i in range(n):
        kind = names["type"][row["type"][i]]
        x, y = float(row["left"][i]), float(row["top"][i])
        w, h = float(row["width"][i]), float(row["height"][i])
        if w <= 0.5 or h <= 0.5:
            continue
        base = {"id": f"e{i:02d}", "x": round(x, 2), "y": round(y, 2), "width": round(w, 2),
                "height": round(h, 2), "rotation": round(float(row["angle"][i]), 2),
                "opacity": round(float(row["opacity"][i]), 3), "z_index": i}
        if kind == "TextElement":
            content = row["text"][i] or ""
            if not content.strip():
                continue
            if row["capitalize"][i]:
                content = content.upper()
            colors = row["text_color"][i] or []
            color = Counter(_rgba(c) for c in colors).most_common(1)[0][0] if colors else "rgba(0,0,0,1)"
            el = {**base, "type": "text", "content": content,
                  "font_family": _family(names["font"][row["font"][i]]),
                  "font_size": round(float(row["font_size"][i]), 2),
                  "font_weight": "bold" if _majority(row["font_bold"][i] or []) else "regular",
                  "font_style": "italic" if _majority(row["font_italic"][i] or []) else "normal",
                  "color": color, "text_align": names["text_align"][row["text_align"][i]] or "left",
                  "line_height": round(float(row["line_height"][i]) or 1.2, 3),
                  "_crello_font": names["font"][row["font"][i]]}
            runs = _style_runs(colors, color) if len(colors) == len(content) else []
            if runs:
                el["style_runs"] = runs
            _shrink_to_fit(el)
            elements.append(el)
        else:
            img = row["image"][i]
            if not img or not img.get("bytes"):
                continue
            asset = f"{did}_{i:02d}.png"
            path = ASSETS_DIR / asset
            if not path.exists():
                Image.open(io.BytesIO(img["bytes"])).convert("RGBA").save(path)
            covers = x <= 2 and y <= 2 and x + w >= W - 2 and y + h >= H - 2
            elements.append({**base, "type": "image", "asset_id": asset,
                             "fit": "cover" if covers or kind == "ColoredBackground" else "contain",
                             "_crello_type": kind})
    if not any(e["type"] == "text" for e in elements) or len(elements) < MIN_ELEMENTS:
        return None
    return {"id": did, "title": row.get("title", ""), "format": names["format"][row["format"]],
            "source": {"canvas": {"width": W, "height": H}, "elements": elements}}


def rows(parquet: Path) -> Iterator[tuple[dict[str, Any], dict[str, list[str]]]]:
    import pyarrow.parquet as pq

    f = pq.ParquetFile(parquet)
    feats = json.loads(f.schema_arrow.metadata[b"huggingface"])["info"]["features"]
    names = {
        "type": feats["type"]["feature"]["names"],
        "font": feats["font"]["feature"]["names"],
        "text_align": feats["text_align"]["feature"]["names"],
        "format": feats["format"]["names"],
    }
    cols = [c for c in f.schema_arrow.names if c != "preview"]
    for g in range(f.metadata.num_row_groups):
        for row in f.read_row_group(g, columns=cols).to_pylist():
            yield row, names


def main(paths: list[str]) -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    DESIGNS_DIR.mkdir(parents=True, exist_ok=True)
    kept = skipped = 0
    for p in paths:
        for row, names in rows(Path(p)):
            design = convert_row(row, names)
            if design is None:
                skipped += 1
                continue
            (DESIGNS_DIR / f"{design['id']}.json").write_text(json.dumps(design))
            kept += 1
    print(f"converted {kept} designs, skipped {skipped} -> {DESIGNS_DIR}")


if __name__ == "__main__":
    main(sys.argv[1:] or [str(p) for p in sorted((OUT_DIR / "raw").glob("*.parquet"))])
