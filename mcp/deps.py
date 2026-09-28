"""The plotting backend, and the optional extras around it.

The rule this module encodes
---------------------------
Figures are drawn with numpy and matplotlib. That is the engine, not an upgrade to it: axis
ticks, error bars and layout are what a plotting library is for, and re-implementing them is
where a from-scratch renderer quietly lies to the reader.

So plotting has exactly one prerequisite, and it is handled where it belongs - as a named step
in the plotting skill:

  1. :func:`probe` reports whether the backend is present and, when it is not, the exact
     command that fixes it.
  2. Nothing here installs anything by itself, ever. No chart call, no skill load, no server
     start. A plugin that mutates a user's Python environment on first run is a plugin nobody
     can audit, and it needs network at a moment the user did not ask for.
  3. :func:`install` does exactly what it says and is only ever reached after a human has
     said yes. The refusal is not ceremony: the two failure modes are "did not install", which
     is a plainer figure, and "installed badly", which is a broken environment.

What is genuinely optional is everything else - deeper statistics, unit propagation, tabular
helpers. Those add capability without being able to block a figure, which is the line the
plotting backend deliberately sits on the other side of.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from typing import Any, Optional

# The backend. A figure cannot be produced without these two, so `probe` reports them as a
# prerequisite rather than as an extra, and `install` names them first.
BACKEND = ("matplotlib", "numpy")

INSTALL_HINT = (
    'kaggle_sources action="install" packages=\'["matplotlib", "numpy"]\'  '
    "(pip install matplotlib numpy does the same thing)"
)

# What each extra is actually for, so the answer to "should I install this" is informed rather
# than a guess.
OPTIONAL = {
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
    "seaborn": {
        "why": "statistical plot types (box, violin, strip) that the six bundled charts do not cover",
        "unlocks": ["distribution-charts"],
        "sizeHint": "~1 MB",
    },
    "pypdf": {
        "why": "lets the vendored scripts inspect a delivered PDF's page size and fonts",
        "unlocks": ["export-inspection"],
        "sizeHint": "~300 kB",
    },
}

# Everything install() will accept: the backend plus the extras. The backend is listed so that
# one call fixes a fresh machine, and so that the refusal message names the whole vocabulary.
INSTALLABLE = {name: {"why": "the plotting backend - figures are drawn with it",
                      "unlocks": ["all-six-charts"], "sizeHint": "~23 MB for both"}
               for name in BACKEND}
INSTALLABLE.update(OPTIONAL)

# The engine's own capability list, so "what can I do right now" is answerable without anyone
# having to read the source.
ENGINE = {
    "backend": "numpy + matplotlib (headless Agg)",
    "chartTypes": ["line", "band", "bar", "scatter", "pareto", "forest"],
    "output": "SVG by default - vector, so a figure stays sharp in a paper - plus PNG and PDF "
              "by naming the extension",
    "properties": [
        "a point with no measurement breaks the line instead of being interpolated across",
        "colour plus dash plus marker, so a series is separable without relying on hue",
        "refuses a dataset with nothing plottable instead of drawing a blank figure",
        "reports what it could not draw, which is more useful than a figure that is missing",
    ],
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
    """Report the environment. Read-only; installs nothing, ever.

    `plottingReady` is the honest answer to "can this machine draw a figure right now", which
    used to be hard-coded True because the engine needed nothing. It is False when the backend
    is absent, and `nextStep` then carries the command that fixes it - so a caller never has to
    guess between "nothing to do" and "you have to install something first".
    """
    present, missing = {}, {}
    for name, meta in INSTALLABLE.items():
        if _installed(name):
            present[name] = {"version": _version(name), **meta}
        else:
            missing[name] = meta
    absent_backend = [n for n in BACKEND if n in missing]
    ready = not absent_backend
    out: dict[str, Any] = {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "engine": ENGINE,
        "backend": {"required": list(BACKEND), "present": [n for n in BACKEND if n in present]},
        "present": present,
        "missing": missing,
        "plottingReady": ready,
    }
    if ready:
        out["note"] = ("the plotting backend is present; the remaining packages only add chart "
                       "types and deeper statistics, and nothing here blocks a figure")
    else:
        out["code"] = "backend_missing"
        out["error"] = ("plotting needs " + " and ".join(absent_backend)
                        + ", which is not installed")
        out["install"] = INSTALL_HINT
        out["nextStep"] = (
            "Ask the user before running this - it changes their Python environment. Once they "
            "have agreed: " + INSTALL_HINT
        )
        out["note"] = ("No figure can be drawn until the backend is present. Everything else in "
                       "the table is optional.")
    return out


def install(names: list[str], timeout: int = 600) -> dict[str, Any]:
    """Install the named optional packages. ONLY ever called after a human said yes.

    Kept separate from probe() and from every code path a skill can take, so that "the agent
    decided to install something" is not a reachable state.
    """
    wanted = [n for n in (names or []) if n in INSTALLABLE]
    unknown = [n for n in (names or []) if n not in INSTALLABLE]
    if unknown:
        return {"ok": False, "code": "unknown_packages",
                "message": f"not installable here: {', '.join(unknown)}; "
                           f"known: {', '.join(sorted(INSTALLABLE))}"}
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
        "plottingReady": not absent_backend_after(todo),
        "note": ("the backend is importable now, so the next chart call will draw. Installing it "
                 "does not change any figure this package already produced."),
    }


def absent_backend_after(installed_now: list[str]) -> list[str]:
    """Which of the backend are STILL missing once `installed_now` is accounted for.

    Asked after a successful pip run, so a half-satisfied backend ("matplotlib installed, numpy
    already was") reports as ready instead of leaving the caller to guess from the list.
    """
    return [n for n in BACKEND if n not in installed_now and not _installed(n)]


# --------------------------------------------------------------------------- backend

def matplotlib_available() -> bool:
    return all(_installed(n) for n in BACKEND)


def available_chart_types() -> dict[str, list[str]]:
    """Which chart types are drawable right now, by backend.

    Empty `ready` is the honest answer on a machine with no backend: naming six chart types that
    cannot be drawn is the same kind of decorative field this package has removed elsewhere.
    """
    kinds = list(ENGINE["chartTypes"])
    return {"ready": kinds if matplotlib_available() else [], "all": kinds,
            "backend": ENGINE["backend"]}
