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

import hashlib
import json
import os
import re
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


# The watch ladder, in seconds: close in while a run is starting and deciding whether
# it works, then stretch out while it is plainly just running. The last rung is the
# steady state, and it is also the cheapest.
LADDER_SECONDS = (60, 180, 300, 600, 1200)
LADDER_LABELS = ("1m", "3m", "5m", "10m", "20m")


def ladder_state(data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Where the watch currently sits on the ladder, and where it goes next."""
    d = data if data is not None else load()
    rung = d.get("rung")
    if not isinstance(rung, int) or rung < 0:
        rung = 0
    rung = min(rung, len(LADDER_SECONDS) - 1)
    return {
        "rung": rung,
        "of": len(LADDER_SECONDS),
        "labels": list(LADDER_LABELS),
        "currentSeconds": LADDER_SECONDS[rung],
        "currentLabel": LADDER_LABELS[rung],
        "nextSeconds": LADDER_SECONDS[min(rung + 1, len(LADDER_SECONDS) - 1)],
        "nextLabel": LADDER_LABELS[min(rung + 1, len(LADDER_SECONDS) - 1)],
        "atSteadyState": rung >= len(LADDER_SECONDS) - 1,
        "ticks": d.get("ticks") if isinstance(d.get("ticks"), int) else 0,
    }


def note_tick(data: dict[str, Any] | None = None, action: str = "") -> dict[str, Any]:
    """Record that a check happened, and report the rung to re-arm with.

    ``action`` is what :func:`observe` concluded - "tighten", "relax" or "hold". Without it
    this is pure arithmetic that drifts on its own; with it, a run that just printed something
    goes back to being watched closely instead of sliding towards the 20-minute steady state
    while it is in the middle of doing the interesting part.
    """
    d = load() if data is None else data
    before = ladder_state(d)
    if action == "tighten":
        rung = 0
    elif action == "relax":
        rung = min(before["rung"] + 1, len(LADDER_SECONDS) - 1)
    else:
        rung = before["rung"]
    d["rung"] = rung
    d["ticks"] = before["ticks"] + 1
    d["updatedAt"] = _now()
    d["revision"] = int(d.get("revision") or 0) + 1
    _write(d)
    # The re-arm interval is the rung just reached, not the one after it. The watch starts at
    # rung 0 (1m) because that is the cadence the FIRST check should use; advancing first and
    # then reporting the next rung along skips 3m entirely.
    return {
        "rung": rung,
        "rearmWithSeconds": LADDER_SECONDS[rung],
        "rearmWithLabel": LADDER_LABELS[rung],
        "atSteadyState": rung >= len(LADDER_SECONDS) - 1,
        "ticks": d["ticks"],
        "action": action or "hold",
        "why": ("the log moved, so it is being watched closely again" if action == "tighten"
                else "the log has been quiet, so the checks can stretch out"
                if action == "relax" else
                "the log has not settled yet, so the cadence holds"),
    }


def _clear_ladder(d: dict[str, Any]) -> None:
    """Drop every field that describes the previous run, in place.

    One implementation, one caller. This replaced two: a two-line `reset_ladder` that
    cleared only `rung` and `ticks` beside a wider inlined copy in set_target, so reaching
    for the callable gave a tight cadence over a stale repair history - the opposite of what
    a fresh watch wants. A digest of an unrelated log and a relax streak earned while
    something else was being watched both belong to the run that is over.
    """
    d["rung"] = 0
    d["ticks"] = 0
    d["attempts"] = 0
    d["tried"] = []
    d["still"] = 0
    d["digest"] = None


# ------------------------------------------------------------------ watching content, not a clock
# The ladder alone is a clock: it knows how long it has been since the last tick and nothing
# else. It cannot tell "the run has printed nothing for an hour" from "the run died in the
# first minute", because the difference is in the log and the clock never reads the log.
#
# So the tick body feeds what it read back in here, and the content decides two things: whether
# this tick is worth a message, and how soon the next one comes. A run that just printed
# something is interesting again and the ladder snaps back to its tightest rung; a run that
# printed nothing new twice running is genuinely steady and the ladder is allowed to move on.
#
# The rules are DATA in the config, not code, so a user can watch for their own field without
# a plugin update - and so a wrong rule is editable rather than baked in.
WATCH_RULES: tuple[dict[str, Any], ...] = (
    {"name": "error", "act": "report", "pattern":
     r"Traceback \(most recent call last\)|\bError\b|\bException\b|"
     r"\bFAILED\b|exit code [1-9]|\bCUDA out of memory\b"},
    {"name": "terminal", "act": "report", "pattern":
     r"Run complete|Cell finished|Kernel (?:exited|shutdown|disconnected)|"
     r"\bCOMPLETE\b|\bSUCCESS\b|\bKAGGLE_KERNEL_(?:COMPLETE|ERROR)\b"},
    {"name": "decision", "act": "report", "pattern":
     r"\?\s*$|Do you want to (?:overwrite|proceed)|waiting for (?:input|user)|"
     r"\[y/n\]|press any key"},
    {"name": "activity", "act": "wake", "pattern":
     r"\bepoch\b|\bstep\s+\d|\bit/s\b|\d+/\d+|\bSaving\b|\bcheckpoint\b|"
     r"\bval_(?:loss|acc|auc)\b|\btrain\b.*\d"},
)

# A log that has not moved for this many consecutive checks is steady, not interesting.
# Two, not one: a run can legitimately go quiet for a while mid-training, and treating the
# first silent tick as "steady" is how a real change gets slept through.
STILL_CHECKS_TO_STEADY = 2

# How a fetch is retried when the log cannot be read. Ordered, bounded, and explicit: the
# agent is told which one to try next rather than inventing its own order each tick.
FETCH_RECIPES: dict[str, tuple[dict[str, str], ...]] = {
    "kaggle": (
        {"step": "logs", "tool": "kaggle_kernels_logs", "hint": "fetch the run's log"},
        {"step": "status", "tool": "kaggle_kernels_status",
         "hint": "check the kernel state first - a queued or starting kernel has no log yet"},
        {"step": "output", "tool": "kaggle_kernels_output",
         "hint": "outputs sometimes land before the log endpoint answers"},
        {"step": "ref", "tool": "handoff_status",
         "hint": "confirm the ref is still the one this run was launched under"},
    ),
    "local": (
        {"step": "read", "tool": "read", "hint": "read the log file"},
        {"step": "nearest", "tool": "glob",
         "hint": "the file was rotated or renamed - find the newest file beside it"},
        {"step": "dir", "tool": "glob",
         "hint": "list the run directory and take the newest log in it"},
    ),
}
# Every route has been tried once. There is no route after the last one, so a failure on the
# final recipe is the end - not another attempt at the same step.
MAX_ATTEMPTS = len(FETCH_RECIPES["kaggle"])


def _tail(text: str, limit: int = 8000) -> str:
    """Watch the end of a log. The interesting line is almost never the first one."""
    return text[-limit:] if len(text) > limit else text


# A bare traceback header names nothing. The exception is on the NEXT line, which is the same
# trap the tree's log diagnosis already documents: reporting the header is reporting "some
# layer broke" for every run. So when the match is a traceback header, the reported line is
# the first following line that names a cause.
_CAUSE = re.compile(r"^[\w.]*(?:Error|Exception|Exit|Interrupt)\b|^\w+(?:Error|Exception):")


def _report_line(body: str, start: int) -> str:
    """The most informative line at or after a match, not merely the line that matched."""
    rest = body[start:]
    lines = rest.splitlines() or [rest]
    first = lines[0].strip()
    if "Traceback (most recent call last)" in first and len(lines) > 1:
        for candidate in lines[1:]:
            text = candidate.strip()
            if text and not text.startswith(("File ", "^", " ")) and _CAUSE.match(text):
                return f"{first} -> {text}"[:200]
    return first[:200]


def observe(text: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read what the tick just fetched, and decide what it means.

    Returns which rules fired, whether the log moved at all since the last tick, and what
    the ladder should do next. The content, not the clock, is the input.
    """
    d = data if data is not None else load()
    rules = d.get("watch") or [dict(r) for r in WATCH_RULES]
    body = _tail(text or "")

    fired: list[dict[str, Any]] = []
    for rule in rules:
        pattern = str(rule.get("pattern") or "")
        if not pattern:
            continue
        try:
            hit = re.search(pattern, body, re.IGNORECASE | re.MULTILINE)
        except re.error:
            continue
        if hit:
            fired.append({"name": rule.get("name") or "?", "act": rule.get("act") or "report",
                          "line": _report_line(body, hit.start())})

    reporting = [f for f in fired if f["act"] == "report"]
    woke = [f for f in fired if f["act"] == "wake"]

    # "Did the log move" is the question a clock cannot answer. A short hash of the tail is
    # enough: two identical tails mean the run printed nothing new, whatever it is doing.
    digest = hashlib.sha256(body.encode("utf-8", "replace")).hexdigest()[:16]
    before = d.get("digest")
    changed = before != digest
    still = 0 if changed else int(d.get("still") or 0) + 1

    # The cadence is driven by ONE thing: did the log move. A run that printed something new
    # is doing something worth watching closely; a run that printed the same bytes twice
    # running is steady, and stretching out is not neglect, it is the whole point of the
    # ladder. Watch rules decide whether to REPORT, never how fast to look - a heartbeat line
    # that re-matches on an unchanged log is the same text read twice, and letting it
    # re-tighten the ladder every tick is the timer-by-another-name this replaces.
    if changed:
        action = "tighten"
    elif still >= STILL_CHECKS_TO_STEADY:
        action = "relax"
    else:
        action = "hold"

    d["digest"] = digest
    d["still"] = still
    d["lastSeen"] = _now()
    if fired:
        d["fired"] = [f["name"] for f in fired]
    _write(d)

    return {
        "ok": True,
        "fired": fired,
        "report": [f for f in reporting],
        "action": action,
        "changed": changed,
        "stillChecks": still,
        "steady": still >= STILL_CHECKS_TO_STEADY,
        "rung": d.get("rung", 0),
        "ticks": d.get("ticks", 0),
        "rules": [r.get("name") for r in rules],
    }


def attempt(reason: str = "", ok: bool = False, recipe: str = "",
            kind: str = "") -> dict[str, Any]:
    """Record one fetch, and hand back the next thing to try when it failed.

    An unreadable log is not a reason to stop watching. It is a reason to try a different
    route to the same bytes, and to remember which route worked so the next run starts
    there instead of rediscovering it.
    """
    d = load()
    table = FETCH_RECIPES.get(kind) or FETCH_RECIPES["kaggle"]
    used = [dict(x) for x in (d.get("tried") or [])]
    n = int(d.get("attempts") or 0)

    if ok:
        worked = recipe or (used[-1]["step"] if used else table[0]["step"])
        d["lastRecipe"] = {"kind": kind or "kaggle", "step": worked, "recipe": recipe,
                           "at": _now()}
        d["attempts"] = 0
        d["tried"] = []
        d["updatedAt"] = _now()
        d["revision"] = int(d.get("revision") or 0) + 1
        _write(d)
        return {"ok": True, "recovered": n > 0, "attempts": n, "lastRecipe": d["lastRecipe"],
                "effective": (
                    f"'{worked}' works for this target; record it on the node so the next "
                    f"run starts from it instead of rediscovering it" if n else
                    f"'{worked}' works")}

    n += 1
    step = table[min(n, len(table) - 1)]
    used.append({"step": step["step"], "reason": reason[:200]})
    d["attempts"] = n
    d["tried"] = used
    d["lastFailure"] = {"reason": reason[:200], "at": _now()}
    d["updatedAt"] = _now()
    d["revision"] = int(d.get("revision") or 0) + 1
    _write(d)

    exhausted = n >= MAX_ATTEMPTS
    return {
        "ok": True,
        "recovered": False,
        "attempts": n,
        "maxAttempts": MAX_ATTEMPTS,
        "exhausted": exhausted,
        "next": None if exhausted else step,
        "tried": [t["step"] for t in used],
        "effective": (
            "every known route to this log has failed. Say so plainly, delete the cron, and "
            "leave the reason on the node - a monitor that cannot reach its log is not "
            "monitoring anything" if exhausted else
            f"the log was unreadable ({reason[:80]}). Try '{step['step']}' next: "
            f"{step['hint']} - and if it works, record that step on the node"),
    }


def defaults() -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "intervalSeconds": DEFAULT_INTERVAL_SECONDS,
        # revision increases on every accepted write. The subagent reports the revision it
        # acted under, so "did my change take effect?" is answerable without guessing.
        "revision": 0,
        "updatedAt": None,
        "rung": 0,
        "ticks": 0,
        "targets": [],
        # What the log has to look like for this tick to matter. Stored, not hard-coded, so a
        # user can watch for their own field without a plugin update.
        "watch": [dict(r) for r in WATCH_RULES],
        # The fetch that worked, and the routes already tried and failed. An unreadable log
        # is a reason to try a different route, not a reason to stop watching.
        "attempts": 0,
        "tried": [],
        "lastRecipe": None,
        "lastFailure": None,
        "digest": None,
        "still": 0,
        "fired": [],
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
    if isinstance(stored.get("rung"), int) and stored["rung"] >= 0:
        data["rung"] = min(stored["rung"], len(LADDER_SECONDS) - 1)
    if isinstance(stored.get("ticks"), int) and stored["ticks"] >= 0:
        data["ticks"] = stored["ticks"]
    if isinstance(stored.get("watch"), list):
        rules = [r for r in stored["watch"] if isinstance(r, dict) and r.get("pattern")]
        # An empty list is a deliberate "watch nothing in particular", so it is kept. A list
        # that is present but unusable falls back to the defaults rather than monitoring
        # nothing and reporting a clean run.
        data["watch"] = rules or [dict(r) for r in WATCH_RULES]
    for key in ("tried", "fired"):
        if isinstance(stored.get(key), list):
            data[key] = stored[key]
    for key in ("attempts", "still"):
        if isinstance(stored.get(key), int) and stored[key] >= 0:
            data[key] = stored[key]
    for key in ("lastRecipe", "lastFailure", "lastSeen"):
        if isinstance(stored.get(key), dict):
            data[key] = stored[key]
    if isinstance(stored.get("digest"), str):
        data["digest"] = stored["digest"]
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
    # A fresh run is the moment worth watching closely, so a new target restarts the
    # ladder at its first rung rather than inheriting the last one's slack. It also starts
    # with no repair history: the previous run's failed fetch routes say nothing about this
    # one. `lastRecipe` deliberately survives - knowing which fetch route worked last time
    # is exactly what should carry over.
    _clear_ladder(data)
    data["updatedAt"] = _now()
    _write(data)
    return {"ok": True, "target": target, "targets": data["targets"], "path": config_path()}


def clear_targets() -> dict[str, Any]:
    data = load()
    removed = len(data["targets"])
    data["targets"] = []
    data["updatedAt"] = _now()
    _write(data)
    return {"ok": True, "removed": removed, "path": config_path(),
            "ladder": ladder_state(data)}


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
        "ladder": ladder_state(data),
        # What this tick should watch FOR, and where a failed fetch has got to. Without these
        # two the subagent can only re-derive them from prose, every tick, slightly differently.
        "watch": [{"name": r.get("name"), "act": r.get("act"),
                   "pattern": r.get("pattern")} for r in data["watch"]],
        "attempts": data["attempts"],
        "maxAttempts": MAX_ATTEMPTS,
        "tried": data["tried"],
        "lastRecipe": data["lastRecipe"],
        "lastFailure": data["lastFailure"],
        "stillChecks": data["still"],
        "recipes": {k: [s["step"] for s in v] for k, v in FETCH_RECIPES.items()},
        "minSeconds": MIN_INTERVAL_SECONDS,
        "maxSeconds": MAX_INTERVAL_SECONDS,
        "stepSeconds": STEP_SECONDS,
        "path": config_path(),
        "exists": os.path.isfile(config_path()),
    }
