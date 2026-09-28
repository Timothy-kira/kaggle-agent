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
                          "diagnosis": "none", "diagnosisReason": "baseline only",
                          "expect": {"direction": "up", "atLeast": 0.01}},
                   read_revision=rev)
        n2 = (et.load(comp).get("tree") or {}).get("nodes", {}).get("n2") or {}
        return not n2.get("recipe")  # no inherited recipe is the failure
    finally:
        _os.environ.pop("KAGGLE_AGENT_HOME", None)
        _shutil.rmtree(home, ignore_errors=True)


@case("declare with no prediction (the gate the search relies on)",
      "declare refuses a node with no prediction")
def _(ks, js):
    import experiment_tree as et
    import os as _os
    import shutil as _shutil
    import tempfile as _tempfile
    home = _tempfile.mkdtemp(prefix="ka-ce-expect-")
    old = _os.environ.get("KAGGLE_AGENT_HOME")
    _os.environ["KAGGLE_AGENT_HOME"] = home
    try:
        comp = "ce-expect"
        t = et.load(comp); t["tree"] = {"base": None, "nodes": {}}; t["revision"] = 0
        et.save(comp, t)
        base = {"id": "b1", "kind": "experiment", "parent": None, "change": "seed",
                "hypothesis": "h",
                "metric": {"name": "s", "parent": 0.0, "result": 0.3, "delta": 0.3,
                           "rank": 1, "rankSource": "local"},
                "verdict": "keep", "reason": "r", "operator": "draft", "family": "base",
                "evidence": "local-only"}
        et.record(comp, base, read_revision=0)
        d = {"id": "n1", "kind": "experiment", "parent": "b1", "change": "c",
             "hypothesis": "h", "reason": "r", "operator": "improve", "family": "opt",
             "diagnosis": "none", "diagnosisReason": "base"}
        res = et.declare(comp, d, read_revision=et.read(comp)["revision"])
        return res.get("ok")  # ACCEPTED is the failure: nothing forces a prediction
    finally:
        _os.environ.pop("KAGGLE_AGENT_HOME", None)
        _shutil.rmtree(home, ignore_errors=True)


@case("a kept node whose prediction was refuted, hidden from the tally",
      "board counts a kept node that worked for the wrong reason")
def _(ks, js):
    import experiment_tree as et
    import os as _os
    import shutil as _shutil
    import tempfile as _tempfile
    home = _tempfile.mkdtemp(prefix="ka-ce-surprise-")
    old = _os.environ.get("KAGGLE_AGENT_HOME")
    _os.environ["KAGGLE_AGENT_HOME"] = home
    try:
        nodes = {
            "b1": {"kind": "experiment", "verdict": "keep", "change": "seed",
                   "metric": {"name": "s", "parent": 0.0, "result": 0.3, "delta": 0.3}},
            # gained, was kept, and was NOT predicted: the cell that teaches.
            "r1": {"kind": "experiment", "verdict": "keep", "change": "c",
                   "metric": {"name": "s", "parent": 0.3, "result": 0.35, "delta": 0.05},
                   "expectation": {"verdict": "partial"}},
        }
        tree = {"tree": {"base": {"id": "b1"}, "nodes": nodes}, "revision": 1}
        tally = et._expectation_tally(nodes)
        # If the tally only counted confirmations, this number would be 0.
        return tally.get("keptNotAsPredicted") != 1
    finally:
        _os.environ.pop("KAGGLE_AGENT_HOME", None)
        _shutil.rmtree(home, ignore_errors=True)


@case("a curriculum that does not gate (declaring the hard stage succeeds)",
      "declaring the hard stage first is refused")
def _(ks, js):
    import experiment_tree as et
    import os as _os
    import shutil as _shutil
    import tempfile as _tempfile
    home = _tempfile.mkdtemp(prefix="ka-ce-stage-")
    old = _os.environ.get("KAGGLE_AGENT_HOME")
    _os.environ["KAGGLE_AGENT_HOME"] = home
    try:
        comp = "ce-stage"
        t = et.load(comp); t["tree"] = {"base": None, "nodes": {}}; t["revision"] = 0
        et.save(comp, t)
        et.set_stage(comp, curriculum=[{"name": "smoke"}, {"name": "scale"}],
                     read_revision=0)
        d = {"id": "n1", "kind": "experiment", "parent": None, "change": "c",
             "hypothesis": "h", "reason": "r", "operator": "draft", "family": "f",
             "diagnosis": "none", "diagnosisReason": "f", "stage": "scale",
             "expect": {"direction": "up", "atLeast": 0.01}}
        res = et.declare(comp, d, read_revision=et.read(comp)["revision"])
        return res.get("ok")  # ACCEPTED is the failure: the stage did not lock
    finally:
        _os.environ.pop("KAGGLE_AGENT_HOME", None)
        _shutil.rmtree(home, ignore_errors=True)


@case("abandon that deletes the branch folder instead of moving it",
      "the branch folder is moved, not left in place")
def _(ks, js):
    import experiment_tree as et
    import os as _os
    import shutil as _shutil
    import tempfile as _tempfile
    from pathlib import Path as _P
    home = _tempfile.mkdtemp(prefix="ka-ce-aband-")
    old = _os.environ.get("KAGGLE_AGENT_HOME")
    _os.environ["KAGGLE_AGENT_HOME"] = home
    try:
        comp = "ce-aband"
        t = et.load(comp); t["tree"] = {"base": None, "nodes": {}}; t["revision"] = 0
        et.save(comp, t)
        bdir = et.branch_dir(comp, "bad")
        _os.makedirs(bdir, exist_ok=True)
        _P(bdir, "model.pt").write_text("w", encoding="utf-8")
        # The destroy-it variant: remove the folder instead of moving it aside. The
        # instrument must then find the file GONE from quarantine - which is the harm.
        _shutil.rmtree(bdir)
        return not _P(et.quarantine_dir(comp, "bad"), "model.pt").exists()
    finally:
        _os.environ.pop("KAGGLE_AGENT_HOME", None)
        _shutil.rmtree(home, ignore_errors=True)


def _fresh_home(fn, prefix):
    """Run fn(et) against a throwaway tree home. Returns whatever fn returned."""
    import experiment_tree as et
    import os as _os
    import shutil as _shutil
    import tempfile as _tempfile
    home = _tempfile.mkdtemp(prefix=prefix)
    _os.environ["KAGGLE_AGENT_HOME"] = home
    try:
        return fn(et)
    finally:
        _os.environ.pop("KAGGLE_AGENT_HOME", None)
        _shutil.rmtree(home, ignore_errors=True)


def _ab(nid, factors, result, parent=None, controls=None, **extra):
    n = {"id": nid, "kind": "experiment", "parent": parent, "change": "swap one thing",
         "hypothesis": "h", "metric": {"name": "s", "parent": 0.0, "result": result,
                                       "delta": result, "rank": 1, "rankSource": "local",
                                       "direction": "higher"},
         "verdict": "keep", "reason": "r", "artifacts": ["a"], "evidence": "local-only",
         "operator": "draft", "family": "f"}
    if factors is not None:
        n["factors"] = factors
    if controls is not None:
        n["controls"] = controls
    n.update(extra)
    return n


_CTL = {"seed": 1, "budget": "s", "eval": "v", "retrain": "re-eval"}


@case("an empty factor list read as 'not recorded'",
      "the bare arm is the baseline")
def _(ks, js):
    import experiment_tree as et
    tree = {"base": "n0", "nodes": {
        "n0": _ab("n0", [], 0.50, controls=_CTL),
        "n1": _ab("n1", ["a"], 0.60, parent="n0", controls=_CTL)}}
    good = et.ablation_table(tree)
    # The break: the old _factor_set returned None for an empty list, so the bare arm left
    # the table entirely and the next shortest arm was promoted into its place.
    rows_without_bare = [r for r in good["rows"] if r["factors"]]
    promoted = min(rows_without_bare, key=lambda r: (len(r["factors"]), r["node"]))
    return ((good["baseline"] or {}).get("node") == "n0"
            and promoted["node"] == "n1" and len(rows_without_bare) == 1)


@case("an interaction computed from the pair row used as its own solo arm",
      "a pair with no standalone arm produces NO interaction")
def _(ks, js):
    import experiment_tree as et
    # baseline -> +a -> +a+b, with no {b} arm. The break: index lookups written against the
    # BASE configuration reach {a,b} for both `pair` and `solo_b`, so the sum of parts
    # telescopes to the joint and the table prints interaction = 0.00, "independent".
    tree = {"base": "n0", "nodes": {
        "n0": _ab("n0", [], 0.50, controls=_CTL),
        "n1": _ab("n1", ["a"], 0.60, parent="n0", controls=_CTL),
        "n2": _ab("n2", ["a", "b"], 0.75, parent="n1", controls=_CTL)}}
    good = et.ablation_table(tree)
    index = {tuple(r["factors"]): r for r in good["rows"]}
    pair = index.get(("a", "b"))
    solo_b = index.get(tuple(sorted({"a", "b"})))       # the same row - that is the bug
    degenerate = pair is not None and solo_b is not None and pair["node"] == solo_b["node"]
    return good["interactions"] == [] and degenerate


@case("a baseline promoted from the shortest arm in a leave-one-out family",
      "a leave-one-out family has no bare arm, and the table says so")
def _(ks, js):
    import experiment_tree as et
    tree = {"base": "m0", "nodes": {
        "m0": _ab("m0", ["a", "b", "c"], 0.90, controls=_CTL),
        "m1": _ab("m1", ["a", "b"], 0.70, parent="m0", controls=_CTL),
        "m2": _ab("m2", ["a", "c"], 0.68, parent="m0", controls=_CTL)}}
    good = et.ablation_table(tree)
    shortest = min(good["rows"], key=lambda r: (len(r["factors"]), r["node"]))
    return good["baseline"] is None and len(shortest["factors"]) == 2


@case("edge direction taken from sort order instead of the parent link",
      "direction is read off the parent link, so an upward ladder reads as additions")
def _(ks, js):
    import experiment_tree as et
    # A leave-one-out family declared as removals. Sorting by factor count makes the full
    # arm last, so every pair reads as an ADDITION - the opposite of how the runs were
    # declared, which is the whole content of "+b on top of a" versus "a without b".
    tree = {"base": "m0", "nodes": {
        "m0": _ab("m0", ["a", "b", "c"], 0.90, controls=_CTL),
        "m1": _ab("m1", ["a", "b"], 0.70, parent="m0", controls=_CTL),
        "m2": _ab("m2", ["a", "c"], 0.68, parent="m0", controls=_CTL),
        "m3": _ab("m3", ["b", "c"], 0.66, parent="m0", controls=_CTL)}}
    good = et.ablation_table(tree)
    rows = sorted(good["rows"], key=lambda r: (len(r["factors"]), r["node"]))
    by_sort = all(d == "add" for d in
                  [("add" if set(_f(r2)) - set(_f(r1)) else "drop")
                   for i, r1 in enumerate(rows) for r2 in rows[i + 1:]
                   if len(set(_f(r2)) ^ set(_f(r1))) == 1])
    return all(e["direction"] == "drop" for e in good["edges"]) and by_sort


def _f(row):
    return row["factors"]


@case("a two-factor change described in one confident sentence",
      "changing two factors at once is refused")
def _(ks, js):
    import experiment_tree as et

    def scenario(et):
        comp = "ce-ablate"
        t = et.load(comp); t["tree"] = {"base": None, "nodes": {}}; t["revision"] = 0
        et.save(comp, t)
        et.record(comp, _ab("b1", ["a"], 0.50, controls=_CTL), read_revision=0)
        two = _ab("b2", ["a", "b", "c"], 0.70, parent="b1", controls=_CTL)
        return et.record(comp, two, read_revision=et.read(comp)["readRevision"])

    res = _fresh_home(scenario, "ka-ce-ab1-")
    # The break the factor arithmetic exists to catch: this sentence describes two changes and
    # trips none of the conjunction words, so the text scan waves it through.
    sentence = "swap the cache while widening the context window"
    return (et._has_conjunction(sentence) is None
            and not res.get("ok")
            and any("2 factors" in p for p in (res.get("problems") or [])))


@case("a repeat run recorded as if it tested a factor",
      "a run with the same factors as its parent is refused")
def _(ks, js):
    def scenario(et):
        comp = "ce-ablate2"
        t = et.load(comp); t["tree"] = {"base": None, "nodes": {}}; t["revision"] = 0
        et.save(comp, t)
        et.record(comp, _ab("b1", ["a"], 0.50, controls=_CTL), read_revision=0)
        again = _ab("b2", ["a"], 0.52, parent="b1", controls=_CTL)
        res = et.record(comp, again, read_revision=et.read(comp)["readRevision"])
        again["factorsIntent"] = "repeat"
        allowed = et.record(comp, again, read_revision=et.read(comp)["readRevision"])
        return res, allowed
    res, allowed = _fresh_home(scenario, "ka-ce-ab2-")
    return (not res.get("ok")
            and any("seed" in p for p in (res.get("problems") or []))
            and allowed.get("ok") is True)


@case("the control gate reusing the table's 'unknown' rule on the write path",
      "a node that records no factors and no controls is still writable")
def _(ks, js):
    import experiment_tree as et
    # This exact confusion broke all 1231 checks once: _control_diff reports "controls were not
    # recorded" so the TABLE can call a delta unattributable, and the same list was then used
    # as a write gate - which refuses every node in every tree ever recorded.
    silent = {"a": {}, "b": {}}
    strict = et._control_diff(silent, silent, missing_is_a_problem=True)
    lenient = et._control_diff(silent, silent, missing_is_a_problem=False)
    recorded_but_different = et._control_diff({"controls": {"seed": 1}},
                                              {"controls": {"seed": 2}},
                                              missing_is_a_problem=False)
    return bool(strict) and lenient == [] and len(recorded_but_different) == 1


@case("a factor slug with spaces in it, matched as a prefix",
      "the slug rule is anchored, not a prefix match")
def _(ks, js):
    import re as _re
    import experiment_tree as et
    unanchored = _re.compile(r"[a-z0-9][a-z0-9._-]*").match("two words and a space")
    anchored = et.FACTOR_RE.match("two words and a space")
    return unanchored is not None and anchored is None


@case("controls that disagree on the seed, compared as equal",
      "an edge whose arms disagree on a control is not attributable")
def _(ks, js):
    import experiment_tree as et
    nodes = {"c0": _ab("c0", [], 0.50, controls=_CTL),
             "c1": _ab("c1", ["a"], 0.62, controls=dict(_CTL, seed=7))}
    good = et.ablation_table({"base": "c0", "nodes": nodes})
    return bool(good["confounds"]) and good["edges"][0]["attributable"] is False


@case("a delta smaller than the run-to-run spread, called a positive effect",
      "a delta inside the noise floor is not evidence and is marked so")
def _(ks, js):
    import experiment_tree as et
    tree = {"base": "r0", "nodes": {
        "r0": _ab("r0", [], 0.50, controls=_CTL),
        "r0b": _ab("r0b", [], 0.52, parent="r0", controls=_CTL, factorsIntent="repeat"),
        "r1": _ab("r1", ["a"], 0.53, parent="r0", controls=_CTL)}}
    good = et.ablation_table(tree)
    tiny = [e for e in good["edges"] if abs(e["delta"]) <= (good["noise"] or 0)]
    return bool(tiny) and all(e["kind"] == "indistinguishable" for e in tiny)


@case("a settled run measured against its own declaration",
      "the settled run is accepted")
def _(ks, js):
    def scenario(et):
        comp = "ce-decl"
        t = et.load(comp); t["tree"] = {"base": None, "nodes": {}}; t["revision"] = 0
        et.save(comp, t)
        et.record(comp, _ab("p0", ["a"], 0.50, controls=_CTL), read_revision=0)
        et.declare(comp, {"id": "p1", "kind": "experiment", "parent": "p0",
                          "change": "add b", "hypothesis": "h", "reason": "r",
                          "operator": "improve", "family": "f", "factors": ["a", "b"],
                          "controls": dict(_CTL), "diagnosis": "none",
                          "diagnosisReason": "ladder",
                          "expect": {"direction": "up", "atLeast": 0.01}},
                   read_revision=et.read(comp)["readRevision"])
        landed = _ab("p1r", ["a", "b"], 0.62, parent="p1")
        return et.settle(comp, "p1", landed,
                         read_revision=et.read(comp)["readRevision"])

    res = _fresh_home(scenario, "ka-ce-decl-")
    # The break this replaces: the parent link is read literally, so the run is compared with
    # its own announcement, the symmetric difference comes out at 0, and the gate calls it an
    # undeclared repeat. That refused every declare->settle in the tree.
    return res.get("ok") is True


@case("the direction anchor handed a table row instead of a node",
      "a declared-and-settled ladder step reads as an addition")
def _(ks, js):
    import experiment_tree as et
    tree = {"base": "n0", "nodes": {
        "n0": _ab("n0", [], 0.50, controls=_CTL),
        "n1": _ab("n1", ["a"], 0.60, parent="n0", controls=_CTL)}}
    good = et.ablation_table(tree)
    row = next(r for r in good["rows"] if r["node"] == "n1")
    nodes = tree["nodes"]
    # A row carries the score and the factor set but NO parent link, so asking it for a
    # parent returns None and every edge silently falls back to "either".
    return (good["edges"][0]["direction"] == "add"
            and "parent" not in row
            and et._comparison_parent(nodes, row) is None
            and et._comparison_parent(nodes, nodes["n1"]) == "n0")


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
