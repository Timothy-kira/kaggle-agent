"""Handoff documents: the cross-agent relay artifact for a competition.

Why a handoff exists
--------------------
A competition run spans many sessions and often several agents (Claude Code, MiniMax
Code, a human). Each of them re-derives the same background otherwise, and the cost of a
wrong re-derivation is high: re-running a refuted experiment, losing 12h of quota to a
mistake that was already diagnosed, or optimising against a metric that stopped being the
binding constraint an hour ago.

So a handoff is one readable file that answers, for someone who has no context at all:

  - what competition and rules, with the link;
  - what the current **base** is and what it costs;
  - what is already refuted, so it is not re-run;
  - what the **next** experiment is and why it is next;
  - where the code, the tree and the artifacts live.

It is deliberately a document and not a feature. An agent that has never seen this plugin
can still read the file and continue the work.

Storage: ``<home>/.kaggle-agent/handoff/<competition-slug>/``::

    HANDOFF.md      the relay document - the one file a new agent must read
    tree.json       the RSI experiment tree, in the shape rsi-experiment-tree defines
    state.json      machine state: last sync, remote commit, quota at last write

``<home>`` is ``KAGGLE_AGENT_HOME`` when set, else ``~/.kaggle-agent``. The store lives under
the user's own home directory and contains no credential, so it is safe to keep, to read,
and to diff. Tokens live elsewhere entirely (``github_sync`` owns those).

Writing a handoff is never automatic. It happens when the user asks, or at an explicitly
agreed checkpoint - see the ``handoff`` skill for when.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

import experiment_tree  # noqa: E402  (local module; the single owner of tree.json)

SLUG_RE = re.compile(r"[^a-z0-9._-]+")


def home() -> str:
    """Base directory for all plugin state. Overridable so tests and sandboxes work."""
    return os.environ.get("KAGGLE_AGENT_HOME") or os.path.join(
        os.path.expanduser("~"), ".kaggle-agent"
    )


def handoff_root() -> str:
    return os.path.join(home(), "handoff")


def slugify(value: str) -> str:
    """A filesystem-safe, stable id for a competition or project name."""
    slug = SLUG_RE.sub("-", (value or "").strip().lower()).strip("-._")
    return slug or "unnamed"


def comp_dir(competition: str) -> str:
    return os.path.join(handoff_root(), slugify(competition))


def handoff_path(competition: str) -> str:
    return os.path.join(comp_dir(competition), "HANDOFF.md")


def state_path(competition: str) -> str:
    return os.path.join(comp_dir(competition), "state.json")


def exists(competition: str) -> bool:
    return os.path.isfile(handoff_path(competition))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------------------- tree
# tree.json has exactly one owner: experiment_tree. This module used to keep its own copy of
# load/save/base_node/kept_chain/refuted, which meant `handoff_write` could drop a tree straight to
# disk and bypass the read-gate and every shape rule the tree enforces. Two writers, one file, and
# the second writer was the unvalidated one.
#
# The names below are thin aliases, not wrappers with logic: they exist only so the rest of this
# module keeps reading naturally. tools/check_plugin.py asserts that no `def load_tree` /
# `def save_tree` reappears here, and that every tree write goes through experiment_tree.save()
# after experiment_tree.validate().
tree_path = experiment_tree.tree_path
load_tree = experiment_tree.load
base_node = experiment_tree.base_node
kept_chain = experiment_tree.kept_chain
refuted = experiment_tree.refuted


# ------------------------------------------------------------------------- the document


def render(competition: str, meta: dict[str, Any], tree: dict[str, Any]) -> str:
    """Render HANDOFF.md.

    Structure follows what a cold reader needs, in that order: the rules, then where we
    are, then what not to repeat, then what to do next. The tree is summarised, not
    dumped - the next agent can read ``tree.json`` for the full graph.
    """
    base = base_node(tree)
    chain = kept_chain(tree)
    bad = refuted(tree)
    base_metric = (base.get("metric") or {}) if base else {}
    meta = meta or {}

    def val(key: str, default: str = "_(not set)_") -> str:
        got = meta.get(key)
        return str(got).strip() if got not in (None, "") else default

    metric_name = base_metric.get("name", val("metric", "metric"))
    metric_result = base_metric.get("result", "_(unknown)_")

    lines: list[str] = [
        f"# Handoff: {val('title', competition)}",
        "",
        f"_Last written: {meta.get('written_at') or _now()}_",
        "",
        "## Read this first",
        "",
        "This is the state of the work for someone with no prior context. Everything below",
        "was true when it was written; verify anything load-bearing against the tree and the",
        "artifacts before spending quota on it.",
        "",
        "## Competition",
        "",
        f"- **Name**: {val('title', competition)}",
        f"- **Link**: {val('url')}",
        f"- **Task**: {val('task')}",
        f"- **Metric**: {metric_name} (higher is better unless stated otherwise)",
        f"- **Deadline**: {val('deadline')}",
        "",
        "## Where we are: the current base",
        "",
    ]

    if base:
        lines += [
            f"**Base node `{base.get('id')}`** — {base.get('change') or base.get('label') or '(no label)'}",
            "",
            f"- **Metric on base**: {metric_name} = {metric_result}",
        ]
        if base.get("reason"):
            lines.append(f"- **Why it was kept**: {base['reason']}")
        if base.get("cost"):
            lines.append(f"- **What the base now costs**: {base['cost']}")
        if base.get("run"):
            run = base["run"]
            bits = [f"{run.get('ref')}" if run.get("ref") else None]
            if run.get("engine"):
                bits.append(run["engine"])
            if run.get("accelerator"):
                accel_req = run.get("accelerator")
                accel_got = run.get("accelerator_verified")
                if accel_got and accel_got != accel_req:
                    bits.append(f"accelerator requested {accel_req}, verified {accel_got}")
                else:
                    bits.append(f"accelerator {accel_req}")
            if run.get("account"):
                bits.append(f"quota from account `{run['account']}`")
            if run.get("timeout_s"):
                bits.append(f"{run['timeout_s']}s limit")
            summary = ", ".join(b for b in bits if b)
            if summary:
                lines.append(f"- **How it was produced**: {summary}")
        lines.append("")
        if chain and len(chain) > 1:
            lines += ["The kept chain that produced it, newest first:", ""]
            for node in chain:
                mark = "← current base" if node.get("id") == base.get("id") else ""
                lines.append(f"- `{node['id']}` {node.get('change') or ''} {mark}".rstrip())
            lines.append("")
    else:
        lines += [
            "No base yet — no experiment has been kept. The first run establishes the baseline.",
            "",
        ]

    if bad:
        lines += [
            "## Already refuted — do not re-run these",
            "",
            "These were tried and discarded. Re-running them wastes quota and re-learns nothing.",
            "",
            "| node | change | delta | why it was dropped |",
            "| --- | --- | --- | --- |",
        ]
        for node in bad:
            change = str(node.get("change") or "").replace("|", "\\|")
            reason = str(node.get("reason") or "").replace("|", "\\|")
            delta = node.get("delta")
            lines.append(f"| `{node['id']}` | {change} | {delta if delta is not None else '—'} | {reason} |")
        lines.append("")

    lines += [
        "## Next",
        "",
        f"- **Next experiment**: {val('next')}",
        f"- **Hypothesis**: {val('hypothesis')}",
        f"- **What to measure**: {metric_name} on the same baseline, plus the secondary metrics below",
        "",
    ]

    secondary = [n for n in (tree.get("nodes") or {}).values() if n.get("secondary")]
    if secondary:
        lines += ["Secondary metrics already being watched:", ""]
        seen_names: set[str] = set()
        for node in secondary:
            for sec in node["secondary"]:
                name = sec.get("name")
                if name and name not in seen_names:
                    seen_names.add(name)
                    lines.append(f"- {name}")
        lines.append("")

    lines += [
        "## Constraints and gotchas",
        "",
    ]
    for item in meta.get("constraints") or []:
        lines.append(f"- {item}")
    if not meta.get("constraints"):
        lines.append("- _(none recorded)_")
    lines += [
        "",
        "## Where things are",
        "",
        f"- **This handoff**: `{handoff_path(competition)}`",
        f"- **Experiment tree**: `{tree_path(competition)}`",
        f"- **Code / workspace**: {val('workspace')}",
        f"- **Kaggle account in use**: {val('account')}",
    ]
    if meta.get("kernels"):
        lines.append(f"- **Notebooks**: {meta['kernels']}")
    if meta.get("quota_at_write"):
        lines.append(f"- **Quota when written**: {meta['quota_at_write']}")
    lines += [
        "",
        "## How to continue",
        "",
        "1. Read `tree.json` next to this file for the full experiment graph.",
        "2. Check quota and the accounts before launching anything.",
        "3. Branch the next experiment off the current base, changing exactly one thing.",
        "4. Record the node in the tree, then update this document.",
        "",
    ]
    return "\n".join(lines)


def write(
    competition: str,
    meta: Optional[dict[str, Any]] = None,
    tree: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Write HANDOFF.md, tree.json and state.json. Returns what was written.

    ``tree`` is merged into whatever is on disk, so a caller that did not read the tree
    cannot accidentally erase the experiment history.

    The merged tree is validated by its owner before anything is written. This used to drop a
    caller-supplied tree straight to disk, which meant the handoff path could persist a node
    that the record path would have refused - no hypothesis, an "and" in the change, no operator,
    no rank. A handoff that documents an unvalidated tree is a handoff built on a fiction, so the
    merge is now rejected outright and the caller is told which rules it broke.
    """
    meta = dict(meta or {})
    meta["written_at"] = _now()
    directory = comp_dir(competition)
    os.makedirs(directory, exist_ok=True)

    if tree:
        current = load_tree(competition)
        # Accept either the v3 document or a bare tree passed by an older caller, and splice it
        # into the current round through the owner's own shape helper. Indexing "nodes"
        # directly here assumed the pre-v3 flat layout and blew up on a v3 document.
        current_inner = experiment_tree._current(current)
        incoming = experiment_tree._current(tree)
        current_inner["nodes"].update(incoming.get("nodes") or {})
        if incoming.get("base"):
            current_inner["base"] = incoming["base"]
        tree = current
    else:
        tree = load_tree(competition)

    problems = experiment_tree.validate(tree)
    if problems:
        raise ValueError(
            "the handoff tree was rejected by its owner's validation; fix these and retry: "
            + "; ".join(problems)
        )

    experiment_tree.save(competition, tree)
    doc = render(competition, meta, tree)
    with open(handoff_path(competition), "w", encoding="utf-8") as fh:
        fh.write(doc)

    state = _read_json(state_path(competition)) or {}
    state["last_written"] = meta["written_at"]
    state["title"] = meta.get("title") or competition
    with open(state_path(competition), "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    return {
        "competition": competition,
        "slug": slugify(competition),
        "handoff": handoff_path(competition),
        "tree": tree_path(competition),
        "state": state_path(competition),
        "base": (tree.get("base") or {}).get("id"),
        "nodes": len(tree.get("nodes") or {}),
        "bytes": len(doc.encode("utf-8")),
    }


def read(competition: str) -> Optional[str]:
    try:
        with open(handoff_path(competition), "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def _read_json(path: str) -> Optional[dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def list_all() -> list[dict[str, Any]]:
    """Every competition with a handoff on disk, newest first."""
    root = handoff_root()
    out: list[dict[str, Any]] = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        doc = os.path.join(root, name, "HANDOFF.md")
        if not os.path.isfile(doc):
            continue
        state = _read_json(os.path.join(root, name, "state.json")) or {}
        tree = load_tree(name)
        out.append(
            {
                "slug": name,
                "title": state.get("title", name),
                "last_written": state.get("last_written", ""),
                "base": (tree.get("base") or {}).get("id") or None,
                "nodes": len(tree.get("nodes") or {}),
                "path": doc,
            }
        )
    return sorted(out, key=lambda e: e.get("last_written") or "", reverse=True)


def set_sync_state(competition: str, **fields: Any) -> dict[str, Any]:
    """Record the outcome of a remote sync in state.json (never a credential)."""
    state = _read_json(state_path(competition)) or {}
    state["last_sync"] = _now()
    state.update(fields)
    with open(state_path(competition), "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return state
