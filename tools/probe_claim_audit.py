"""End-to-end: action='audit-report' through the real MCP stdio server.

The claim audit exists because a report is the one document whose failure mode is a confident
sentence nobody can check. This probe proves the two halves are actually different halves:

  - the mechanical pass REFUSES. A forbidden sentence copied in, and a number attributed to a
    node that does not hold it, must both come back as refusals. Neither needs a reviewer, and
    a check that only fires after a model has looked is not a check.
  - the mechanical pass does NOT ACQUIT. A number that matches must be reported as *existing*,
    never as *supported*, and the packet it hands over must be file paths - because a reviewer
    given a summary is reviewing the summary.

Driving it over stdio matters: the argument names, the JSON round trip and the dispatch are
where the last real bugs lived.

Usage:  python tools/probe_claim_audit.py
"""

import importlib.util
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
COMP = "probe-audit"


def _pc():
    spec = importlib.util.spec_from_file_location("pp", ROOT / "tools" / "probe_plot_cycle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def call(args_list, home):
    env = dict(os.environ)
    env["PLUGIN_ROOT"] = str(ROOT)
    env["KAGGLE_AGENT_HOME"] = str(home)
    proc = subprocess.Popen(
        [sys.executable, "-B", str(SERVER)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env=env, cwd=str(ROOT))
    script = [{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
        "protocolVersion": "2024-11-05", "capabilities": {},
        "clientInfo": {"name": "probe", "version": "0"}}}]
    for i, args in enumerate(args_list, start=1):
        script.append({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                       "params": {"name": "kaggle_experiment_tree", "arguments": args}})
    try:
        raw, err = proc.communicate("\n".join(json.dumps(m) for m in script) + "\n", timeout=300)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return [{"text": "<<timed out>>"}] * len(args_list)
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
        out.append({"text": payload[0].get("text", ""), "code": (res.get("result") or {}).get("isCode")})
    return out


def main():
    pp = _pc()
    # The seed helper carries its own competition slug. Auditing a DIFFERENT one means
    # auditing an empty tree, and then "the number was checked" passes for the wrong reason:
    # every node is missing, so nothing matches. Point it at this probe's tree.
    pp.COMP = COMP
    failures = 0

    def ok(cond, label, detail=""):
        nonlocal failures
        if not cond:
            failures += 1
        print(f"  {'ok  ' if cond else 'FAIL'} {label}" + (f"\n         {detail}" if detail else ""))

    home = Path(_mkdtemp(prefix="ka-audit-"))
    # The seeded nodes claim an artifact path that does not exist, and the audit refuses a
    # phantom artifact - correctly, because a tree that claims a run produced a file it never
    # produced is exactly the "plausible unsupported success" this whole stage exists to catch.
    # The fixture was lying, not the check. Make the artifact real.
    real_artifact = home / "run-output.txt"
    real_artifact.write_text("the run this node claims to have produced\n", encoding="utf-8")
    for n in pp.NODES:
        n["artifacts"] = [str(real_artifact)]
    pp.seed(home)

    # read the ledger once so the fixture uses the tree's OWN forbidden sentence rather than
    # one invented here - a fixture that guesses the wording tests nothing.
    led = call([{"competition": COMP, "action": "report"}], home)[0]["text"]
    m = re.search(r"^\s*-\s*no-anchor:\s*(.+)$", led, re.M)
    forbidden = m.group(1).strip() if m else ""
    ok(bool(forbidden), "the ledger carries a forbidden sentence to copy into the fixture",
       forbidden[:90])

    clean = home / "report-clean.md"
    clean.write_text(
        "# Results\n\n"
        "The kept chain reached n3: 0.61, from a base of n1: 0.50.\n"
        "That is a delta of 0.04 on the primary metric.\n"     # unattributed: prose, not checked
        "## Limitations\n\nThe held-out set was never declared.\n", encoding="utf-8")

    dirty = home / "report-dirty.md"
    dirty.write_text(
        "# Results\n\n"
        "The kept chain reached n3: 0.97.\n"          # n3 holds 0.61
        "Also n1: 0.50.\n"                            # n1 does hold this
        f"We can say this anyway: {forbidden}\n",     # verbatim from mayNotClaim
        encoding="utf-8")

    print("clean report")
    a = call([{"competition": COMP, "action": "audit-report", "path": str(clean)}], home)[0]
    t = a["text"]
    ok("REFUSED" not in t, "a clean report is not refused", t[:200])
    nodes_seen = re.search(r"against (\d+) node\(s\)", t)
    ok(nodes_seen is not None and int(nodes_seen.group(1)) >= 3,
       "the audit actually read the seeded tree, not an empty one",
       f"nodes seen: {nodes_seen.group(1) if nodes_seen else '?'}")
    ok(re.search(r"2 matched, 0 matched nothing", t) is not None,
       "both correct numbers matched and nothing was flagged", t[:200])
    ok("MATCHING A NUMBER IS NOT SUPPORT" in t,
       "a matching number is explicitly NOT called supported", t[:200] if "NOT SUPPORT" not in t else "")
    ok("tree.json" in t, "the reviewer packet names the tree file, not a summary of it")

    print("dirty report")
    b = call([{"competition": COMP, "action": "audit-report", "path": str(dirty)}], home)[0]
    t2 = b["text"]
    ok("REFUSED" in t2, "a forbidden sentence copied in is refused mechanically",
       t2[:240] if "REFUSED" not in t2 else "")
    ok("forbidden_claim" in t2, "the refusal names the kind",
       t2[:240] if "forbidden_claim" not in t2 else "")
    ok("0.97" in t2, "the number n3 does not hold is called out", t2[:240] if "0.97" not in t2 else "")
    ok("needs no reviewer" in t2 or "mechanical" in t2.lower(),
       "the refusal is marked as needing no second opinion",
       t2[:240] if "no reviewer" not in t2 and "mechanical" not in t2.lower() else "")

    print("missing report")
    c = call([{"competition": COMP, "action": "audit-report",
               "path": str(home / "nope.md")}], home)[0]
    ok("report_missing" in c["text"] or "no report at" in c["text"],
       "auditing a file that is not there is refused", c["text"][:200])

    print("\n" + ("all claim-audit probes passed" if not failures else f"{failures} probe(s) FAILED"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
