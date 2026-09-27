"""Optional accelerators: detect what is missing, never install it unasked.

The rule this module encodes
---------------------------
The bundled plotting engine is pure standard library and always works. That is the guarantee, and
it cannot be conditional on somebody's machine having pip and a network. So nothing here is ever
required for a chart to be produced.

What is optional is *richness*. A handful of chart types - heatmaps, violins, contours, dense
grids - are genuinely better drawn by matplotlib, and a user who already has it should get that
rather than our approximation. So:

  1. :func:`probe` reports what is present, what is missing, and what each would buy.
  2. Nothing is installed by :func:`probe`, by any chart call, or by any skill loading. Ever.
  3. :func:`install` exists, does exactly what it says, and is only ever called after a human
     has said yes.

Why not install silently
------------------------
Because a plugin that mutates a user's Python environment on first run is a plugin nobody can
audit. It can conflict with what the user already pinned, it needs network at a moment the user
did not expect, and when it fails there is no chart at all. The failure mode of "did not install"
is a slightly plainer figure; the failure mode of "installed badly" is a broken environment.

So: report, ask, install on consent, and keep working either way.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from typing import Any, Optional

# What each accelerator is actually for, so the answer to "should I install this" is informed
# rather than a guess. The bundled engine is not listed here because it is not optional.
OPTIONAL = {
    "matplotlib": {
        "why": "heatmaps, violins, contour and dense matrix figures that the bundled SVG "
               "engine approximates; also a second opinion when a figure looks wrong",
        "unlocks": ["heatmap", "violin", "contour", "matrix", "matplotlib-backend"],
        "sizeHint": "~8 MB wheel plus a matching numpy",
    },
    "numpy": {
        "why": "faster numeric work for large trees; the bundled engine does not need it",
        "unlocks": ["large-tree-performance"],
        "sizeHint": "~15 MB",
    },
    "scipy": {
        "why": "distribution functions and tests, if you want them for effect sizes and "
               "confidence intervals rather than the closed-form approximations",
        "unlocks": ["statistical-power", "assumption-checks"],
        "sizeHint": "~35 MB",
    },
    "statsmodels": {
        "why": "regression diagnostics and power calculations",
        "unlocks": ["statistical-power", "assumption-checks"],
        "sizeHint": "~10 MB",
    },
    "pint": {
        "why": "unit conversion with uncertainty propagation, if you track quantities with units",
        "unlocks": ["unit-propagation"],
        "sizeHint": "~1 MB",
    },
    "pandas": {
        "why": "tabular result handling; the tree store is plain JSON and does not need it",
        "unlocks": ["dataframe-workflows"],
        "sizeHint": "~12 MB",
    },
}

# The bundled engine's own capability list, so "what can I do right now" is answerable without
# anyone having to read the source.
BUNDLED = {
    "engine": "pure-stdlib SVG (bundled, always available)",
    "chartTypes": ["line", "band", "bar", "scatter", "pareto", "forest"],
    "properties": [
        "vector output, so figures stay sharp in a paper",
        "no dependency, so it works on a machine that has nothing installed",
        "text underneath, so axis values can be read exactly",
        "colour plus dash/marker, so a figure survives greyscale and colour-vision deficiency",
    ],
    "refuses": "a chart with no plottable points returns an error rather than an empty figure",
}


def _installed(name: str) -> bool:
    if name in sys.modules:
        return True
    try:
        importlib.import_module(name)
        return True
    except Exception:  # noqa: BLE001 - any import failure means "not usable"
        return False


def _version(name: str) -> Optional[str]:
    try:
        mod = importlib.import_module(name)
        return str(getattr(mod, "__version__", "unknown"))
    except Exception:  # noqa: BLE001
        return None


def probe() -> dict[str, Any]:
    """Report the environment. Read-only; installs nothing, ever."""
    present, missing = {}, {}
    for name, meta in OPTIONAL.items():
        if _installed(name):
            present[name] = {"version": _version(name), **meta}
        else:
            missing[name] = meta
    return {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "bundled": BUNDLED,
        "present": present,
        "missing": missing,
        "plottingReady": True,
        "note": (
            "plotting works right now regardless of this table. These packages only add chart "
            "types and deeper statistics; nothing here is a prerequisite for drawing a figure."
        ),
    }


def install(names: list[str], timeout: int = 600) -> dict[str, Any]:
    """Install the named optional packages. ONLY ever called after a human said yes.

    Kept separate from probe() and from every code path a skill can take, so that "the agent
    decided to install something" is not a reachable state.
    """
    wanted = [n for n in (names or []) if n in OPTIONAL]
    unknown = [n for n in (names or []) if n not in OPTIONAL]
    if unknown:
        return {"ok": False, "code": "unknown_packages",
                "message": f"not optional accelerators: {', '.join(unknown)}; "
                           f"known: {', '.join(sorted(OPTIONAL))}"}
    if not wanted:
        return {"ok": False, "code": "nothing_to_do", "message": "no packages named"}

    already = [n for n in wanted if _installed(n)]
    todo = [n for n in wanted if n not in already]
    if not todo:
        return {"ok": True, "installed": [], "alreadyPresent": already,
                "message": "everything requested was already available"}

    cmd = [sys.executable, "-m", "pip", "install", *todo]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": "timeout",
                "message": f"pip did not finish within {timeout}s; plotting is unaffected"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "code": "pip_failed", "message": f"{type(exc).__name__}: {exc}"}

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-4:]
        return {"ok": False, "code": "pip_failed",
                "message": "pip failed; plotting is unaffected by this",
                "detail": "\n".join(tail)}
    return {
        "ok": True,
        "installed": todo,
        "alreadyPresent": already,
        "versions": {n: _version(n) for n in todo},
        "note": ("the new chart types are available immediately; the bundled engine is still "
                 "the fallback and nothing that worked before has changed"),
    }


# --------------------------------------------------------------------------- backend

def matplotlib_available() -> bool:
    return _installed("matplotlib") and _installed("numpy")


def available_chart_types() -> dict[str, list[str]]:
    """Which chart types are drawable right now, by backend."""
    bundled = list(BUNDLED["chartTypes"])
    extra = ["heatmap", "violin", "contour"] if matplotlib_available() else []
    return {"bundled": bundled, "matplotlibOnly": extra, "all": bundled + extra}
