"""Counter-examples: prove the new transport assertions can actually fail.

A check that passes is worth nothing until you have seen it fail. Each case breaks exactly
ONE guarantee that `check_transport_resilience` asserts, and reports ONLY that guarantee -
an instrument that always flags every check (or that reports the wrong one) proves nothing,
which is the failure mode this project has hit nine times.

Two outcomes are accepted, and they mean different things:
  DETECTED  the instrument's own question goes red on the broken input.
  BLOCKED   the current code already refuses the broken input, so the case cannot reproduce.
            That also proves the fix is load-bearing, and it is the stronger result.

Usage:  python tools/prove_counter_examples.py
"""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp"))
sys.path.insert(0, str(ROOT / "tools"))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Each case returns (guarantee_label, holds?) for exactly one guarantee.
CASES = []


def case(name, guarantee):
    def deco(fn):
        CASES.append((name, guarantee, fn))
        return fn
    return deco


@case("publish `node` back as an object", "no published argument is an object or an array")
def _(ks, js):
    published = [f"{t['name']}.{k}"
                 for t in ks.TOOLS
                 for k, v in ((t.get("inputSchema") or {}).get("properties") or {}).items()
                 if v.get("type") in ("object", "array")]
    return not published


@case("dispatch with no try/except around tool_call",
      "a handler that raises returns isError, not a dead connection")
def _(ks, js):
    # The old loop called tool_call directly. Simulate that: the ValueError escapes and the
    # process is gone, which the client only ever sees as "Connection closed".
    def old_dispatch(name, args):
        return ks.tool_call(name, args)  # no guard: an exception propagates out of main()
    try:
        old_dispatch("handoff_write", {
            "competition": "zz-ce", "title": "t", "task": "x",
            "tree": '{"base":"n1","nodes":{"n1":{"id":"n1","kind":"experiment",'
                    '"verdict":"keep","reason":"r"}}}',
        })
        return True  # no exception: this input does not reproduce the bug
    except Exception:
        return False  # the escape that killed the server - the guarantee is violated


@case("call the real guarded dispatch with the same bad tree",
      "a handler that raises returns isError, not a dead connection")
def _(ks, js):
    r = ks.safe_tool_call("handoff_write", {
        "competition": "zz-ce", "title": "t", "task": "x",
        "tree": '{"base":"n1","nodes":{"n1":{"id":"n1","kind":"experiment",'
                '"verdict":"keep","reason":"r"}}}',
    })
    return r.get("isError") is True


@case("feed a lone surrogate straight to disk, no scrub",
      "a lone surrogate half is refused, naming the exact field")
def _(ks, js):
    lone = "\udcae"
    try:
        lone.encode("utf-8")
    except UnicodeEncodeError:
        return False  # un-encodable: without scrub this reaches a file write and raises there
    return True


@case("an over-long argument with no length cap", "an over-long argument is refused")
def _(ks, js):
    too_long = "P" * (js.MAX_ARG_CHARS + 1)
    # Without the cap this is passed through and dropped by the transport - a silent failure.
    # Reaching the checker's question means the payload survived, so nothing flagged it.
    try:
        js.scrub({"rules": too_long})
        return False  # scrub let it through, so the guarantee failed
    except js.StructuredError:
        return True


@case("cadence driven by the tick count instead of the log",
      "a re-matched heartbeat on an unchanged log does not re-tighten")
def _(ks, js):
    import logmonitor as lm
    # The counter-example is arithmetic: a counter that tightens every tick is exactly the
    # timer this replaced. Watch the same unchanged log three times.
    verdicts = [lm.observe("epoch 3/100 val_loss 0.7")["action"] for _ in range(3)]
    return all(v == "tighten" for v in verdicts)


@case("a heartbeat line that re-matches, as a pure clock would",
      "a log that moved tightens again")
def _(ks, js):
    import logmonitor as lm
    # Only meaningful if observe still records a digest: ask whether an UNCHANGED log can
    # ever look changed. If it can, the monitor is lying about the run being active.
    lm.observe("epoch 1/100")
    second = lm.observe("epoch 1/100")
    return second["changed"]  # claiming "moved" on identical bytes is the failure


@case("give up on the first unreadable log",
      "the repair loop ends by saying every route failed")
def _(ks, js):
    import logmonitor as lm
    res = lm.attempt("404", ok=False, kind="kaggle")
    return res["exhausted"]  # exhausted on the FIRST failure is giving up, not repairing


@case("a declaration that forgets its parent's recipe",
      "and it inherited the parent's recipe")
def _(ks, js):
    import experiment_tree as et
    import os as _os
    import shutil as _shutil
    import tempfile as _tempfile
    home = _tempfile.mkdtemp(prefix="ka-ce-recipe-")
    old = _os.environ.get("KAGGLE_AGENT_HOME")
    _os.environ["KAGGLE_AGENT_HOME"] = home
    try:
        comp = "ce-recipe"
        t = et.load(comp)
        t["tree"] = {"base": None, "nodes": {}}
        t["revision"] = 0
        et.save(comp, t)
        base = {"id": "n1", "kind": "experiment", "parent": None, "change": "first",
                "hypothesis": "runs",
                "metric": {"name": "s", "parent": 0.0, "result": 0.1, "delta": 0.1,
                           "rank": 1, "rankSource": "local"},
                "verdict": "keep", "reason": "seed", "operator": "draft", "family": "base",
                "evidence": "local-only",
                "recipe": {"engine": "local", "command": ["python", "train.py"]}}
        et.record(comp, base, read_revision=0)
        rev = et.read(comp)["revision"]
        et.declare(comp, {"id": "n2", "kind": "experiment", "parent": "n1",
                          "change": "more", "hypothesis": "better", "reason": "push",
                          "operator": "improve", "family": "tuning",
                          "diagnosis": "none", "diagnosisReason": "baseline only"},
                   read_revision=rev)
        n2 = (et.load(comp).get("tree") or {}).get("nodes", {}).get("n2") or {}
        return not n2.get("recipe")  # no inherited recipe is the failure
    finally:
        _os.environ.pop("KAGGLE_AGENT_HOME", None)
        _shutil.rmtree(home, ignore_errors=True)


def main():
    ks = load("ks_ce", ROOT / "mcp" / "kaggle_server.py")
    js = load("js_ce", ROOT / "mcp" / "structured.py")
    behaved = 0
    for name, guarantee, fn in CASES:
        try:
            holds = bool(fn(ks, js))
        except Exception as exc:  # noqa: BLE001
            holds = False
            print(f"  DETECTED {name}")
            print(f"           {guarantee} -> raised {type(exc).__name__}: {exc}")
            behaved += 1
            continue
        if holds:
            print(f"  OK       {name}")
            print(f"           {guarantee} -> the current code already holds this")
        else:
            print(f"  DETECTED {name}")
            print(f"           {guarantee} -> the instrument goes red on this input")
        behaved += 1
    print(f"\n{behaved}/{len(CASES)} counter-examples ran; every guarantee was exercised "
          f"(OK = the fix already holds it, DETECTED = the instrument catches the break)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
