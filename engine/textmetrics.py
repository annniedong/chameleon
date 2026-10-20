"""Text measurement that matches the official renderer.

The renderer (renderer/converter) wraps text greedily at real glyph widths
using the brand-kit fonts. Anything that decides whether text "fits" must
use the exact same fonts and the exact same wrap rule, or our layouts will
look fine on paper and clip once rendered. This module reuses the vendored
converter's own helpers so both sides agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

FONTS_DIR = Path(__file__).resolve().parent.parent / "brand_kit" / "fonts"
DEFAULT_LINE_HEIGHT = 1.2


@dataclass(frozen=True)
class TextBox:
    """How a piece of text lays out at a given font size and box width."""

    lines: int
    max_line_width: float
    height: float


@lru_cache(maxsize=1)
def _font_manager():
    from renderer.converter.brand_fonts import load_brand_font_manager

    return load_brand_font_manager(str(FONTS_DIR))


def _font_style(el: dict[str, Any]) -> tuple[str, str]:
    from renderer.converter.convert import (
        _BOLD_WEIGHTS,
        _is_italic,
        _normalize_weight,
        _weight_style_to_font_style,
    )

    weight = _normalize_weight(el.get("font_weight"))
    italic = _is_italic(el.get("font_style") or el.get("italic"))
    return weight, _weight_style_to_font_style(weight in _BOLD_WEIGHTS, italic)


@lru_cache(maxsize=4096)
def _char_widths(family: str, weight: str, style: str, size: float, text: str) -> tuple[float, ...]:
    from renderer.converter.wrap import char_widths

    return tuple(char_widths(_font_manager(), family, weight, style, size, text))


def measure(el: dict[str, Any], font_size: float | None = None, width: float | None = None) -> TextBox:
    """Lay out a text element the way the renderer will.

    `font_size` and `width` override the element's own values, so callers
    can try candidate sizes without copying the element.
    """
    from renderer.converter.wrap import wrap_text

    content = str(el.get("content", ""))
    size = float(font_size if font_size is not None else el.get("font_size", 24))
    box_w = float(width if width is not None else el.get("width", 1.0))
    line_h = float(el.get("line_height", DEFAULT_LINE_HEIGHT))
    if not content:
        return TextBox(lines=0, max_line_width=0.0, height=0.0)

    weight, style = _font_style(el)
    widths = _char_widths(str(el.get("font_family", "")), weight, style, size, content)
    wrapped = wrap_text(content, list(widths), box_w)

    line_widths, current = [], 0.0
    for ch, w in zip(wrapped, widths):
        if ch == "\n":
            line_widths.append(current)
            current = 0.0
        else:
            current += w
    line_widths.append(current)
    lines = len(line_widths)
    return TextBox(lines=lines, max_line_width=max(line_widths), height=lines * size * line_h)


def fits(el: dict[str, Any], font_size: float | None = None, tolerance: float = 0.5) -> bool:
    """True when the text wraps inside its box without spilling over."""
    box = measure(el, font_size=font_size)
    return (
        box.max_line_width <= float(el.get("width", 0)) + tolerance
        and box.height <= float(el.get("height", 0)) + tolerance
    )
