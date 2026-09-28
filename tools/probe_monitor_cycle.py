"""End-to-end: a full monitoring cycle through the real MCP stdio server.

The in-process `tool_call` shortcut skips the dispatch loop, the JSON round trip and the
argument scrub - which is exactly where the last two bugs lived (a parameter named `action`
colliding with the top-level `action`, and a KeyError that only `describe()` triggers).
So the cycle is driven the way the cron tick actually drives it: JSON in, text out.

Each request is its own process, so a crash cannot bleed into the next case.

Usage:  python tools/probe_monitor_cycle.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


import atexit as _atexit
import shutil as _shutil

_OWN_TEMP_DIRS: list[str] = []


def _mkdtemp(*args, **kwargs) -> str:
    """A temp directory this process is responsible for removing.

    Thirty-odd suites across these files each made a throwaway home per run and never removed
    it, so a few hundred ka-* folders had piled up in the user's temp directory. That residue
    reads as if the WORK left it behind when the tests did, and a suite that passes every
    assertion can still leave a mess behind - which is exactly the kind of failure that never
    shows up as a failure. Tracking each directory and removing it at exit is the whole fix,
    and it only ever touches what this process created, so a suite running beside another one
    cannot delete its neighbour's home.
    """
    d = tempfile.mkdtemp(*args, **kwargs)
    _OWN_TEMP_DIRS.append(d)
    return d


_atexit.register(lambda: [_shutil.rmtree(d, ignore_errors=True) for d in _OWN_TEMP_DIRS])

# The launchers start the server with `-B` so the package directory stays free of
# __pycache__. A developer running this file by hand would put it straight back, and
# the directory is read-only by contract - so the tool holds the same line the
# launcher does. Set before any mcp/ module is imported below, which is what makes it
# effective rather than decorative.
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "mcp" / "agent_server.py"

INIT = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
    "protocolVersion": "2024-11-05", "capabilities": {},
    "clientInfo": {"name": "probe", "version": "0"}}}


def call_sequence(tool, arg_list, home):
    """Run one process that performs several tools/call in order (a whole tick cycle)."""
    env = dict(os.environ)
    env["PLUGIN_ROOT"] = str(ROOT)
    env["KAGGLE_AGENT_HOME"] = str(home)
    proc = subprocess.Popen(
        [sys.executable, "-B", str(SERVER)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env=env, cwd=str(ROOT),
    )
    script = [INIT]
    for i, args in enumerate(arg_list, start=1):
        script.append({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                       "params": {"name": tool, "arguments": args}})
    try:
        raw, err = proc.communicate("\n".join(json.dumps(m) for m in script) + "\n",
                                    timeout=120)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return [None] * len(arg_list), "<<timed out>>"
    by_id = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("id"), int):
            by_id[obj["id"]] = obj
    return [by_id.get(i) for i in range(1, len(arg_list) + 1)], (err or "")[-600:]


def text_of(reply):
    if reply is None:
        return "SERVER DIED (no reply)"
    if "error" in reply:
        return "RPC-ERROR " + json.dumps(reply["error"])[:140]
    res = reply.get("result") or {}
    return "\n".join(c.get("text", "") for c in (res.get("content") or [])
                     if isinstance(c, dict))


def main():
    home = _mkdtemp(prefix="ka-probe-cycle-")
    M = "kaggle_log_monitor"
    # One process, one whole cycle: register, get, observe, tick. Then a second process for
    # the repair path, because the first is a healthy run and the second is not.
    cycle = [
        {"action": "target", "kind": "local", "path": str(Path(home) / "run.log")},
        {"action": "get"},
        {"action": "observe", "text": "epoch 1/100 val_loss 0.9\nepoch 2/100 val_loss 0.8"},
        {"action": "tick", "verdict": "tighten"},
        {"action": "observe", "text": "epoch 2/100 val_loss 0.8"},
        {"action": "tick", "verdict": "hold"},
        {"action": "observe", "text": "epoch 2/100 val_loss 0.8"},
        {"action": "tick", "verdict": "relax"},
        {"action": "observe", "text": "Traceback (most recent call last):\n"
                                       "  File \"train.py\", line 88\nValueError: bad shape"},
    ]
    replies, err = call_sequence(M, cycle, home)
    labels = ["target", "get", "observe (moved)", "tick tighten", "observe (quiet 1)",
              "tick hold", "observe (quiet 2)", "tick relax", "observe (error)"]
    for label, rep in zip(labels, replies):
        print(f"--- {label} ---")
        print(text_of(rep)[:340].rstrip(), "\n")
    if "Traceback" in (err or ""):
        print("!! server stderr traceback:", err[:300])

    # Second process: the repair path, and the route that works being remembered.
    repair = [
        {"action": "attempt", "ok": False, "reason": "file not found", "kind": "local"},
        {"action": "attempt", "ok": False, "reason": "still missing", "kind": "local"},
        {"action": "attempt", "ok": True, "recipe": "nearest", "kind": "local"},
        {"action": "get"},
    ]
    replies2, err2 = call_sequence(M, repair, home)
    print("=== repair path (a second process) ===")
    for label, rep in zip(["fail 1", "fail 2", "worked", "get"], replies2):
        print(f"--- {label} ---")
        print(text_of(rep)[:260].rstrip(), "\n")
    if "Traceback" in (err2 or ""):
        print("!! server stderr traceback:", err2[:300])


if __name__ == "__main__":
    main()
