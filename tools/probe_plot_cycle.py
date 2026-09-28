"""End-to-end: action='analyze' through the real MCP stdio server, on a real tree.

The in-process shortcut would prove the renderer works. It would not prove an agent can get a
figure: the dispatch loop, the argument scrub, the JSON round trip and the plot store are all
part of the path, and a figure that never reaches disk is not a figure.

So this builds a tree that has earned a band, a frontier and a per-criterion spread - each of
which needs a different field recorded on the node - by going through action='record', and then
asks the server for the figures the way an agent does. Then it asks again with numpy made
unimportable, because "what happens on a machine without matplotlib" is the whole reason step 0
of the skill exists, and it has to be answered on the real path.

Every write is preceded by its own read, because the tool authorises exactly one write per
readRevision. Collecting the revisions first and sending a batch is what an earlier version of
the sibling probe did, and every write after the first came back `stale_read`.

Usage:  python tools/probe_plot_cycle.py
"""

import json
import os
import re
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
COMP = "probe-plots"

INIT = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
    "protocolVersion": "2024-11-05", "capabilities": {},
    "clientInfo": {"name": "probe", "version": "0"}}}

CTL = {"seed": 1, "budget": "1h", "eval": "holdout", "retrain": "re-eval"}


def node(nid, parent, factors, result, delta, *, samples=None, **extra):
    # `samples` goes INSIDE the metric. An earlier version of this probe passed it as a stray
    # top-level key and then popped it out of the metric, so it was never sent - and the band
    # figure silently did not appear, which looked exactly like the tool dropping the field.
    metric = {"name": "score", "parent": result - delta, "result": result,
              "delta": delta, "rank": 1, "rankSource": "local"}
    if samples:
        metric["samples"] = samples
    n = {
        "id": nid, "kind": "experiment", "parent": parent,
        "change": f"turn on {factors[0]}" if factors else "bare model, nothing switched on",
        "hypothesis": f"{factors[0] if factors else 'the bare model'} is worth its cost",
        "metric": metric,
        "verdict": "keep", "reason": "measured, with the spread recorded alongside it",
        "operator": "improve", "family": "ablation", "artifacts": ["probe/plot-cycle"],
        "evidence": "local-only", "factors": factors, "controls": dict(CTL),
    }
    n.update(extra)
    return n


NODES = [
    node("n1", None, [], 0.50, 0.00,
         samples={"mean": 0.50, "std": 0.02, "n": 3},
         cost={"quotaHours": 0.4, "wallSeconds": 900, "agentCalls": 12},
         criteria=[{"name": "accuracy", "value": 0.61, "std": 0.01, "direction": "higher"}]),
    node("n2", "n1", ["cache"], 0.57, 0.07,
         samples={"mean": 0.57, "std": 0.05, "n": 5},
         cost={"quotaHours": 1.9, "wallSeconds": 2400, "agentCalls": 31},
         criteria=[{"name": "accuracy", "value": 0.68, "std": 0.02, "direction": "higher"},
                   {"name": "latency_ms", "value": 210, "std": 25, "direction": "lower"}]),
    # n3 keeps n2's factor and adds one. Listing only ["budget"] here was rejected by the tree's
    # own gate - it differs from its parent in two factors, so it measured a combination - which
    # is the gate doing its job, not the probe being wrong about the API.
    node("n3", "n2", ["cache", "budget"], 0.61, 0.04,
         change="add budget on top of cache",
         samples={"mean": 0.61, "std": 0.01, "n": 4},
         cost={"quotaHours": 4.1, "wallSeconds": 5200, "agentCalls": 58},
         criteria=[{"name": "accuracy", "value": 0.72, "std": 0.01, "direction": "higher"}]),
]

SHIM = """import sys


class _Block:
    def find_spec(self, name, path=None, target=None):
        if name in ('numpy', 'matplotlib'):
            raise ImportError("No module named %r" % name)
        return None


sys.meta_path.insert(0, _Block())
"""


def call(args_list, home, block_backend=False):
    env = dict(os.environ)
    env["PLUGIN_ROOT"] = str(ROOT)
    env["KAGGLE_AGENT_HOME"] = str(home)
    if block_backend:
        shim = Path(home) / "shim"
        shim.mkdir(parents=True, exist_ok=True)
        (shim / "sitecustomize.py").write_text(SHIM, encoding="utf-8")
        env["PYTHONPATH"] = str(shim)
    proc = subprocess.Popen(
        [sys.executable, "-B", str(SERVER)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env=env, cwd=str(ROOT))
    script = [INIT]
    for i, args in enumerate(args_list, start=1):
        script.append({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                       "params": {"name": "kaggle_experiment_tree", "arguments": args}})
    try:
        raw, err = proc.communicate("\n".join(json.dumps(m) for m in script) + "\n",
                                    timeout=300)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return [{"text": "<<timed out>>", "isError": True}] * len(args_list), "timeout"
    by_id = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if isinstance(msg, dict) and isinstance(msg.get("id"), int):
            by_id[msg["id"]] = msg
    out = []
    for i in range(1, len(args_list) + 1):
        res = by_id.get(i) or {}
        payload = (res.get("result") or {}).get("content") or [{}]
        out.append({"text": payload[0].get("text", ""),
                    "isError": bool((res.get("result") or {}).get("isError"))})
    return out, err


def revision(home, block_backend=False):
    out, _ = call([{"competition": COMP, "action": "read"}], home, block_backend)
    m = re.search(r"^revision:\s*(\d+)", out[0]["text"], re.M)
    if m is None:
        raise SystemExit(f"could not read a revision out of the answer:\n{out[0]['text'][:400]}")
    return int(m.group(1))


def seed(home, block_backend=False):
    for n in NODES:
        r = revision(home, block_backend)
        out, _ = call([{"competition": COMP, "action": "record", "read_revision": r,
                        "node": json.dumps(n, ensure_ascii=False)}], home, block_backend)
        if out[0]["isError"] or "recorded" not in out[0]["text"].lower():
            raise SystemExit(f"seeding {n['id']} failed:\n{out[0]['text'][:500]}")


def main():
    failures = 0

    def ok(cond, label, detail=""):
        nonlocal failures
        if not cond:
            failures += 1
        print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"\n         {detail}" if detail else ""))

    print("backend present")
    home = Path(_mkdtemp(prefix="ka-plotprobe-"))
    seed(home)
    out, err = call([{"competition": COMP, "action": "analyze"}], home)
    text = out[0]["text"]
    ok(not out[0]["isError"], "the server answered action='analyze'", text[:200] if not text else "")
    plots = sorted((home / "plots").glob("*")) if (home / "plots").exists() else []
    kinds = sorted(p.stem.rsplit("-", 1)[-1] for p in plots if p.is_file())
    ok(len(plots) >= 3, f"figures landed on disk: {kinds}", text[:200] if not plots else "")
    ok(all(p.stat().st_size > 2000 for p in plots if p.is_file()),
       "every figure is a real file, not an empty placeholder")
    if plots:
        head = next(p for p in plots if p.is_file()).read_text(encoding="utf-8")[:200]
        ok("<svg" in head, "the first figure is vector SVG", head[:60].replace("\n", " "))
    ok("frontier" in text or "skipped" in text,
       "the answer names the figures and the ones it could not draw", text[:160])

    print("backend absent")
    home2 = Path(_mkdtemp(prefix="ka-plotprobe-nb-"))
    seed(home2, block_backend=True)
    out2, _ = call([{"competition": COMP, "action": "analyze"}], home2, block_backend=True)
    text2 = out2[0]["text"]
    ok("Traceback" not in text2, "no traceback reached the caller",
       text2[:200].replace("\n", " "))
    ok("matplotlib" in text2 or "numpy" in text2,
       "the answer names the missing backend", text2[:200].replace("\n", " "))
    ok("kaggle_sources" in text2 and "install" in text2,
       "the answer carries the command that installs it", text2[:240].replace("\n", " "))
    made = list((home2 / "plots").glob("*")) if (home2 / "plots").exists() else []
    ok(not [p for p in made if p.is_file() and p.stat().st_size > 0],
       "no half-drawn figure was written without the backend", str(made))

    print("\n" + ("all plot probes passed" if not failures else f"{failures} probe(s) FAILED"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
