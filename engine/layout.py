"""A small layout tree, in the spirit of flexbox.

Nodes describe how parts of the design arrange; one scale factor `k`
(relative to the source's pixel sizes) sizes everything. `fit_scale` finds
the largest `k` at which the whole tree fits the canvas, then `place`
writes positions onto the output elements.

  Rigid   one unit scaled uniformly (images, pills, lockups, rotated groups)
  Text    a single text element: font scales with k, lines rewrap to fit
  Column  children stacked top to bottom
  Row     children side by side; text children share the leftover width
  Wrap    equal siblings in rows that wrap (badges, sponsor logos)

Templates (engine/templates.py) build these trees from the design model.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine import textmetrics
from engine.geometry import rotated_extent
from engine.normalize import Element

Size = tuple[float, float]


class Node:
    flexible = False  # can it trade width for height (text that rewraps)?

    def size(self, k: float, aw: float, ah: float) -> Size:
        raise NotImplementedError

    def place(self, out: list[Element], x: float, y: float, w: float, h: float, k: float) -> None:
        raise NotImplementedError

    def indices(self) -> list[int]:
        raise NotImplementedError


@dataclass
class _Member:
    index: int
    cx: float
    cy: float
    width: float
    height: float
    font_size: float | None


class Rigid(Node):
    """A unit that scales uniformly about its own bounding box."""

    def __init__(self, members: list[Element], mult: float = 1.0, align: str = "center"):
        self.mult = mult
        self.align = align
        self.members = [_Member(m.index, m.x + m.width / 2, m.y + m.height / 2, m.width, m.height, m.font_size)
                        for m in members]
        x0 = y0 = float("inf")
        x1 = y1 = float("-inf")
        for m, src in zip(self.members, members):
            ex, ey = rotated_extent(m.width, m.height, src.rotation)
            x0, x1 = min(x0, m.cx - ex), max(x1, m.cx + ex)
            y0, y1 = min(y0, m.cy - ey), max(y1, m.cy + ey)
        self.x0, self.y0 = x0, y0
        self.bw, self.bh = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)

    def size(self, k: float, aw: float, ah: float) -> Size:
        s = k * self.mult
        return self.bw * s, self.bh * s

    def place(self, out, x, y, w, h, k):
        s = k * self.mult
        if self.bw * s > w or self.bh * s > h:
            s *= min(w / (self.bw * s), h / (self.bh * s))
        fw, fh = self.bw * s, self.bh * s
        ox = x if self.align == "left" else x + (w - fw) / 2
        oy = y + (h - fh) / 2
        for m in self.members:
            el = out[m.index]
            el.width, el.height = m.width * s, m.height * s
            el.x = ox + (m.cx - self.x0) * s - el.width / 2
            el.y = oy + (m.cy - self.y0) * s - el.height / 2
            if m.font_size is not None:
                el.font_size = max(m.font_size * s, 1.0)

    def indices(self):
        return [m.index for m in self.members]


class Text(Node):
    """One text element. Its font scales with k; its lines rewrap to the
    available width, up to a comfortable multiple of its source width."""

    flexible = True

    def __init__(self, el: Element, mult: float = 1.0, align: str = "center", stretch: float = 1.6,
                 nowrap: bool = False):
        self.el = el
        self.mult = mult
        self.align = align
        self.stretch = stretch
        # Short labels (CTAs, badges) read as one line; they shrink, never wrap.
        self.nowrap = nowrap
        self.flexible = not nowrap
        self._cache: dict[tuple[float, float], tuple[float, float, float]] = {}

    def _layout(self, k: float, aw: float) -> tuple[float, float, float]:
        """(font size, box width, box height) at scale k within width aw."""
        f = max((self.el.font_size or 16.0) * k * self.mult, 1.0)
        if self.nowrap:
            aw = float("inf")
        wrap = max(min(aw, self.el.width * k * self.mult * self.stretch), 1.0)
        key = (round(f, 3), round(wrap, 1))
        if key not in self._cache:
            box = textmetrics.measure(self.el.text_props(font_size=f, width=wrap))
            if box.max_line_width > wrap and box.max_line_width <= aw:
                # One long word: widen to fit it rather than overflow.
                wrap = box.max_line_width + 1
                box = textmetrics.measure(self.el.text_props(font_size=f, width=wrap))
            width = box.max_line_width + 2 if box.max_line_width <= wrap else box.max_line_width
            self._cache[key] = (f, width, box.height + 2)
        return self._cache[key]

    def size(self, k, aw, ah):
        _, w, h = self._layout(k, aw)
        return w, h

    def place(self, out, x, y, w, h, k):
        f, bw, bh = self._layout(k, w)
        el = out[self.el.index]
        el.font_size = f
        el.width, el.height = min(bw, w), bh
        el.x = x if self.align == "left" else x + (w - el.width) / 2
        el.y = y + (h - bh) / 2

    def indices(self):
        return [self.el.index]


class Column(Node):
    def __init__(self, children: list[Node], gap: float, align: str = "center"):
        self.children = children
        self.gap = gap
        self.align = align
        self.flexible = any(c.flexible for c in children)

    def size(self, k, aw, ah):
        sizes = [c.size(k, aw, ah) for c in self.children]
        return (max((s[0] for s in sizes), default=0.0),
                sum(s[1] for s in sizes) + self.gap * max(len(sizes) - 1, 0))

    def place(self, out, x, y, w, h, k):
        sizes = [c.size(k, w, h) for c in self.children]
        n = len(sizes)
        content = sum(s[1] for s in sizes)
        gap = self.gap
        if n > 1 and h - content - gap * (n - 1) > 0:
            # Spare height loosens the spacing first (up to 3x), then centres.
            gap = min((h - content) / (n + 1), 3 * self.gap) if (h - content) / (n + 1) > gap else gap
        total = content + gap * max(n - 1, 0)
        cy = y + max(h - total, 0) / 2
        cw = max((s[0] for s in sizes), default=w) if self.align == "left" else w
        for child, (_, ch) in zip(self.children, sizes):
            child.place(out, x, cy, cw, ch, k)
            cy += ch + gap

    def indices(self):
        return [i for c in self.children for i in c.indices()]


class Row(Node):
    def __init__(self, children: list[Node], gap: float):
        self.children = children
        self.gap = gap
        self.flexible = any(c.flexible for c in children)

    def _widths(self, k: float, aw: float, ah: float) -> list[Size]:
        n = len(self.children)
        free = aw - self.gap * max(n - 1, 0)
        natural = [c.size(k, free, ah) for c in self.children]
        if sum(s[0] for s in natural) <= free:
            return natural
        fixed = sum(s[0] for c, s in zip(self.children, natural) if not c.flexible)
        flex_total = sum(s[0] for c, s in zip(self.children, natural) if c.flexible)
        room = free - fixed
        if flex_total <= 0 or room <= 0:
            return natural
        return [c.size(k, room * s[0] / flex_total, ah) if c.flexible else s
                for c, s in zip(self.children, natural)]

    def size(self, k, aw, ah):
        sizes = self._widths(k, aw, ah)
        return (sum(s[0] for s in sizes) + self.gap * max(len(sizes) - 1, 0),
                max((s[1] for s in sizes), default=0.0))

    def place(self, out, x, y, w, h, k):
        sizes = self._widths(k, w, h)
        total = sum(s[0] for s in sizes) + self.gap * max(len(sizes) - 1, 0)
        cx = x + max(w - total, 0) / 2
        for child, (cw, _) in zip(self.children, sizes):
            child.place(out, cx, y, cw, h, k)
            cx += cw + self.gap

    def indices(self):
        return [i for c in self.children for i in c.indices()]


class Wrap(Node):
    """Equal siblings laid out in rows that wrap when they run out of width."""

    flexible = True

    def __init__(self, children: list[Node], gap: float):
        self.children = children
        self.gap = gap

    def _rows(self, k: float, aw: float, ah: float) -> list[list[tuple[Node, Size]]]:
        rows: list[list[tuple[Node, Size]]] = [[]]
        used = 0.0
        for c in self.children:
            s = c.size(k, aw, ah)
            if rows[-1] and used + self.gap + s[0] > aw:
                rows.append([])
                used = 0.0
            used += (self.gap if rows[-1] else 0.0) + s[0]
            rows[-1].append((c, s))
        return rows

    def size(self, k, aw, ah):
        rows = self._rows(k, aw, ah)
        widths = [sum(s[0] for _, s in r) + self.gap * (len(r) - 1) for r in rows]
        heights = [max(s[1] for _, s in r) for r in rows]
        return max(widths), sum(heights) + self.gap * (len(rows) - 1)

    def place(self, out, x, y, w, h, k):
        rows = self._rows(k, w, h)
        heights = [max(s[1] for _, s in r) for r in rows]
        total = sum(heights) + self.gap * (len(rows) - 1)
        cy = y + max(h - total, 0) / 2
        for row, rh in zip(rows, heights):
            rw = sum(s[0] for _, s in row) + self.gap * (len(row) - 1)
            cx = x + max(w - rw, 0) / 2
            for child, (cw, _) in row:
                child.place(out, cx, cy, cw, rh, k)
                cx += cw + self.gap
            cy += rh + self.gap

    def indices(self):
        return [i for c in self.children for i in c.indices()]


def fit_scale(root: Node, aw: float, ah: float, k_max: float) -> float | None:
    """Largest k in (0, k_max] at which the tree fits an aw x ah box."""

    def fits(k: float) -> bool:
        w, h = root.size(k, aw, ah)
        return w <= aw + 0.5 and h <= ah + 0.5

    if fits(k_max):
        return k_max
    lo, hi = 1e-3, k_max
    if not fits(lo):
        return None
    for _ in range(28):
        mid = (lo + hi) / 2
        if fits(mid):
            lo = mid
        else:
            hi = mid
    return lo
