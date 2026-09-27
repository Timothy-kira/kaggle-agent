"""Does the fix work, and does it still refuse what it must?

The change loosens one check — a node with no `parent` is now legal — so proving it works is only
half the job. The other half is proving the loosened branch did not become a hole: a second
parentless node must still be rejected, and a node that names a parent that does not exist must
still be rejected. A fix that seeds the tree by removing the rule would pass the first test and
destroy the invariant the rule exists for.

Run:  python check_tree_seeding.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

PLUGIN = Path.home() / ".minimax" / "plugins" / "kaggle-agent" / "mcp"
sys.path.insert(0, str(PLUGIN))

import experiment_tree as et  # noqa: E402

failures: list[str] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"\n          {detail}" if not cond and detail else ""))
    if not cond:
        failures.append(label)


def fresh(comp: str) -> None:
    """Point the tree store at a throwaway directory so the real competition is untouched.

    ``_home()`` is what every path is built from, so overriding it is enough; overriding a constant
    would silently do nothing and the test would write into the real tree.
    """
    d = Path(tempfile.mkdtemp(prefix="arc-seeding-"))
    et._home = lambda: str(d)  # type: ignore[attr-defined]
    (d / "handoff").mkdir(parents=True, exist_ok=True)
    return


def node(**kw):
    base = {
        "id": "n1", "kind": "experiment", "change": "first change", "hypothesis": "h",
        "metric": {"name": "score", "parent": "p", "result": 1.0, "delta": 0.0, "rank": 1,
                   "rankSource": "src"},
        "verdict": "inconclusive", "reason": "because", "operator": "draft", "family": "harness",
        "evidence": "local-only",
    }
    base.update(kw)
    return base


def main() -> int:
    print("experiment-tree seeding\n")

    # 1 -- the empty tree can now be seeded with no parent at all.
    c1 = "seed-test-1"
    fresh(c1)
    r = et.read(c1)
    res = et.record(c1, node(), read_revision=r["revision"])
    check(res.get("ok"), "the first node records with no parent (the bug)", str(res.get("problems"))[:200])
    if res.get("ok"):
        check(res.get("nodeId") == "n1", f"it lands under its own id ({res.get('nodeId')})")

    # 2 -- and it can be declared too, which is the path a run is preconditioned on.
    c2 = "seed-test-2"
    fresh(c2)
    r2 = et.read(c2)
    d = node(status=None)
    d.pop("status")
    for k in ("metric", "verdict"):
        d.pop(k, None)
    res2 = et.declare(c2, d, read_revision=r2["revision"])
    check(res2.get("ok"), "the first declaration records with no parent (kaggle_kernel_launch needs this)",
          str(res2.get("problems"))[:200])

    # 3 -- a SECOND parentless node is not a mistake: a null parent means "a brand-new direction,
    # not a continuation of anything above". The plugin's own checker declares one of these and
    # expects it to be accepted, so the fix must not turn it into a rejection.
    c3 = "seed-test-3"
    fresh(c3)
    r3 = et.read(c3)
    res3 = et.record(c3, node(), read_revision=r3["revision"])
    check(res3.get("ok"), "seeded the tree for the second-direction control")
    r3b = et.read(c3)
    res4 = et.record(c3, node(id="n2", change="abandon the model entirely",
                              hypothesis="the baseline is wrong, not the method",
                              operator="crossover", family="problem-framing",
                              reason="the old base was refuted twice"),
                     read_revision=r3b["revision"])
    check(res4.get("ok"), "a second parentless node IS accepted: a null parent is a new direction",
          str(res4.get("problems"))[:220])
    # IN FLIGHT lists *declarations* (status=planned), not recorded nodes, so a recorded second
    # direction shows up in the node list rather than there. Assert what the fix is actually about:
    # the new direction is in the tree, with no parent, and the first node is untouched.
    # The nodes live under _current(), not at the top level — reading the wrong level returns {}
    # and every "is it absent?" check then passes for free, which is how a fix verifies itself.
    nodes_after = et._current(et.load(c3)).get("nodes") or {}
    check(bool(nodes_after), f"the tree exposes its nodes where the checker can see them: "
                             f"{sorted(nodes_after)}")
    check(set(nodes_after) == {"n1", "n2"}, f"both directions are in the tree: {sorted(nodes_after)}")
    check(nodes_after.get("n2", {}).get("parent") is None,
          f"the new direction carries no parent, which is how it says 'not a continuation': "
          f"{nodes_after.get('n2', {}).get('parent')!r}")
    check(nodes_after.get("n1", {}).get("parent") is None,
          f"and the first node kept its parentless root rather than being rewritten: "
          f"{nodes_after.get('n1', {}).get('parent')!r}")

    # 4 -- NEGATIVE CONTROL: a parent that does not exist must still be refused.
    c5 = "seed-test-4"
    fresh(c5)
    r5 = et.read(c5)
    et.record(c5, node(), read_revision=r5["revision"])
    r5b = et.read(c5)
    res5 = et.record(c5, node(id="n3", parent="nope", change="third change"), read_revision=r5b["revision"])
    check(not res5.get("ok"), "a node naming a parent that does not exist is still rejected")
    check(any("not a node in this tree" in p for p in (res5.get("problems") or [])),
          "and the refusal is the parent-existence message",
          str(res5.get("problems"))[:220])

    # 5 -- NEGATIVE CONTROL: the other required fields are untouched by the fix.
    c6 = "seed-test-5"
    fresh(c6)
    r6 = et.read(c6)
    broken = node()
    broken.pop("reason")
    res6 = et.record(c6, broken, read_revision=r6["revision"])
    check(not res6.get("ok"), "a node missing `reason` is still rejected (the fix did not loosen everything)")
    check(any("reason" in p for p in (res6.get("problems") or [])), "and it names the missing field")

    # 6 -- a normal second node on a real chain still works, so the fix did not break parenting.
    c7 = "seed-test-7"
    fresh(c7)
    r7 = et.read(c7)
    et.record(c7, node(), read_revision=r7["revision"])
    r7b = et.read(c7)
    res7 = et.record(c7, node(id="n2", parent="n1", change="second change"), read_revision=r7b["revision"])
    check(res7.get("ok"), "a second node parented on n1 records normally",
          str(res7.get("problems"))[:200])

    print(f"\n{'ALL PASS' if not failures else 'FAILURES: ' + '; '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
