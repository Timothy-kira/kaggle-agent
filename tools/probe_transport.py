"""End-to-end check that the experiment tree is reachable the way the host calls it.

Each case gets its OWN server process. That is not tidiness: before the dispatch guard, one
unhandled exception killed the process and every later case in the same process reported
"Connection closed" - a failure that looks like the transport, not like the code under test.
One process per case is what makes each result attributable.

Cases send the payload the way the host has to: structured data as ONE JSON string.

Usage:  python tools/probe_transport.py
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


def call(tool, args, home):
    """One initialize + one tools/call in a fresh process. Returns (reply, stderr_tail)."""
    env = dict(os.environ)
    env["PLUGIN_ROOT"] = str(ROOT)
    env["KAGGLE_AGENT_HOME"] = str(home)
    proc = subprocess.Popen(
        [sys.executable, "-B", str(SERVER)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env=env, cwd=str(ROOT),
    )
    script = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "probe", "version": "0"}}},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": tool, "arguments": args}},
    ]
    try:
        raw, err = proc.communicate("\n".join(json.dumps(m) for m in script) + "\n",
                                    timeout=90)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return None, "<<timed out>>"
    reply = None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("id") == 1:
            reply = obj
    return reply, (err or "").strip()[-400:]


def brief(reply):
    if reply is None:
        return "SERVER DIED (no reply)"
    if "error" in reply:
        return "RPC-ERROR " + json.dumps(reply["error"])[:120]
    res = reply.get("result") or {}
    text = "\n".join(c.get("text", "") for c in (res.get("content") or [])
                     if isinstance(c, dict))
    flag = "isError" if res.get("isError") else "ok"
    return f"[{flag}] " + " / ".join(l for l in text.splitlines() if l.strip())[:170]


NODE = {
    "id": "e1", "kind": "experiment", "parent": "root",
    "change": "stdio node as one JSON string", "hypothesis": "strings survive the host",
    "metric": {"name": "score", "parent": 0.0, "result": 0.1, "delta": 0.1},
    "operator": "draft", "family": "transport",
    "verdict": "keep", "reason": "probe", "evidence": "local-only", "artifacts": [],
}

CASES = [
    ("record: node as ONE JSON string (the host's only survivable shape)",
     "kaggle_experiment_tree",
     {"action": "record", "competition": "probe-tree", "read_revision": 0,
      "node": json.dumps(NODE)}),
    ("record: node as a REAL object (a plain MCP client may still send this)",
     "kaggle_experiment_tree",
     {"action": "record", "competition": "probe-tree", "read_revision": 0, "node": NODE}),
    ("record: node with broken JSON quotes (must be named, not crash)",
     "kaggle_experiment_tree",
     {"action": "record", "competition": "probe-tree", "read_revision": 0,
      "node": "{'id': 'e1'}"}) ,
    ("record: an EMPTY node string (must say what is missing)",
     "kaggle_experiment_tree",
     {"action": "record", "competition": "probe-tree", "read_revision": 0, "node": ""}),
    ("record: node text carrying a lone surrogate half",
     "kaggle_experiment_tree",
     {"action": "record", "competition": "probe-tree", "read_revision": 0,
      "node": "{\"change\":\"x\udcaey\"}"}),
    ("local launch: command as a JSON array string",
     "kaggle_local_launch",
     {"command": '["python","-c","print(1)"]', "competition": "probe-tree",
      "declares": "nope"}),
    ("local launch: command as plain text",
     "kaggle_local_launch",
     {"command": "python -c print(1)", "competition": "probe-tree", "declares": "nope"}),
]


def main():
    import tempfile
    dead = []
    for i, (label, tool, args) in enumerate(CASES):
        home = _mkdtemp(prefix=f"ka-probe-{i}-")
        reply, err = call(tool, args, home)
        print(f"  {label}\n    -> {brief(reply)}")
        if reply is None:
            # No reply is not a rejected payload: the server never came up. Every case in this
            # suite reported SERVER DIED and the suite still exited 0, which is how a dead entry
            # point reads as a suite that passed.
            dead.append(label)
        if err and "Traceback" in err:
            print(f"    !! server stderr had a traceback: {err[:160]}")
            dead.append(label + " (traceback)")
    print("\neach case ran in its own process, so no crash can bleed into the next.")
    if dead:
        print(f"\nPROBE_TRANSPORT_FAILED ({len(dead)}): a case got no reply at all. That is a dead "
              f"entry point, not a rejected payload, and it must not exit 0.")
        for line in dead:
            print(f"  - {line}")
        return 1
    print("\nPROBE_TRANSPORT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
