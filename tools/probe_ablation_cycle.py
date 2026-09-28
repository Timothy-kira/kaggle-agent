"""End-to-end: an ablation run through the real MCP stdio server.

The in-process `tool_call` shortcut skips the dispatch loop, the JSON round trip and the
argument scrub - which is exactly where the last bugs lived (a parameter named `action`
colliding with the top-level `action`, a KeyError only describe() triggers). So the whole
ablation path is driven the way an agent drives it: JSON in, text out, through action=declare,
action=settle and action=ablate.

Each request is its own process, so a crash cannot bleed into the next case.

Usage:  python tools/probe_ablation_cycle.py
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
COMP = "probe-ablate"

INIT = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
    "protocolVersion": "2024-11-05", "capabilities": {},
    "clientInfo": {"name": "probe", "version": "0"}}}

CTL = {"seed": 1, "budget": "1h", "eval": "holdout", "retrain": "re-eval"}


def call_sequence(tool, arg_list, home):
    """Run one process that performs several tools/call in order."""
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
    out = []
    for i in range(1, len(arg_list) + 1):
        res = by_id.get(i) or {}
        payload = (res.get("result") or {}).get("content") or [{}]
        out.append({"error": (res.get("error") or {}).get("message"),
                    "text": payload[0].get("text", ""),
                    "isError": (res.get("result") or {}).get("isError", False)})
    return out, err


def node(nid, parent, factors, result, delta, **extra):
    n = {
        "id": nid, "kind": "experiment", "parent": parent,
        "change": f"run {nid}", "hypothesis": "h",
        "metric": {"name": "acc", "parent": result - delta, "result": result,
                   "delta": delta, "rank": 1, "rankSource": "local"},
        "verdict": "keep", "reason": "measured", "operator": "improve",
        "family": "ablation", "artifacts": ["run"], "evidence": "local-only",
        "factors": factors, "controls": dict(CTL),
    }
    n.update(extra)
    return n


def rev(home, args):
    """declare/settle/record need the readRevision that authorises exactly one write."""
    r, _ = call_sequence("kaggle_experiment_tree", [dict(args, competition=COMP,
                                                         action="read")], home)
    import re
    m = re.search(r"^revision:\s*(\d+)", r[0]["text"], re.M)
    return int(m.group(1)) if m else None


def write(home, args):
    """One write, preceded by the read that authorises it - the order an agent uses.

    The revision has to be fetched per write. Collecting them all first and sending the
    batch is what an earlier version of this probe did, and every write after the first came
    back `stale_read`: the tree had moved on exactly as the tool said it would.
    """
    r = rev(home, {"action": "read"})
    out, _ = call_sequence("kaggle_experiment_tree",
                           [dict(args, competition=COMP, read_revision=r)], home)
    return out[0]


CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


@case("bare -> +a -> +a+b, declared and settled through the real server")
def _(home):
    done = [("record", write(home, {"action": "record",
                                    "node": json.dumps(node("n0", None, [], 0.50, 0.0))}))]
    prev, prev_score = "n0", 0.50
    for nid, factors, result in (("n1", ["a"], 0.60), ("n2", ["a", "b"], 0.75)):
        d = {"id": nid, "kind": "experiment", "parent": prev,
             "change": f"add what reaches {nid}", "hypothesis": "this helps",
             "reason": "worth a run", "operator": "improve", "family": "ablation",
             "factors": factors, "controls": dict(CTL),
             "diagnosis": "none", "diagnosisReason": "building the ladder",
             "expect": {"direction": "up", "atLeast": 0.02}}
        done.append(("declare", write(home, {"action": "declare", "node": json.dumps(d)})))
        s = dict(node(f"{nid}r", nid, factors, result, result - prev_score))
        for gone in ("expect", "diagnosis", "diagnosisReason"):
            s.pop(gone, None)
        done.append(("settle", write(home, {"action": "settle", "node": json.dumps(s),
                                            "declared": nid})))
        prev, prev_score = f"{nid}r", result
    out, _ = call_sequence("kaggle_experiment_tree",
                           [{"action": "ablate", "competition": COMP}], home)
    fails = [f"{label} -> {(o.get('error') or o['text'][:200])}"
             for label, o in done if o.get("error") or o.get("isError")]
    if fails:
        return False, "\n      ".join(fails)
    table = out[0]["text"]
    checks = [
        ("baseline: n0" in table, "the bare arm is the baseline"),
        ("(bare)" in table, "the bare arm is labelled, not blank"),
        (table.count(" add ") >= 2, "both ladder steps are edges"),
        ("interactions" not in table,
         "no interaction is reported from a pair with no standalone arm"),
        ("in company" in table, "b is reported as measured only alongside a"),
        ("full factorial" in table, "the factorial size is stated"),
    ]
    bad = [why for cond, why in checks if not cond]
    return (not bad), "\n      ".join(bad) + (f"\n{table}" if bad else "")


@case("a two-factor change is refused through the real write path")
def _(home):
    write(home, {"action": "record",
                 "node": json.dumps(node("n0", None, ["a"], 0.50, 0.0))})
    d = {"id": "x1", "kind": "experiment", "parent": "n0", "change": "two things at once",
         "hypothesis": "h", "reason": "r", "operator": "improve", "family": "ablation",
         "factors": ["a", "b", "c"], "controls": dict(CTL),
         "diagnosis": "none", "diagnosisReason": "x",
         "expect": {"direction": "up", "atLeast": 0.02}}
    o = write(home, {"action": "declare", "node": json.dumps(d)})
    if o.get("error"):
        return False, f"protocol error {o['error']}"
    text = o.get("text", "")
    return ("2 factors" in text), f"expected the arithmetic in the refusal, got: {text[:300]}"


@case("ablate on a tree with no factor sets explains how to start one")
def _(home):
    d = {"id": "y0", "kind": "experiment", "parent": None, "change": "first run",
         "hypothesis": "h",
         "metric": {"name": "acc", "parent": 0.0, "result": 0.5, "delta": 0.5,
                    "rank": 1, "rankSource": "local"},
         "verdict": "keep", "reason": "baseline", "operator": "draft", "family": "ablation",
         "artifacts": ["run"], "evidence": "local-only"}
    r = write(home, {"action": "record", "node": json.dumps(d)})
    if r.get("isError") or r.get("error"):
        return False, f"record refused: {(r.get('error') or r['text'])[:250]}"
    out, _ = call_sequence("kaggle_experiment_tree",
                           [{"action": "ablate", "competition": COMP}], home)
    return ("factors" in out[0]["text"]), f"got: {out[0]['text'][:250]}"


def main():
    failures = 0
    for name, fn in CASES:
        home = _mkdtemp(prefix="ka-probe-ablate-")
        try:
            ok, detail = fn(home)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"raised {type(exc).__name__}: {exc}"
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
        if not ok:
            failures += 1
            print(f"      {detail}")
    print(f"\n{'all probes passed' if not failures else f'{failures} probe(s) failed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
