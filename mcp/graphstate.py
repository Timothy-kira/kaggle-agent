"""Read the plugin's own relationship graph as a live decision surface.

Why the graph is also the state store
------------------------------------
Every skill in this package already depends on ``relationships.json``: it is the single source
of truth for how the skills relate, and the check fails the build when an index drifts from it.
That makes it the one place in the package that is already guaranteed to be consistent, read by
everything, and validated.

The thing it was missing was **live state**. "Is the user here?" changes minute to minute, and
until now the only way to learn it was to know which tool holds the answer. A subagent that had
not read the presence skill had no way to find out, and could not tell whether asking was
welcome.

So the graph gains a ``state`` section: named pieces of live state, each with the tool that
reads it and the values it can take. Reading the graph and reading the state are then the same
act - ``graph_state()`` returns the declared policy *and* the current value of every piece of
state, resolved through the same tools the agent would have called.

This is deliberately not a second source of truth. The graph declares *where* state lives and
*what it means*; the state modules remain the only writers. The graph cannot drift from reality
because it does not store the values, it points at the readers.

Why it matters for the ask decision
-----------------------------------
Because "should I ask, or should I auto-decide?" is the single most consequential question in
the package - it decides whether a 12-hour run stalls on a question nobody will answer, or
proceeds on a recorded default - it should be answerable from one cheap, validated read rather
than from memory. ``decide()`` turns the declared state into that answer, and refuses to guess:
if the state is unreadable, it returns ``ask``, because the failure mode of asking is a delay and
the failure mode of not asking is an unattended irreversible action.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

GRAPH_FILENAME = "relationships.json"


def _plugin_root() -> Optional[str]:
    """Locate the package root from this module's own location: <root>/mcp/this_file.py."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    if os.path.isfile(os.path.join(root, "skills", GRAPH_FILENAME)):
        return root
    return None


def _skills_graph() -> Optional[str]:
    """The graph path, overridable for tests and sandboxes."""
    override = os.environ.get("KAGGLE_AGENT_GRAPH")
    if override and os.path.isfile(override):
        return override
    root = _plugin_root()
    if root:
        candidate = os.path.join(root, "skills", GRAPH_FILENAME)
        if os.path.isfile(candidate):
            return candidate
    return None


def load_graph() -> dict[str, Any]:
    """The graph itself. An unreadable graph yields ``{}`` rather than raising.

    Every caller of this module has a safe answer for "no graph": ask the user. Failing loud
    here would turn a missing file into a crash in the middle of a decision.
    """
    path = _skills_graph()
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def declared_state() -> dict[str, Any]:
    """The state's declaration: what exists, who reads it, what it may say.

    Underscore-prefixed keys are human documentation and are excluded, so callers iterate real
    state rather than tripping over a comment.
    """
    graph = load_graph()
    state = graph.get("state")
    if not isinstance(state, dict):
        return {}
    return {k: v for k, v in state.items() if not k.startswith("_")}


def _read_presence() -> dict[str, Any]:
    try:
        import presence
    except ImportError:
        return {"ok": False, "error": "presence module unavailable"}
    d = presence.describe()
    return {
        "ok": True,
        "value": d["mode"],
        "values": list(presence.VALID_MODES),
        "detail": {
            "autoAdvanceBudget": d["autoAdvanceBudget"],
            "autoDecisionsUsed": d["autoDecisionsUsed"],
            "remaining": d["remaining"],
            "stale": d["stale"],
            "changedAt": d["changedAt"],
        },
        "readTool": "kaggle_presence action=\"get\"",
        "writeTool": "kaggle_presence action=\"set\" mode=\"present|away\"",
    }


def _read_search_engine() -> dict[str, Any]:
    try:
        import searchengine
    except ImportError:
        return {"ok": False, "error": "search-engine module unavailable"}
    d = searchengine.describe()
    return {
        "ok": True,
        "value": d["engine"],
        "values": list(d["available"]),
        "detail": {"label": d["label"], "language": d["language"], "source": d["source"]},
        "readTool": "kaggle_search_engine action=\"describe\"",
        "writeTool": "kaggle_search_engine action=\"use\" engine=\"<name>\"",
    }


READERS = {
    "presence": _read_presence,
    "search-engine": _read_search_engine,
}


def graph_state() -> dict[str, Any]:
    """Read every piece of live state the graph declares, through its own reader.

    This is the cheap call a decision point makes: one read, the declared policy, and the
    current value. Nothing is cached, so a mode change lands on the next read - which is the
    whole reason a subagent already running sees that the user left.
    """
    declared = declared_state()
    out: dict[str, Any] = {
        "ok": bool(declared),
        "graph": _skills_graph(),
        "declared": declared,
        "state": {},
    }
    for name in sorted(declared.keys()):
        # Underscore-prefixed keys are documentation for humans, not live state.
        if name.startswith("_"):
            continue
        reader = READERS.get(name)
        if reader is None:
            out["state"][name] = {
                "ok": False,
                "error": f"no reader is registered for declared state '{name}'",
            }
            continue
        try:
            out["state"][name] = reader()
        except Exception as exc:  # noqa: BLE001 - a state read must never crash a decision
            out["state"][name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return out


def decide(decision_point: Optional[str] = None) -> dict[str, Any]:
    """Answer the only question that matters at a decision point: ask, or auto-decide?

    The answer is derived from the declared state, not from context or memory. That is the
    point: an agent that never read the presence skill can still get this right with one call.
    """
    pres = _read_presence()
    if not pres.get("ok"):
        # Unreadable state must fail toward asking. The cost of a wrong "ask" is a pause; the
        # cost of a wrong "auto" is an unattended action nobody authorised.
        return {
            "decision": "ask",
            "confidence": "low",
            "reason": (
                "the presence state could not be read, so this fails toward asking. A pause is "
                "recoverable; an unattended irreversible action is not."
            ),
            "state": pres,
        }

    mode = pres.get("value")
    remaining = (pres.get("detail") or {}).get("remaining")
    stale = (pres.get("detail") or {}).get("stale")

    if mode == "away":
        blocked = isinstance(remaining, int) and remaining <= 0
        return {
            "decision": "stop" if blocked else "auto",
            "confidence": "high",
            "mode": mode,
            "decisionPoint": decision_point,
            "reason": (
                "the away budget is spent, so stop and wait"
                if blocked
                else "the user is away: take a conservative, reversible, recorded default "
                     "and keep going. Ask only for irreversible or externally visible actions."
            ),
            "recordWith": "kaggle_presence action=\"record\"",
            "warning": (
                "this away mode has been set for over 12h; it may no longer be true"
                if stale else None
            ),
            "state": pres,
        }

    return {
        "decision": "ask",
        "confidence": "high",
        "mode": mode,
        "decisionPoint": decision_point,
        "reason": (
            "the user is present: ask early and often. A question is cheap here and a wrong "
            "assumption can waste a 12-hour run."
        ),
        "state": pres,
    }


def summary() -> str:
    """A one-block human/machine readable answer: the mode, and what it means right now."""
    d = decide()
    pres = d.get("state") or {}
    detail = pres.get("detail") or {}
    lines = [
        f"ask-or-auto: {d['decision'].upper()}  (mode={d.get('mode', 'unknown')}, "
        f"confidence={d['confidence']})",
        f"reason: {d['reason']}",
    ]
    if d.get("mode") == "away":
        lines.append(
            f"away budget: {detail.get('autoDecisionsUsed')}/{detail.get('autoAdvanceBudget')} "
            f"used ({detail.get('remaining')} left)"
        )
        lines.append(f"record each auto-decision with: {d.get('recordWith')}")
    if d.get("warning"):
        lines.append(f"WARNING: {d['warning']}")
    eng = graph_state().get("state", {}).get("search-engine", {})
    if eng.get("ok"):
        lines.append(
            f"discovery-search engine: {eng['value']} "
            f"(read with kaggle_search_engine action=\"ask\")"
        )
    graph = _skills_graph()
    lines.append(f"graph: {graph or '(not found - falling back to asking)'}")
    return "\n".join(lines)
