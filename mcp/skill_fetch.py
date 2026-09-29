"""Downloading a published skill, and the four gates it has to pass first.

A SKILL.md is not data. It is instructions that the next turn will read and follow, so putting
one in front of an agent is the same act as telling the agent to do something. Fetching it from
a branch at runtime makes that act happen at a moment nobody reviewed, from a version nobody
pinned, with no record afterwards. Four gates turn that back into a decision that can be
explained:

1. **Allowlist.** The path has to be one this package names. A name that merely appears in the
   index is not permission; permission is a list in this file, and it is a list a person can
   read before it matters.
2. **Pin.** Content comes from one commit, never from a branch. ``main`` moves, and a skill
   that changed between the plan being written and the plan being followed is a plan nobody
   read.
3. **Cache first.** A copy that is already on disk is used without opening a socket. The cache
   is keyed by commit, so a new version lands beside the old one instead of quietly replacing
   it.
4. **Scan.** The same ``scan_for_injection`` the package's own check uses, on the same
   definition - one implementation, so the wall cannot be two walls. Its verdict is a floor, not
   a clearance: a clean regex scan means no known-bad string, and the code says so where the
   gate is.

Nothing is stored in the package. Downloads land under ``~/.kaggle-agent/skill-cache/<sha>/``,
outside the marketplace bundle and outside git, because the point of fetching is to read
something, not to ship it.

A failure is reported as what was observed - the HTTP status, the transport's own error text,
and the offline capability probe - and never as a conclusion about what to do about it. See the
module note in ``kdense_index`` for why the taxonomy is deliberately absent.
"""

from __future__ import annotations

import importlib.util
import os
from typing import Any, Optional

# Skills this package is willing to download. Deliberately a list of names rather than a
# pattern: the point of an allowlist is that a reader can see the whole thing, and a pattern
# silently grows.
#
# Every name here is a name that exists upstream. This package's own `ablation-design` and
# `rsi-experiment-tree` are deliberately absent: they are not in the catalogue, so listing them
# would promise a download that can only ever 404, and a list that lies about what it will fetch
# is worse than a shorter one.
ALLOWED: tuple[str, ...] = (
    "experimental-design",
    "hypothesis-generation",
    "scientific-brainstorming",
    "scientific-writing",
    "statistical-power",
    "peer-review",
)

# A skill is a directory; a fetch is per-file so that one large asset cannot be dragged in
# because someone wanted the instruction file.
SKILL_ENTRY = "SKILL.md"


def _checker():
    """The package's own scanner, loaded by path.

    Loaded rather than reimplemented so "the same scan the check runs" is a fact about one
    function, not a promise about two that agree today. ``check_plugin`` defines constants and
    functions at import time and runs its checks under ``__main__``, so importing it here has
    no side effect.
    """
    tools = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
    if tools not in os.sys.path:
        os.sys.path.insert(0, tools)
    spec = importlib.util.spec_from_file_location(
        "ks_skill_fetch_check", os.path.join(tools, "check_plugin.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _index():
    spec = importlib.util.spec_from_file_location(
        "ks_skill_fetch_index",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "kdense_index.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def allowed(name: str) -> bool:
    return str(name or "").strip() in ALLOWED


def _scan(text: str) -> dict[str, Any]:
    """Run the package scanner and report what it found, without deciding what it means."""
    cp = _checker()
    hits = cp.scan_for_injection(text)
    return {
        "scanned": True,
        "hits": [{"pattern": pid, "line": lineno, "phrasedAsProhibition": proh}
                 for pid, lineno, proh in hits],
        "clean": not hits,
        "verdictMeans": "no known-bad string matched; this is a floor, not a clearance",
    }


def fetch(name: str, commit: str, *, index_commit: str = "") -> dict[str, Any]:
    """Fetch one allowed skill at one commit, through all four gates in order.

    The order is the point. Allowlist before network, so a name this package does not sanction
    costs nothing and reveals nothing. Cache before network, so the common case - a skill
    already read once this session - never touches the wire at all.
    """
    ki = _index()
    nm = str(name or "").strip()

    if not allowed(nm):
        return {"ok": False, "stage": "allowlist", "name": nm,
                "detail": f"{nm!r} is not in the allowlist",
                "allowed": list(ALLOWED)}

    if not str(commit or "").strip():
        return {"ok": False, "stage": "pin", "name": nm,
                "detail": "no commit was named; a branch would move under the fetch"}

    path = f"skills/{nm}/{SKILL_ENTRY}"

    cached = ki.cached_skill(commit, path)
    if cached is not None:
        return {"ok": True, "stage": "cache", "name": nm, "path": path, "commit": commit,
                "network": "not used", "source": "cache",
                "scan": _scan(cached), "text": cached}

    got = ki.fetch_skill(commit, path)
    if got.get("outcome") != "ok":
        got["stage"] = "fetch"
        got["name"] = nm
        return got

    text = got.get("text") or ""
    scan = _scan(text)
    if not scan["clean"]:
        # The rejected text is named and quoted, never stored and never summarised away: the
        # caller has to be able to show a human the line that stopped it.
        return {"ok": False, "stage": "scan", "name": nm, "path": path, "commit": commit,
                "network": "used", "scan": scan,
                "detail": "the scanner matched; nothing was stored"}

    stored = ki.store_skill(commit, path, text)
    return {"ok": True, "stage": "fetched", "name": nm, "path": path, "commit": commit,
            "network": "used", "source": "upstream", "stored": stored,
            "scan": scan, "text": text}
