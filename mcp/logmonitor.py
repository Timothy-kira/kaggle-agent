"""Shared log-monitor configuration: the one place the fetch interval is decided.

Why the interval lives in a file and not in a prompt
----------------------------------------------------
Experiment logs are fetched by a subagent, not by the main agent. The user changes the
fetch cadence from a GUI while that subagent is already running. For that change to take
effect *without restarting anything*, both sides have to agree on one mutable source of
truth on disk:

- the GUI writes the interval here when the user confirms;
- the subagent re-reads this file at the top of every single cycle.

The re-read is the whole design. An in-memory copy, a value captured once when the
subagent started, or a value passed in its original brief would all freeze the cadence at
launch time - the slider would move and nothing would happen, which is the exact failure
the user asked to avoid. So `load()` opens the file on every call and nothing in this
module caches. There is deliberately no module-level "last read" value to go stale.

Storage: ``<home>/log-monitor.json``, where ``<home>`` is ``KAGGLE_AGENT_HOME`` when set,
else ``~/.kaggle-agent``. It sits beside the handoff store and, like it, under the user's
own home directory rather than inside the plugin package - the package must stay
read-only and portable, and this is per-user runtime state.

The file holds configuration only. No credential, no token, no notebook output.

The bounds are not decoration: a floor stops a user from hammering the Kaggle API hard
enough to get rate-limited, and a ceiling stops a "monitor" that effectively never checks
a run that died in the first minute. Both are reported back to the user when a value is
rejected, so a refused write is never silent.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

SCHEMA_VERSION = 1
CONFIG_FILENAME = "log-monitor.json"

# A run that is checked more often than this risks Kaggle API rate limits, which would
# make the monitoring itself the cause of a failure it is meant to detect.
MIN_INTERVAL_SECONDS = 15
# Above this, a run that fails immediately is not noticed until the ceiling itself is the
# bug. 15 minutes is long enough to be cheap and short enough to still be monitoring.
MAX_INTERVAL_SECONDS = 900
# The slider moves in 15s steps, which is fine enough to feel continuous and coarse enough
# that the label stays readable.
STEP_SECONDS = 15
DEFAULT_INTERVAL_SECONDS = 120

VALID_KINDS = ("kaggle", "local")


def _home() -> str:
    return os.environ.get("KAGGLE_AGENT_HOME") or os.path.join(
        os.path.expanduser("~"), ".kaggle-agent"
    )


def config_path() -> str:
    return os.path.join(_home(), CONFIG_FILENAME)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clamp(value: int) -> int:
    return max(MIN_INTERVAL_SECONDS, min(MAX_INTERVAL_SECONDS, int(value)))


def _snap(value: int) -> int:
    """Round to the nearest slider step so a hand-typed value matches what a slider shows."""
    step = STEP_SECONDS
    return int(round(value / step) * step)


def defaults() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "intervalSeconds": DEFAULT_INTERVAL_SECONDS,
        # revision increases on every accepted write. The subagent reports the revision it
        # acted under, so "did my change take effect?" is answerable without guessing.
        "revision": 0,
        "updatedAt": None,
        "targets": [],
        "notify": {
            "onError": True,
            "onTerminal": True,
            "onDecision": True,
        },
    }


def load() -> dict[str, Any]:
    """Read the config from disk on every call. Never cached, by design.

    A missing or corrupt file yields the defaults rather than an error: monitoring should
    still start with a sane cadence when the file is absent, and a half-written file must
    not wedge a run that is already in flight.
    """
    data = defaults()
    try:
        with open(config_path(), "r", encoding="utf-8") as fh:
            stored = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return data
    if not isinstance(stored, dict):
        return data

    if isinstance(stored.get("intervalSeconds"), (int, float)):
        data["intervalSeconds"] = _clamp(stored["intervalSeconds"])
    if isinstance(stored.get("revision"), int):
        data["revision"] = stored["revision"]
    if isinstance(stored.get("updatedAt"), str):
        data["updatedAt"] = stored["updatedAt"]
    if isinstance(stored.get("targets"), list):
        data["targets"] = [t for t in stored["targets"] if isinstance(t, dict)]
    if isinstance(stored.get("notify"), dict):
        for key in ("onError", "onTerminal", "onDecision"):
            if isinstance(stored["notify"].get(key), bool):
                data["notify"][key] = stored["notify"][key]
    return data


def _write(data: dict[str, Any]) -> str:
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    # Replace rather than truncate, so a reader mid-cycle never sees a half-written file.
    os.replace(tmp, path)
    return path


def set_interval(seconds: Any) -> dict[str, Any]:
    """Validate and store a new interval. Returns the full config plus what was applied.

    Out-of-range values are clamped and the caller is told the real applied value, rather
    than rejecting the write and leaving the user with a slider that appears to do nothing.
    """
    try:
        requested = int(float(seconds))
    except (TypeError, ValueError):
        raise ValueError(
            f"interval must be a number of seconds, got {seconds!r}. "
            f"Valid range: {MIN_INTERVAL_SECONDS}-{MAX_INTERVAL_SECONDS}."
        )

    data = load()
    before = data["intervalSeconds"]
    applied = _clamp(_snap(requested))
    changed = applied != before

    data["intervalSeconds"] = applied
    if changed:
        data["revision"] = int(data["revision"]) + 1
        data["updatedAt"] = _now()
    _write(data)

    return {
        "ok": True,
        "requestedSeconds": requested,
        "previousSeconds": before,
        "intervalSeconds": applied,
        "changed": changed,
        "clamped": applied != requested,
        "snapped": applied != _clamp(requested),
        "revision": data["revision"],
        "updatedAt": data["updatedAt"],
        "minSeconds": MIN_INTERVAL_SECONDS,
        "maxSeconds": MAX_INTERVAL_SECONDS,
        "stepSeconds": STEP_SECONDS,
        "path": config_path(),
        "effective": (
            f"fetch interval is now {applied}s (revision {data['revision']}); "
            "a monitoring subagent picks this up on its next cycle without restarting"
            if changed
            else f"interval was already {applied}s; no revision bump"
        ),
    }


def set_target(kind: str, ref: str = "", path: str = "") -> dict[str, Any]:
    """Register something to watch: a Kaggle kernel ref, or a local log file."""
    kind = (kind or "").strip().lower()
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {', '.join(VALID_KINDS)}, got {kind!r}")
    if kind == "kaggle" and not ref.strip():
        raise ValueError("a kaggle target needs a ref, as owner/slug")
    if kind == "local" and not path.strip():
        raise ValueError("a local target needs a path to the log file")

    data = load()
    target = {"kind": kind}
    if kind == "kaggle":
        target["ref"] = ref.strip()
    else:
        target["path"] = path.strip()

    # Replace a target with the same identity rather than accumulating duplicates, so
    # re-registering a run after a re-push does not make the monitor fetch it twice.
    def same(t: dict[str, Any]) -> bool:
        return t.get("kind") == target["kind"] and t.get(
            "ref" if kind == "kaggle" else "path"
        ) == target.get("ref" if kind == "kaggle" else "path")

    data["targets"] = [t for t in data["targets"] if not same(t)] + [target]
    data["updatedAt"] = _now()
    _write(data)
    return {"ok": True, "target": target, "targets": data["targets"], "path": config_path()}


def clear_targets() -> dict[str, Any]:
    data = load()
    removed = len(data["targets"])
    data["targets"] = []
    data["updatedAt"] = _now()
    _write(data)
    return {"ok": True, "removed": removed, "path": config_path()}


def reset() -> dict[str, Any]:
    data = defaults()
    _write(data)
    return {"ok": True, "config": data, "path": config_path()}


def describe() -> dict[str, Any]:
    """A compact, user-facing view for the GUI and for the subagent's first cycle."""
    data = load()
    return {
        "intervalSeconds": data["intervalSeconds"],
        "revision": data["revision"],
        "updatedAt": data["updatedAt"],
        "targets": data["targets"],
        "notify": data["notify"],
        "minSeconds": MIN_INTERVAL_SECONDS,
        "maxSeconds": MAX_INTERVAL_SECONDS,
        "stepSeconds": STEP_SECONDS,
        "path": config_path(),
        "exists": os.path.isfile(config_path()),
    }
