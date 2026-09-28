"""Scientific plots, drawn with numpy and matplotlib.

Why the engine depends on a plotting library
--------------------------------------------
Axis ticks, error bars, layout, hatching and typography are what a plotting library is for.
The previous engine wrote SVG by hand to keep the package dependency-free, and the cost of
that choice showed up as six things this file used to have to do itself and mostly did
adequately rather than correctly. matplotlib is the honest answer for the charts we actually
draw, so the engine uses it, and numpy carries the arrays.

The cost is that a figure now needs two packages present. That belongs in a named step - the
plotting skill checks, installs on the user's word, and only then plots - and here at the point
of use: a missing package produces :class:`BackendMissing`, which carries the exact command
that fixes it. It never surfaces as a traceback from inside a third-party import, and it never
produces a half-drawn figure that looks like a result.

What this engine deliberately does not do
------------------------------------------
No 3D, no images, no statistical fitting, no smoothing. Each of those is a place where a model
can be mistaken for a measurement, and a figure that has been quietly fitted is not evidence.

Output is SVG by default, which is vector: it stays sharp in a paper and its axis values
remain real text rather than pixels traced back into a guess. PNG is available by asking for
it in the output name.
"""

from __future__ import annotations

import os
from typing import Any, Optional, Sequence

CHART_KINDS = ["line", "band", "bar", "scatter", "pareto", "forest"]

# Okabe-Ito, filtered to the five that pass WCAG 3:1 against a white background, in the order
# the vendored auditor reports them clear of each other in greyscale. The full eight-colour
# set was measured and rejected: #E69F00 and #F0E442 fall below 3:1 on white, and #56B4E9
# collides with #E69F00 at dL*=0.8 - two series that look identical on paper.
#
# The numbers below are measurements, not intentions. Re-run them after any change:
#   python skills/scientific-plotting/scripts/palette_audit.py \
#     --palette okabe_ito_on_white --background FFFFFF --role graphical
# It reports 0 contrast failures and 4 of 10 pairs still close in greyscale - which is exactly
# why every series also carries its own marker and dash, and why a claim of "survives greyscale
# printing" is not something colour alone can carry here.
PALETTE = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#000000"]
MARKERS = ["o", "s", "^", "D", "v", "P", "X", "*"]
DASHES = [(None, None), (5, 2), (1.5, 1.5), (6, 2, 1.5, 2), (3, 1.5), (8, 3)]

INSTALL_HINT = (
    'kaggle_sources action="install" packages=\'["matplotlib", "numpy"]\'  '
    "(pip install matplotlib numpy does the same thing)"
)


class BackendMissing(RuntimeError):
    """numpy or matplotlib is not importable, and this says what to do about it.

    A dedicated type rather than a bare ImportError, because the one thing that must never
    happen here is a user being shown a stack trace and left to work out which of the two
    packages is missing and where to install it.
    """

    def __init__(self, missing: Sequence[str]):
        self.missing = list(missing)
        super().__init__(
            "plotting needs " + " and ".join(self.missing) + ", which is not installed. "
            "Run: " + INSTALL_HINT
        )


class NoData(ValueError):
    """The dataset has nothing plottable.

    Raised by the chart functions and turned into ``ok: False`` by :func:`render`. This
    replaces a string search over the finished SVG, which was wrong in a way that could bite:
    a chart whose *title* happened to contain the refusal phrase was reported as a failure
    even though it had been drawn correctly, and a genuinely empty figure whose wording
    changed would have been reported as a success.
    """


def _backend():
    """Import numpy + matplotlib with a headless backend, or say exactly what is missing."""
    import importlib

    missing = []
    modules = {}
    for name in ("numpy", "matplotlib"):
        try:
            modules[name] = importlib.import_module(name)
        except Exception:  # noqa: BLE001 - any import failure means "not usable here"
            missing.append(name)
    if missing:
        raise BackendMissing(missing)
    # Agg before pyplot: a server process has no display, and importing pyplot first can pick
    # a GUI backend that then fails on first draw rather than on import.
    modules["matplotlib"].use("Agg")
    modules["matplotlib.pyplot"] = importlib.import_module("matplotlib.pyplot")
    return modules["numpy"], modules["matplotlib.pyplot"]


def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
    """A float, or None. None means "no measurement", which is not the same as zero."""
    if value is None or isinstance(value, bool):
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out or out in (float("inf"), float("-inf")):  # NaN or infinity is not a measurement
        return default
    return out


def _fmt(value: Any) -> str:
    v = _num(value)
    if v is None:
        return "n/a"
    text = f"{v:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _style(fig, title: str, subtitle: str, xlabel: str, ylabel: str) -> None:
    # "bold", not "600": matplotlib ships DejaVu, which has two weights. Asking for a third is
    # a findfont warning on every single figure, which trains a reader to ignore warnings.
    # The two y positions are 0.05 apart on a figure that is 4.4 inches tall, which is about
    # 32pt - enough for a 13pt line and a 9.5pt line not to sit on top of each other. At 0.035
    # they overlapped, and a title you cannot read is a figure nobody uses.
    fig.suptitle(title, fontsize=13, fontweight="bold", x=0.01, y=0.985, ha="left", va="top")
    if subtitle:
        fig.text(0.01, 0.935, subtitle, fontsize=9.5, color="#666666", ha="left", va="top")
    ax = fig.axes[0] if fig.axes else None
    if ax is None:
        return
    ax.set_facecolor("#fcfcfd")
    ax.grid(True, color="#ececec", linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#d8d8d8")
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=10)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=10)
    ax.tick_params(labelsize=9, colors="#555555")


def _finish(fig, title: str, subtitle: str, xlabel: str, ylabel: str, width: int, height: int):
    fig.set_size_inches(max(4.0, width / 100.0), max(3.0, height / 100.0))
    _style(fig, title, subtitle, xlabel, ylabel)
    # the rect is what stops tight_layout from pushing the axes up under the title block
    fig.tight_layout(rect=(0, 0, 1, 0.90 if subtitle else 0.93))
    return fig


# --------------------------------------------------------------------------- charts

def line_chart(series: Sequence[dict[str, Any]], title: str, xlabel: str, ylabel: str,
               subtitle: str = "", width: int = 780, height: int = 440,
               annotations: Optional[Sequence[str]] = None):
    """One line per series over an ordered x.

    A point with no measurement is drawn as NaN, which matplotlib renders as a break in the
    line. That is the whole point: a gap in the record is not a measurement, and a line drawn
    through it invents one.
    """
    np, plt = _backend()
    fig, ax = plt.subplots()
    drawn = 0
    for i, s in enumerate(series or []):
        xs, ys = [], []
        for pt in (s.get("points") or []):
            if not isinstance(pt, (list, tuple)) or len(pt) < 2:
                xs.append(np.nan)
                ys.append(np.nan)
                continue
            px, py = _num(pt[0]), _num(pt[1])
            xs.append(px if px is not None else np.nan)
            ys.append(py if py is not None else np.nan)
        arr_x = np.asarray(xs, dtype=float)
        arr_y = np.asarray(ys, dtype=float)
        if arr_x.size == 0 or not np.isfinite(arr_y).any():
            continue
        colour = s.get("color") or PALETTE[i % len(PALETTE)]
        dash = DASHES[i % len(DASHES)]
        ax.plot(arr_x, arr_y, color=colour, linewidth=2.0, marker=MARKERS[i % len(MARKERS)],
                markersize=5, markerfacecolor=colour, markeredgecolor="white",
                markeredgewidth=0.8, linestyle="-" if dash[0] is None else (0, dash),
                label=s.get("label") or f"series {i + 1}", zorder=3)
        drawn += 1
    if drawn == 0:
        plt.close(fig)
        raise NoData("no series carried a plottable point")
    for i, ann in enumerate(annotations or []):
        ax.annotate(str(ann), xy=(0.99, 0.97 - i * 0.06), xycoords="axes fraction",
                    ha="right", fontsize=8.5, color="#777777")
    if len(series or []) > 1:
        ax.legend(fontsize=8.5, frameon=False, loc="best")
    return _finish(fig, title, subtitle, xlabel, ylabel, width, height)


def band_chart(bands: Sequence[dict[str, Any]], title: str, xlabel: str, ylabel: str,
               subtitle: str = "", width: int = 780, height: int = 440):
    """A mean with its own spread.

    This is the chart that answers the question repeated measurements exist to answer: is that
    delta real, or is it inside the noise? A mean drawn without its band invites exactly the
    over-reading the band exists to prevent, and `select` already flags `withinNoise` when
    |delta| < 2*std - the figure is what makes that impossible to forget.
    """
    np, plt = _backend()
    rows = [b for b in (bands or []) if _num(b.get("mean"), _num(b.get("value"))) is not None]
    if not rows:
        raise NoData("no band carried a mean")

    fig, ax = plt.subplots()
    xs = np.arange(len(rows), dtype=float)
    mids = np.asarray([_num(b.get("mean"), _num(b.get("value"), 0.0)) for b in rows], dtype=float)
    errs = np.asarray([max(0.0, _num(b.get("std"), 0.0) or 0.0) for b in rows], dtype=float)
    ax.errorbar(xs, mids, yerr=errs, fmt="none", ecolor=PALETTE[0], elinewidth=1.4,
                capsize=4, capthick=1.4, alpha=0.65, zorder=2)
    ax.plot(xs, mids, linestyle="none", marker="o", markersize=6, color=PALETTE[0],
            markeredgecolor="white", markeredgewidth=0.8, zorder=3)
    ax.set_xticks(xs)
    ax.set_xticklabels([str(b.get("label", i + 1)) for i, b in enumerate(rows)],
                       rotation=30, ha="right")
    spread = np.nanmax(errs) if errs.size else 0.0
    for i, b in enumerate(rows):
        n = b.get("n")
        if n is None:
            continue
        # anchored to the LOWER whisker, not to the mean: over a marker the label is
        # unreadable, and "above" is wrong for a negative band.
        ax.annotate(f"n={n}", xy=(i, float(mids[i] - errs[i])), xytext=(0, -13),
                    textcoords="offset points", ha="center", fontsize=8, color="#777777")
    if spread > 0 and not subtitle:
        subtitle = "mean +/- 1 std, where samples were recorded"
    return _finish(fig, title, subtitle, xlabel or "node", ylabel or "metric", width, height)


def bar_chart(items: Sequence[dict[str, Any]], title: str, xlabel: str, ylabel: str,
              subtitle: str = "", width: int = 780, height: int = 440):
    """One bar per item, colour keyed to sign.

    Used for "which operator or family actually produced the gain", so the sign is the message:
    a bar that is grey when it is negative and coloured when it is positive answers the
    question without the reader having to find the zero line.
    """
    _, plt = _backend()
    rows = [it for it in (items or []) if _num(it.get("value")) is not None]
    if not rows:
        raise NoData("no item carried a value")

    fig, ax = plt.subplots()
    labels = [str(it.get("label", i + 1)) for i, it in enumerate(rows)]
    values = [float(_num(it.get("value"), 0.0)) for it in rows]
    colours = [PALETTE[0] if v >= 0 else "#999999" for v in values]
    ax.bar(range(len(rows)), values, color=colours, width=0.62, zorder=3)
    ax.axhline(0, color="#666666", linewidth=0.9, zorder=4)
    ax.set_xticks(range(len(rows)))
    ax.set_xticklabels(labels, rotation=30, ha="right")
    for i, v in enumerate(values):
        ax.annotate(_fmt(v), xy=(i, v), xytext=(0, 4 if v >= 0 else -12),
                    textcoords="offset points", ha="center", fontsize=8, color="#555555")
    return _finish(fig, title, subtitle, xlabel, ylabel or "delta", width, height)


def scatter_chart(points: Sequence[dict[str, Any]], title: str, xlabel: str, ylabel: str,
                  subtitle: str = "", width: int = 780, height: int = 440,
                  highlight_best: bool = False):
    """Quality against cost, one point per run.

    `highlight_best` marks the top point per series, which is the only judgement this chart
    makes: it is there so "is the best one also the cheapest" is a glance rather than a scan.
    """
    np, plt = _backend()
    rows = [p for p in (points or []) if _num(p.get("x")) is not None and _num(p.get("y")) is not None]
    if not rows:
        raise NoData("no point carried both an x and a y")

    fig, ax = plt.subplots()
    groups: dict[str, list[dict[str, Any]]] = {}
    for p in rows:
        groups.setdefault(str(p.get("group") or p.get("label") or "run"), []).append(p)
    for i, (name, pts) in enumerate(sorted(groups.items())):
        colour = pts[0].get("color") or PALETTE[i % len(PALETTE)]
        ax.scatter([float(_num(p["x"])) for p in pts], [float(_num(p["y"])) for p in pts],
                   s=52, color=colour, marker=MARKERS[i % len(MARKERS)],
                   edgecolor="white", linewidth=0.8, label=name, zorder=3)
        for p in pts:
            if p.get("label") and len(pts) <= 40:
                ax.annotate(str(p["label"]), xy=(float(_num(p["x"])), float(_num(p["y"]))),
                            xytext=(6, 4), textcoords="offset points", fontsize=7.5, color="#666666")
        if highlight_best and pts:
            best = max(pts, key=lambda p: float(_num(p["y"])))
            ax.scatter([float(_num(best["x"]))], [float(_num(best["y"]))], s=190,
                       facecolor="none", edgecolor=colour, linewidth=1.8, zorder=4)
    if len(groups) > 1:
        ax.legend(fontsize=8.5, frameon=False)
    return _finish(fig, title, subtitle, xlabel, ylabel, width, height)


def pareto_chart(points: Sequence[dict[str, Any]], title: str, xlabel: str, ylabel: str,
                 subtitle: str = "", width: int = 780, height: int = 440):
    """Quality against effective cost, with the non-dominated frontier marked.

    A candidate is worth its cost only if nothing cheaper is at least as good, so the frontier
    is what turns "what should we try next" from a preference into a reading. Dominated points
    are drawn small and grey, on purpose: they are context, not candidates.
    """
    np, plt = _backend()
    rows = [p for p in (points or []) if _num(p.get("x")) is not None and _num(p.get("y")) is not None]
    if len(rows) < 2:
        raise NoData(f"a frontier needs at least 2 points with a cost; got {len(rows)}")

    pts = sorted(((float(_num(p["x"])), float(_num(p["y"])), p) for p in rows), key=lambda t: t[0])
    frontier, best = [], float("-inf")
    for x, y, p in pts:
        if y > best:
            frontier.append((x, y, p))
            best = y
    on = {id(f[2]) for f in frontier}
    fx = [f[0] for f in frontier]
    fy = [f[1] for f in frontier]

    fig, ax = plt.subplots()
    if len(frontier) >= 2:
        ax.plot(fx, fy, color=PALETTE[0], linewidth=2.0, linestyle=(0, (6, 3)), zorder=3)
    ax.scatter([p[0] for p in pts if id(p[2]) not in on],
               [p[1] for p in pts if id(p[2]) not in on],
               s=42, color="#b0b0b0", alpha=0.65, zorder=2)
    ax.scatter(fx, fy, s=70, color=PALETTE[0], edgecolor="white", linewidth=0.8, zorder=4)
    for x, y, p in pts:
        if p.get("label"):
            ax.annotate(str(p["label"]), xy=(x, y), xytext=(7, 5),
                        textcoords="offset points", fontsize=7.5, color="#555555")
    if len(frontier) >= 2:
        ax.annotate("non-dominated frontier", xy=(fx[-1], fy[-1]), xytext=(-8, 12),
                    textcoords="offset points", ha="right", fontsize=8.5, color=PALETTE[0])
    return _finish(fig, title, subtitle, xlabel or "effective cost (quota hours)",
                   ylabel or "quality", width, height)


def forest_chart(rows: Sequence[dict[str, Any]], title: str, xlabel: str, ylabel: str,
                 subtitle: str = "", width: int = 780, height: int = 440):
    """Per-criterion values with direction and spread.

    One row per criterion, with the direction that counts and a whisker for the spread. It
    exists because a composite can look perfectly steady while one criterion quietly falls
    over, and the reader has to see that rather than infer it from the average.
    """
    np, plt = _backend()
    items = [r for r in (rows or []) if _num(r.get("value")) is not None]
    if not items:
        raise NoData("no criterion carried a value")

    fig, ax = plt.subplots()
    ys = np.arange(len(items), dtype=float)
    vals = np.asarray([float(_num(r["value"])) for r in items], dtype=float)
    errs = np.asarray([max(0.0, _num(r.get("std"), 0.0) or 0.0) for r in items], dtype=float)
    higher = np.asarray([str(r.get("direction", "higher")) != "lower" for r in items])
    colours = np.where(higher, PALETTE[0], PALETTE[1])
    ax.errorbar(vals, ys, xerr=errs, fmt="none", ecolor="#999999", elinewidth=1.3,
                capsize=3, alpha=0.7, zorder=2)
    ax.scatter(vals, ys, s=58, c=colours, edgecolor="white", linewidth=0.8, zorder=3)
    ax.set_yticks(ys)
    ax.set_yticklabels([str(r.get("name", i + 1)) for i, r in enumerate(items)])
    for y, v, up in zip(ys, vals, higher):
        ax.annotate(f"{_fmt(v)} - {'higher is better' if up else 'lower is better'}",
                    xy=(v, y), xytext=(9, -3), textcoords="offset points",
                    fontsize=8, color="#666666")
    lo = float(np.min(vals - errs))
    hi = float(np.max(vals + errs))
    if hi == lo:
        hi = lo + 1
    # A shared x-axis is what makes this a forest plot, and it is also its one real trap:
    # accuracy (0.61) next to latency (240) puts the accuracy row in the noise, and a reader
    # who does not notice reads "accuracy barely moved". The exact value and direction are
    # printed on every row, so the position is the coarse read; the note below stops the axis
    # itself from being over-read. Values stay absolute - rescaling each row to its own range
    # would invent a comparison between quantities that do not share a unit.
    if not subtitle and lo != 0 and abs(hi / lo) > 50:
        subtitle = ("criteria carry different units - read each row against its own value, "
                    "not across rows")
    pad = (hi - lo) * 0.30
    ax.set_xlim(lo - pad * 0.7, hi + pad * 2.0)  # room on the right for the per-row annotation
    return _finish(fig, title, subtitle, xlabel or "value", ylabel, width, height)


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
    """Render one chart to disk and return where it went.

    Three failures are reported rather than drawn, and each is a different kind of problem:
    an unknown kind is a caller's mistake, a missing backend is an environment the user can
    fix in one command, and an empty dataset is a node that has not earned a figure yet. None
    of them produces a file - a blank plot that looks like a result is worse than no plot.
    """
    if kind not in RENDERERS:
        return {"ok": False, "code": "unknown_kind",
                "error": f"unknown chart kind {kind!r}; known: {', '.join(CHART_KINDS)}"}
    title = title or data.get("title") or "experiment"
    subtitle = subtitle or data.get("subtitle") or ""
    series = (data.get("series") or data.get("bands") or data.get("items")
              or data.get("points") or data.get("rows") or [])
    # `dpi` belongs to savefig, not to a chart. Left in `extra` it would be forwarded to every
    # renderer as an unexpected keyword and turn a valid request into "bad arguments".
    dpi = extra.pop("dpi", None) or 160
    plt = None
    fig = None
    try:
        fig = RENDERERS[kind](series, title=title, subtitle=subtitle,
                              xlabel=data.get("xlabel", ""), ylabel=data.get("ylabel", ""),
                              **extra)
        _, plt = _backend()
        d = plots_dir()
        os.makedirs(d, exist_ok=True)
        stem, ext = os.path.splitext(out_name)
        ext = ext.lower() if ext.lower() in (".svg", ".png", ".pdf") else ".svg"
        path = os.path.join(d, (stem or out_name) + ext)
        fig.savefig(path, format=ext.lstrip("."), bbox_inches="tight",
                    facecolor="white", dpi=int(dpi))
    except BackendMissing as exc:
        return {"ok": False, "code": "backend_missing", "error": str(exc),
                "missing": exc.missing, "install": INSTALL_HINT}
    except NoData as exc:
        return {"ok": False, "code": "no_data", "error": str(exc),
                "hint": "record samples, a metric result, or a cost before plotting this node"}
    except TypeError as exc:
        return {"ok": False, "code": "bad_arguments",
                "error": f"bad arguments for a {kind} chart: {exc}"}
    except Exception as exc:  # noqa: BLE001 - a bad dataset must not crash a run
        return {"ok": False, "code": type(exc).__name__, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        # A server process draws many figures over its life; leaving them open is a leak that
        # only shows up as memory nobody can account for hours later.
        if fig is not None and plt is not None:
            try:
                plt.close(fig)
            except Exception:  # noqa: BLE001
                pass
    return {"ok": True, "kind": kind, "path": path, "title": title,
            "backend": "matplotlib", "bytes": os.path.getsize(path)}
