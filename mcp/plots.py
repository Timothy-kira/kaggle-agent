"""Scientific plots as dependency-free SVG.

Why no matplotlib
-----------------
This package has to work on a user's machine with nothing installed but a Python interpreter, so
a plotting library is not an option we get to assume. Measured on this machine: neither matplotlib
nor numpy is present, and a plugin that tells the user to ``pip install`` something is a plugin
half the time unusable.

So the plots are emitted as SVG, written by hand. That is not a compromise for these charts:

- SVG is vector, so a curve stays sharp at any zoom and in a paper's figure;
- it is text underneath, so a number can be printed exactly and read back by a person;
- it needs nothing but ``str`` and the standard library.

What it deliberately does not do: no 3D, no images, no statistical fitting. Those are not what
a run of ablation experiments needs, and every one of them would drag a dependency in behind it.

The chart types here are the ones an experiment tree actually produces:

``line``     one metric over an ordered sequence - the progress curve
``band``     a mean with its noise band - repeated measurements, so the reader can see which
             deltas are inside the noise
``bar``      a categorical comparison - gain by operator, best per method family
``scatter``  two quantities against each other - score against effective cost
``pareto``   quality against cost with the non-dominated frontier marked
``forest``   a criterion with its direction and noise - the per-criterion picture
"""

from __future__ import annotations

import html
import math
import os
from typing import Any, Optional, Sequence

SCHEMA_VERSION = 1

CHART_KINDS = ("line", "band", "bar", "scatter", "pareto", "forest")

# A small palette chosen to stay distinguishable in greyscale and for the common colour-vision
# deficiencies; every series is also distinguished by its marker/line style, so colour is never
# the only channel carrying the information.
PALETTE = ("#3b6ea5", "#c1663a", "#4f8a5b", "#8a5ba8", "#b08a2e", "#6b6b6b")
DASHES = ("", "6 3", "2 3", "8 3 2 3", "1 3", "10 3")

MARGIN = {"top": 58, "right": 24, "bottom": 62, "left": 74}


def _esc(text: Any) -> str:
    return html.escape(str(text), quote=True)


def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if f != f or f in (float("inf"), float("-inf")):
        return default
    return f


def _finite(values: Sequence[Any]) -> list[float]:
    out = []
    for v in values:
        n = _num(v)
        if n is not None:
            out.append(n)
    return out


def _ticks(lo: float, hi: float, count: int = 6) -> list[float]:
    """Human-readable tick positions. Degenerate ranges collapse to a single mid tick."""
    if hi <= lo:
        return [lo]
    raw = (hi - lo) / max(1, count)
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1.0
    for mult in (1, 2, 2.5, 5, 10):
        step = mag * mult
        if step > 0 and raw <= step:
            break
    start = math.floor(lo / step) * step
    ticks = []
    v = start
    while v <= hi + step * 0.5:
        if v >= lo - step * 0.001:
            ticks.append(round(v, 12))
        v += step
    return ticks or [lo, hi]


def _fmt(value: float) -> str:
    """Short, exact-enough label. Avoids float noise like 0.30000000000000004 on an axis."""
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    av = abs(value)
    if av >= 1000 or (av < 0.001 and av > 0):
        return f"{value:.3g}"
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _svg_open(width: int, height: int, title: str, subtitle: str = "") -> list[str]:
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="Segoe UI, Helvetica, Arial, sans-serif">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{MARGIN["left"]}" y="26" font-size="16" font-weight="600" fill="#1a1a1a">'
        f"{_esc(title)}</text>",
    ]
    if subtitle:
        out.append(
            f'<text x="{MARGIN["left"]}" y="45" font-size="11.5" fill="#666">'
            f"{_esc(subtitle)}</text>"
        )
    return out


def _axes(out: list[str], x0: int, y0: int, x1: int, y1: int,
          xticks: list[float], yticks: list[float],
          xlo: float, xhi: float, ylo: float, yhi: float,
          xlabel: str, ylabel: str) -> None:
    out.append(f'<rect x="{x0}" y="{y0}" width="{x1-x0}" height="{y1-y0}" '
               f'fill="#fcfcfd" stroke="#d8d8d8"/>')
    for t in yticks:
        if yhi == ylo:
            continue
        y = y1 - (t - ylo) / (yhi - ylo) * (y1 - y0)
        out.append(f'<line x1="{x0}" y1="{y:.2f}" x2="{x1}" y2="{y:.2f}" '
                   f'stroke="#ececec" stroke-width="1"/>')
        out.append(f'<text x="{x0-8}" y="{y+4:.2f}" font-size="10.5" fill="#555" '
                   f'text-anchor="end">{_esc(_fmt(t))}</text>')
    for t in xticks:
        if xhi == xlo:
            continue
        x = x0 + (t - xlo) / (xhi - xlo) * (x1 - x0)
        out.append(f'<line x1="{x:.2f}" y1="{y1}" x2="{x:.2f}" y2="{y1+4}" '
                   f'stroke="#888" stroke-width="1"/>')
        out.append(f'<text x="{x:.2f}" y="{y1+17}" font-size="10.5" fill="#555" '
                   f'text-anchor="middle">{_esc(_fmt(t))}</text>')
    out.append(f'<text x="{(x0+x1)//2}" y="{y1+40}" font-size="11.5" fill="#333" '
               f'text-anchor="middle">{_esc(xlabel)}</text>')
    out.append(f'<text x="16" y="{(y0+y1)//2}" font-size="11.5" fill="#333" '
               f'text-anchor="middle" transform="rotate(-90 16 {(y0+y1)//2})">'
               f"{_esc(ylabel)}</text>")


def _legend(out: list[str], entries: Sequence[tuple[str, str]], x: int, y: int) -> None:
    """entries are (label, colour). A dash-style marker is drawn first so series remain
    distinguishable without colour."""
    for i, (label, colour) in enumerate(entries):
        ly = y + i * 16
        dash = DASHES[i % len(DASHES)]
        da = f' stroke-dasharray="{dash}"' if dash else ""
        out.append(f'<line x1="{x}" y1="{ly}" x2="{x+20}" y2="{ly}" stroke="{colour}" '
                   f'stroke-width="2.2"{da}/>')
        out.append(f'<text x="{x+26}" y="{ly+4}" font-size="10.5" fill="#444">'
                   f"{_esc(label)}</text>")


# --------------------------------------------------------------------------- charts

def line_chart(series: Sequence[dict[str, Any]], title: str, xlabel: str, ylabel: str,
               subtitle: str = "", width: int = 780, height: int = 440,
               annotations: Optional[Sequence[str]] = None) -> str:
    """One line per series over an ordered x. Points that are None break the line rather than
    being interpolated, because a missing measurement is not a measurement."""
    out = _svg_open(width, height, title, subtitle)
    x0, y0 = MARGIN["left"], MARGIN["top"]
    x1, y1 = width - MARGIN["right"], height - MARGIN["bottom"]

    xs = _finite([pt[0] for s in series for pt in (s.get("points") or [])])
    ys = _finite([pt[1] for s in series for pt in (s.get("points") or [])])
    if not xs or not ys:
        out.append(f'<text x="{width//2}" y="{height//2}" font-size="13" fill="#888" '
                   f'text-anchor="middle">no plottable points</text>')
        return "\n".join(out) + "\n</svg>"

    xlo, xhi = min(xs), max(xs)
    ylo, yhi = min(ys), max(ys)
    if xhi == xlo:
        xhi = xlo + 1
    if yhi == ylo:
        yhi = ylo + 1
    pad = (yhi - ylo) * 0.08
    ylo, yhi = ylo - pad, yhi + pad

    _axes(out, x0, y0, x1, y1, _ticks(xlo, xhi), _ticks(ylo, yhi),
          xlo, xhi, ylo, yhi, xlabel, ylabel)

    for i, s in enumerate(series):
        colour = s.get("color") or PALETTE[i % len(PALETTE)]
        dash = DASHES[i % len(DASHES)]
        da = f' stroke-dasharray="{dash}"' if dash else ""
        run: list[str] = []
        for pt in s.get("points") or []:
            px, py = _num(pt[0]), _num(pt[1])
            if px is None or py is None:
                if len(run) >= 2:
                    out.append(f'<polyline points="{" ".join(run)}" fill="none" '
                               f'stroke="{colour}" stroke-width="2.2"{da}/>')
                run = []
                continue
            X = x0 + (px - xlo) / (xhi - xlo) * (x1 - x0)
            Y = y1 - (py - ylo) / (yhi - ylo) * (y1 - y0)
            run.append(f"{X:.2f},{Y:.2f}")
        if len(run) >= 2:
            out.append(f'<polyline points="{" ".join(run)}" fill="none" stroke="{colour}" '
                       f'stroke-width="2.2"{da}/>')
        for pt in s.get("points") or []:
            px, py = _num(pt[0]), _num(pt[1])
            if px is None or py is None:
                continue
            X = x0 + (px - xlo) / (xhi - xlo) * (x1 - x0)
            Y = y1 - (py - ylo) / (yhi - ylo) * (y1 - y0)
            out.append(f'<circle cx="{X:.2f}" cy="{Y:.2f}" r="3.4" fill="{colour}"/>')

    for i, ann in enumerate(annotations or []):
        out.append(f'<text x="{x1-6}" y="{y0+14+i*14}" font-size="10" fill="#777" '
                   f'text-anchor="end">{_esc(ann)}</text>')
    if len(series) > 1:
        _legend(out, [(s.get("label") or f"series {i+1}",
                       s.get("color") or PALETTE[i % len(PALETTE)])
                      for i, s in enumerate(series)], x0 + 10, y0 + 12)
    return "\n".join(out) + "\n</svg>"


def band_chart(bands: Sequence[dict[str, Any]], title: str, xlabel: str, ylabel: str,
               subtitle: str = "", width: int = 780, height: int = 440) -> str:
    """A mean with its noise band.

    This is the chart that answers the only question repeated measurements exist to answer:
    is that delta real, or is it inside the spread? A mean drawn without its band invites
    exactly the over-reading the band exists to prevent.
    """
    out = _svg_open(width, height, title, subtitle)
    x0, y0 = MARGIN["left"], MARGIN["top"]
    x1, y1 = width - MARGIN["right"], height - MARGIN["bottom"]

    centres = [i for i, _ in enumerate(bands)]
    lows, highs, mids = [], [], []
    for b in bands:
        mid = _num(b.get("mean"), _num(b.get("value"), 0.0))
        sd = _num(b.get("std"), 0.0) or 0.0
        mids.append(mid or 0.0)
        lows.append((mid or 0.0) - sd)
        highs.append((mid or 0.0) + sd)
    if not bands:
        out.append(f'<text x="{width//2}" y="{height//2}" font-size="13" fill="#888" '
                   f'text-anchor="middle">no bands to plot</text>')
        return "\n".join(out) + "\n</svg>"

    ylo, yhi = min(lows), max(highs)
    if yhi == ylo:
        yhi = ylo + 1
    pad = (yhi - ylo) * 0.10
    ylo, yhi = ylo - pad, yhi + pad
    _axes(out, x0, y0, x1, y1, _ticks(0, max(1, len(bands) - 1)), _ticks(ylo, yhi),
          0, max(1, len(bands) - 1), ylo, yhi, xlabel, ylabel)

    step = (x1 - x0) / max(1, len(bands))
    colour = PALETTE[0]
    for i, b in enumerate(bands):
        cx = x0 + step * (i + 0.5)
        half = min(step * 0.32, 26)
        lo_y = y1 - (lows[i] - ylo) / (yhi - ylo) * (y1 - y0)
        hi_y = y1 - (highs[i] - ylo) / (yhi - ylo) * (y1 - y0)
        mid_y = y1 - (mids[i] - ylo) / (yhi - ylo) * (y1 - y0)
        out.append(f'<rect x="{cx-half:.2f}" y="{min(lo_y,hi_y):.2f}" '
                   f'width="{2*half:.2f}" height="{abs(hi_y-lo_y):.2f}" '
                   f'fill="{colour}" fill-opacity="0.18" stroke="none"/>')
        out.append(f'<line x1="{cx-half:.2f}" y1="{mid_y:.2f}" x2="{cx+half:.2f}" '
                   f'y2="{mid_y:.2f}" stroke="{colour}" stroke-width="2.4"/>')
        n = b.get("n")
        if n is not None:
            out.append(f'<text x="{cx:.2f}" y="{y1+32}" font-size="9.5" fill="#777" '
                       f'text-anchor="middle">n={_esc(n)}</text>')
        label = b.get("label")
        if label:
            out.append(f'<text x="{cx:.2f}" y="{y0-8}" font-size="9.5" fill="#666" '
                       f'text-anchor="middle">{_esc(label)}</text>')
    out.append(f'<text x="{x1}" y="{y0+14}" font-size="10" fill="#777" '
               f'text-anchor="end">shaded band = +/- 1 std</text>')
    return "\n".join(out) + "\n</svg>"


def bar_chart(items: Sequence[dict[str, Any]], title: str, ylabel: str, subtitle: str = "",
              xlabel: str = "", width: int = 780, height: int = 400) -> str:
    """Categorical comparison, sorted so the reader does not have to hunt for the biggest bar."""
    out = _svg_open(width, height, title, subtitle)
    x0, y0 = MARGIN["left"], MARGIN["top"]
    x1, y1 = width - MARGIN["right"], height - MARGIN["bottom"]

    rows = sorted(([i for i in items if _num(i.get("value")) is not None]),
                  key=lambda i: -abs(_num(i.get("value"), 0.0)))
    if not rows:
        out.append(f'<text x="{width//2}" y="{height//2}" font-size="13" fill="#888" '
                   f'text-anchor="middle">nothing to compare</text>')
        return "\n".join(out) + "\n</svg>"

    vals = [_num(r.get("value"), 0.0) for r in rows]
    lo, hi = min(0.0, min(vals)), max(0.0, max(vals))
    if hi == lo:
        hi = lo + 1
    pad = (hi - lo) * 0.1
    lo, hi = lo - pad, hi + pad
    _axes(out, x0, y0, x1, y1, _ticks(0, max(1, len(rows) - 1)), _ticks(lo, hi),
          0, max(1, len(rows) - 1), lo, hi, "", ylabel)

    step = (x1 - x0) / max(1, len(rows))
    zero_y = y1 - (0.0 - lo) / (hi - lo) * (y1 - y0)
    for i, r in enumerate(rows):
        v = _num(r.get("value"), 0.0)
        cx = x0 + step * (i + 0.5)
        bw = min(step * 0.6, 60)
        vy = y1 - (v - lo) / (hi - lo) * (y1 - y0)
        colour = r.get("color") or PALETTE[0]
        top, h = min(vy, zero_y), abs(vy - zero_y)
        out.append(f'<rect x="{cx-bw/2:.2f}" y="{top:.2f}" width="{bw:.2f}" height="{h:.2f}" '
                   f'fill="{colour}" fill-opacity="0.82"/>')
        out.append(f'<text x="{cx:.2f}" y="{top-5:.2f}" font-size="9.5" fill="#555" '
                   f'text-anchor="middle">{_esc(_fmt(v))}</text>')
        out.append(f'<text x="{cx:.2f}" y="{y1+16}" font-size="9.5" fill="#555" '
                   f'text-anchor="middle" transform="rotate(-18 {cx:.2f} {y1+16})">'
                   f"{_esc(r.get('label', ''))}</text>")
    out.append(f'<line x1="{x0}" y1="{zero_y:.2f}" x2="{x1}" y2="{zero_y:.2f}" '
               f'stroke="#666" stroke-width="1.2"/>')
    return "\n".join(out) + "\n</svg>"


def scatter_chart(points: Sequence[dict[str, Any]], title: str, xlabel: str, ylabel: str,
                  subtitle: str = "", width: int = 780, height: int = 440,
                  highlight_best: bool = False) -> str:
    """Two quantities against each other, which is how 'quality per unit of compute' is read."""
    out = _svg_open(width, height, title, subtitle)
    x0, y0 = MARGIN["left"], MARGIN["top"]
    x1, y1 = width - MARGIN["right"], height - MARGIN["bottom"]
    pts = [p for p in points if _num(p.get("x")) is not None and _num(p.get("y")) is not None]
    if not pts:
        out.append(f'<text x="{width//2}" y="{height//2}" font-size="13" fill="#888" '
                   f'text-anchor="middle">no plottable points</text>')
        return "\n".join(out) + "\n</svg>"

    xs = [_num(p["x"]) for p in pts]
    ys = [_num(p["y"]) for p in pts]
    xlo, xhi, ylo, yhi = min(xs), max(xs), min(ys), max(ys)
    if xhi == xlo:
        xhi = xlo + 1
    if yhi == ylo:
        yhi = ylo + 1
    xlo -= (xhi - xlo) * 0.08
    xhi += (xhi - xlo) * 0.08
    ylo -= (yhi - ylo) * 0.10
    yhi += (yhi - ylo) * 0.10
    _axes(out, x0, y0, x1, y1, _ticks(xlo, xhi), _ticks(ylo, yhi),
          xlo, xhi, ylo, yhi, xlabel, ylabel)

    best = max(pts, key=lambda p: _num(p["y"], 0.0) - _num(p["x"], 0.0)) if highlight_best else None
    for i, p in enumerate(pts):
        X = x0 + (_num(p["x"]) - xlo) / (xhi - xlo) * (x1 - x0)
        Y = y1 - (_num(p["y"]) - ylo) / (yhi - ylo) * (y1 - y0)
        is_best = best is not None and p is best
        colour = "#c1663a" if is_best else PALETTE[i % len(PALETTE)]
        r = 5.4 if is_best else 4.0
        out.append(f'<circle cx="{X:.2f}" cy="{Y:.2f}" r="{r}" fill="{colour}" '
                   f'fill-opacity="{0.95 if is_best else 0.6}"/>')
        label = p.get("label")
        if label:
            out.append(f'<text x="{X+7:.2f}" y="{Y-6:.2f}" font-size="9.5" '
                       f'fill="{"#a04a22" if is_best else "#666"}">{_esc(label)}</text>')
    if best is not None and best.get("label"):
        out.append(f'<text x="{x0+8}" y="{y0+14}" font-size="10" fill="#a04a22">'
                   f'highlighted: {_esc(best.get("label"))}</text>')
    return "\n".join(out) + "\n</svg>"


def pareto_chart(points: Sequence[dict[str, Any]], title: str, subtitle: str = "",
                 xlabel: str = "", ylabel: str = "quality",
                 width: int = 780, height: int = 440) -> str:
    """Quality against cost, with the non-dominated frontier marked.

    The frontier is the honest answer to 'what should we have tried next': a candidate is only
    worth its cost if nothing cheaper is at least as good.
    """
    out = _svg_open(width, height, title, subtitle)
    x0, y0 = MARGIN["left"], MARGIN["top"]
    x1, y1 = width - MARGIN["right"], height - MARGIN["bottom"]
    pts = [p for p in points if _num(p.get("x")) is not None and _num(p.get("y")) is not None]
    if not pts:
        out.append(f'<text x="{width//2}" y="{height//2}" font-size="13" fill="#888" '
                   f'text-anchor="middle">no plottable points</text>')
        return "\n".join(out) + "\n</svg>"

    xs = [_num(p["x"]) for p in pts]
    ys = [_num(p["y"]) for p in pts]
    xlo, xhi, ylo, yhi = min(xs), max(xs), min(ys), max(ys)
    if xhi == xlo:
        xhi = xlo + 1
    if yhi == ylo:
        yhi = ylo + 1
    xlo -= (xhi - xlo) * 0.08
    xhi += (xhi - xlo) * 0.08
    ylo -= (yhi - ylo) * 0.10
    yhi += (yhi - ylo) * 0.10
    _axes(out, x0, y0, x1, y1, _ticks(xlo, xhi), _ticks(ylo, yhi),
          xlo, xhi, ylo, yhi, "effective cost (quota hours)", "quality")

    def X(p): return x0 + (_num(p["x"]) - xlo) / (xhi - xlo) * (x1 - x0)
    def Y(p): return y1 - (_num(p["y"]) - ylo) / (yhi - ylo) * (y1 - y0)

    ordered = sorted(pts, key=lambda p: _num(p["x"]))
    frontier: list[dict[str, Any]] = []
    best_y = None
    for p in ordered:
        y = _num(p["y"])
        if best_y is None or y > best_y:
            frontier.append(p)
            best_y = y
    fset = {id(p) for p in frontier}
    if len(frontier) >= 2:
        out.append('<polyline points="' + " ".join(f"{X(p):.2f},{Y(p):.2f}" for p in frontier) +
                   '" fill="none" stroke="#3b6ea5" stroke-width="2.2" stroke-dasharray="6 3"/>')
    for i, p in enumerate(pts):
        on = id(p) in fset
        colour = "#3b6ea5" if on else "#b0b0b0"
        out.append(f'<circle cx="{X(p):.2f}" cy="{Y(p):.2f}" r="{5 if on else 3.6}" '
                   f'fill="{colour}" fill-opacity="{0.95 if on else 0.55}"/>')
        if p.get("label"):
            out.append(f'<text x="{X(p)+7:.2f}" y="{Y(p)-6:.2f}" font-size="9.5" '
                       f'fill="#555">{_esc(p.get("label"))}</text>')
    out.append(f'<text x="{x1}" y="{y0+14}" font-size="10" fill="#3b6ea5" text-anchor="end">'
               f"non-dominated frontier</text>")
    return "\n".join(out) + "\n</svg>"


def forest_chart(rows: Sequence[dict[str, Any]], title: str, subtitle: str = "",
                 xlabel: str = "value", ylabel: str = "",
                 width: int = 780, height: int = 420) -> str:
    """Per-criterion values with direction and noise.

    One row per criterion, with an arrow showing which direction is better and a whisker for the
    spread. It exists because a single composite number can be perfectly stable while one
    criterion quietly collapses, and a reader needs to be able to see that at a glance.
    """
    out = _svg_open(width, height, title, subtitle)
    x0, y0 = MARGIN["left"], MARGIN["top"]
    x1, y1 = width - MARGIN["right"], height - MARGIN["bottom"]
    rows = [r for r in rows if _num(r.get("value")) is not None]
    if not rows:
        out.append(f'<text x="{width//2}" y="{height//2}" font-size="13" fill="#888" '
                   f'text-anchor="middle">no criteria to plot</text>')
        return "\n".join(out) + "\n</svg>"

    lo = min(_num(r["value"]) - (_num(r.get("std"), 0.0) or 0.0) for r in rows)
    hi = max(_num(r["value"]) + (_num(r.get("std"), 0.0) or 0.0) for r in rows)
    if hi == lo:
        hi = lo + 1
    pad = (hi - lo) * 0.12
    lo, hi = lo - pad, hi + pad
    _axes(out, x0, y0, x1, y1, _ticks(lo, hi), [], lo, hi, lo, hi, "value", "")

    step = (y1 - y0) / max(1, len(rows))
    for i, r in enumerate(rows):
        v = _num(r["value"])
        sd = _num(r.get("std"), 0.0) or 0.0
        cy = y0 + step * (i + 0.5)
        higher = str(r.get("direction", "higher")) != "lower"
        colour = PALETTE[0] if higher else PALETTE[1]
        cx = x0 + (v - lo) / (hi - lo) * (x1 - x0)
        if sd:
            lx = x0 + (v - sd - lo) / (hi - lo) * (x1 - x0)
            rx = x0 + (v + sd - lo) / (hi - lo) * (x1 - x0)
            out.append(f'<line x1="{lx:.2f}" y1="{cy:.2f}" x2="{rx:.2f}" y2="{cy:.2f}" '
                       f'stroke="{colour}" stroke-width="1.6" stroke-opacity="0.5"/>')
        out.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="4.6" fill="{colour}"/>')
        arrow = "higher is better" if higher else "lower is better"
        out.append(f'<text x="{x0-8}" y="{cy+4:.2f}" font-size="10.5" fill="#333" '
                   f'text-anchor="end">{_esc(r.get("name", ""))}</text>')
        out.append(f'<text x="{cx+9:.2f}" y="{cy-5:.2f}" font-size="9" fill="#777">'
                   f'{_esc(_fmt(v))} &middot; {arrow}</text>')
    return "\n".join(out) + "\n</svg>"


RENDERERS = {
    "line": line_chart,
    "band": band_chart,
    "bar": bar_chart,
    "scatter": scatter_chart,
    "pareto": pareto_chart,
    "forest": forest_chart,
}


# --------------------------------------------------------------------------- store

def plots_dir() -> str:
    return os.path.join(
        os.environ.get("KAGGLE_AGENT_HOME") or os.path.join(
            os.path.expanduser("~"), ".kaggle-agent"),
        "plots",
    )


def render(kind: str, data: dict[str, Any], out_name: str,
           title: str = "", subtitle: str = "", **extra: Any) -> dict[str, Any]:
    """Render one chart to SVG on disk and return where it went.

    A chart that cannot be drawn says so and returns ok:False rather than emitting an empty or
    misleading figure. A blank plot that looks like a result is worse than no plot.
    """
    if kind not in RENDERERS:
        return {"ok": False, "error": f"unknown chart kind {kind!r}; "
                                      f"known: {', '.join(CHART_KINDS)}"}
    title = title or data.get("title") or "experiment"
    subtitle = subtitle or data.get("subtitle") or ""
    series = (data.get("series") or data.get("bands") or data.get("items")
              or data.get("points") or data.get("rows") or [])
    try:
        # chart-specific options (highlight_best, width, height, ...) pass straight through, so
        # adding a chart kind does not mean editing this dispatcher again
        svg = RENDERERS[kind](series, title=title, subtitle=subtitle,
                              xlabel=data.get("xlabel", ""), ylabel=data.get("ylabel", ""),
                              **extra)
    except TypeError as exc:
        return {"ok": False, "error": f"bad arguments for a {kind} chart: {exc}"}
    except Exception as exc:  # noqa: BLE001 - a bad dataset must not crash a run
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    if 'no plottable points' in svg or 'nothing to compare' in svg or 'no bands' in svg:
        return {"ok": False, "error": "the dataset produced no plottable points",
                "hint": "record samples, a metric result, or a cost before plotting this node"}
    d = plots_dir()
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, out_name if out_name.endswith(".svg") else out_name + ".svg")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(svg)
    return {"ok": True, "kind": kind, "path": path, "title": title,
            "bytes": len(svg.encode("utf-8"))}
