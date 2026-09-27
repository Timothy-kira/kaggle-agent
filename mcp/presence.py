"""Is the user at the keyboard, or has the work been handed over?

Why this is a file and not a prompt
----------------------------------
The question "can I decide this myself, or must I stop and ask?" is asked at decision points
scattered across many skills, and sometimes by subagents that were launched before the user
said they were leaving. If the answer lived in the system prompt or in the current turn's
context, then:

- a subagent already running would never learn that the user left, and would keep asking
  questions nobody is there to answer, and
- the same decision would be answered differently in different parts of the run.

So the answer lives in one small file that every decision point re-reads, the same way
``logmonitor`` re-reads the fetch interval on every cycle. ``load()`` opens the file on every
call and nothing here is cached. That is the whole mechanism: the user says "I'm leaving",
the state changes once, and every decision point - including ones already in flight - sees it
on their next read.

The two states
--------------
``present``
    The user is at the machine and is watching. Being here means the work is *collaborative*:
    ask early, ask often, and prefer a question over a guess. A question costs a few seconds
    and turns a wrong assumption into a corrected one; a silent wrong guess can waste an
    entire 12-hour run.

``away``
    The user has left and is not coming back for a while. Being away means the work is
    *autonomous*: do not stop for questions that can be answered with a stated, reversible,
    conservative default. Record the default you chose, and keep going. Stopping to ask a
    question that nobody will answer does not make the work safer - it makes it stall.

What away mode is NOT
---------------------
It is not a licence to do anything irreversible. ``away`` lowers the ask threshold for
*decisions*, and changes nothing about *permissions*. Creating a GitHub repo, pushing a
handoff to a remote, retiring a kernel, or spending the last of someone's accelerator quota
still requires explicit confirmation, whether or not the user is present. The presence mode
decides whether a **decision point** is worth a question; it never grants authority that the
tools themselves require.

This is the failure this module exists to prevent: an "autonomous" mode that quietly becomes
"unattended irreversible actions". The safe default for the away state is *progress*, not
*permanence*.

Storage: ``<home>/presence.json``, where ``<home>`` is ``KAGGLE_AGENT_HOME`` when set, else
``~/.kaggle-agent``. The file holds the mode and a small auto-advance budget. No credential, no
token, no personal data.

The auto-advance budget
-----------------------
Unattended work is also *bounded* work. If ``away`` means "never ask", an overnight run could
walk straight through twelve hours of quota on a line of attack that was wrong from the first
hour. So away mode carries a budget: the number of consequential auto-decisions the run may
make before it must stop and wait for the user. It is a circuit breaker, not a permission -
hitting it stops the work, it does not escalate it.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

SCHEMA_VERSION = 1
CONFIG_FILENAME = "presence.json"

VALID_MODES = ("present", "away")

# "present" is the default, and that default is a deliberate choice rather than a neutral one.
# A user who has said nothing is treated as present, so the worst case for an unstated user is
# a few extra questions - never unattended irreversible actions. Defaulting to "away" would
# make silence mean consent, which is the dangerous direction to err in.
DEFAULT_MODE = "present"

# How many consequential auto-decisions an away run may take before it has to stop and wait.
# Low enough that a wrong line of attack is caught within a fraction of a run; high enough
# that ordinary unattended progress (read, profile, try, measure, record) does not trip it.
DEFAULT_AUTO_ADVANCE_BUDGET = 6

# Beyond this, a timer is reported as stale so a user who forgot to switch back does not get
# an unattended run that has been "away" for a week.
STALE_AFTER_SECONDS = 12 * 3600


def _home() -> str:
    return os.environ.get("KAGGLE_AGENT_HOME") or os.path.join(
        os.path.expanduser("~"), ".kaggle-agent"
    )


def config_path() -> str:
    return os.path.join(_home(), CONFIG_FILENAME)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _epoch_now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def defaults() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "mode": DEFAULT_MODE,
        "autoAdvanceBudget": DEFAULT_AUTO_ADVANCE_BUDGET,
        "autoDecisionsUsed": 0,
        "autoDecisions": [],
        "changedAt": None,
        "changedEpoch": None,
        "note": None,
    }


def load() -> dict[str, Any]:
    """Read the presence state from disk on every call. Never cached, by design.

    Same reasoning as ``logmonitor.load()``: a subagent that captured the mode at launch
    would keep asking questions after the user left, which is precisely the bug this state
    is meant to fix. A missing or half-written file yields ``present``, the safe direction.
    """
    data = defaults()
    try:
        with open(config_path(), "r", encoding="utf-8") as fh:
            stored = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return data
    if not isinstance(stored, dict):
        return data

    mode = stored.get("mode")
    if isinstance(mode, str) and mode in VALID_MODES:
        data["mode"] = mode
    if isinstance(stored.get("autoAdvanceBudget"), int) and stored["autoAdvanceBudget"] >= 0:
        data["autoAdvanceBudget"] = stored["autoAdvanceBudget"]
    if isinstance(stored.get("autoDecisionsUsed"), int) and stored["autoDecisionsUsed"] >= 0:
        data["autoDecisionsUsed"] = stored["autoDecisionsUsed"]
    if isinstance(stored.get("autoDecisions"), list):
        data["autoDecisions"] = [d for d in stored["autoDecisions"] if isinstance(d, dict)]
    for key in ("changedAt", "changedEpoch", "note"):
        if isinstance(stored.get(key), (str, int)) or stored.get(key) is None:
            data[key] = stored.get(key)
    return data


def _write(data: dict[str, Any]) -> str:
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    # Replace rather than truncate, so a decision point reading mid-cycle never sees a
    # half-written file and act on a truncated mode.
    os.replace(tmp, path)
    return path


def set_mode(mode: str, note: Optional[str] = None) -> dict[str, Any]:
    """Switch between present and away, and report what actually changed.

    Switching back to ``present`` resets the auto-advance budget, because that budget belongs
    to one unattended run: a fresh attended session should not inherit a nearly-spent
    allowance from an earlier departure.
    """
    value = (mode or "").strip().lower()
    if value not in VALID_MODES:
        raise ValueError(
            f"mode must be one of {', '.join(VALID_MODES)}, got {mode!r}"
        )

    data = load()
    before = data["mode"]
    data["mode"] = value
    data["changedAt"] = _now()
    data["changedEpoch"] = _epoch_now()
    if note:
        data["note"] = note
    if value == "present":
        data["autoDecisionsUsed"] = 0
        data["autoDecisions"] = []
    changed = value != before

    _write(data)
    return {
        "ok": True,
        "previousMode": before,
        "mode": value,
        "changed": changed,
        "autoAdvanceBudget": data["autoAdvanceBudget"],
        "autoDecisionsUsed": data["autoDecisionsUsed"],
        "updatedAt": data["changedAt"],
        "path": config_path(),
        "effective": _mode_sentence(value),
    }


def _mode_sentence(mode: str) -> str:
    if mode == "away":
        return (
            "away: proceed unattended. Take conservative, reversible defaults at ordinary "
            "decision points, record what you decided and why, and do not ask questions "
            "nobody is there to answer. Irreversible or externally visible actions still "
            "require explicit confirmation."
        )
    return (
        "present: the user is here. Ask early and often, prefer a question to a guess, and "
        "surface a decision point as soon as it is a real choice rather than a formality."
    )


def record_auto_decision(decision: str, rationale: str = "") -> dict[str, Any]:
    """Log one auto-decided call, and report whether the away budget still has room.

    This is the circuit breaker. When the budget is spent, the caller must stop and wait for
    the user rather than continue on its own - the whole point of the budget is that a run
    which is confidently wrong should fail *early* instead of burning a night.
    """
    text = (decision or "").strip()
    if not text:
        raise ValueError("decision must name the call that was made, got an empty string")

    data = load()
    budget = int(data["autoAdvanceBudget"])
    used = int(data["autoDecisionsUsed"])
    remaining = max(0, budget - used)

    if remaining <= 0:
        return {
            "ok": False,
            "stopped": True,
            "mode": data["mode"],
            "autoAdvanceBudget": budget,
            "autoDecisionsUsed": used,
            "remaining": 0,
            "reason": (
                f"the away budget is spent ({used}/{budget} auto-decisions). Stop and wait "
                "for the user rather than continuing unattended on your own judgement."
            ),
        }

    used += 1
    data["autoDecisionsUsed"] = used
    data["autoDecisions"] = list(data["autoDecisions"]) + [
        {"decision": text, "rationale": rationale, "at": _now()}
    ]
    _write(data)
    remaining = max(0, budget - used)
    return {
        "ok": True,
        "stopped": False,
        "mode": data["mode"],
        "decision": text,
        "rationale": rationale,
        "autoAdvanceBudget": budget,
        "autoDecisionsUsed": used,
        "remaining": remaining,
        "path": config_path(),
    }


def reset_budget(budget: Optional[int] = None) -> dict[str, Any]:
    data = load()
    if budget is not None:
        try:
            value = int(budget)
        except (TypeError, ValueError):
            raise ValueError(f"budget must be an integer, got {budget!r}")
        if value < 0:
            raise ValueError(f"budget must not be negative, got {value}")
        data["autoAdvanceBudget"] = value
    data["autoDecisionsUsed"] = 0
    data["autoDecisions"] = []
    _write(data)
    return {"ok": True, "config": data, "path": config_path()}


def is_stale(data: Optional[dict[str, Any]] = None) -> bool:
    """True when an ``away`` mode has been sitting long enough to be suspect."""
    data = data or load()
    if data.get("mode") != "away":
        return False
    epoch = data.get("changedEpoch")
    if not isinstance(epoch, int):
        return False
    return (_epoch_now() - epoch) > STALE_AFTER_SECONDS


def describe() -> dict[str, Any]:
    data = load()
    return {
        "mode": data["mode"],
        "autoAdvanceBudget": data["autoAdvanceBudget"],
        "autoDecisionsUsed": data["autoDecisionsUsed"],
        "remaining": max(0, int(data["autoAdvanceBudget"]) - int(data["autoDecisionsUsed"])),
        "changedAt": data["changedAt"],
        "note": data["note"],
        "path": config_path(),
        "exists": os.path.isfile(config_path()),
        "stale": is_stale(data),
        "guidance": _mode_sentence(data["mode"]),
    }
