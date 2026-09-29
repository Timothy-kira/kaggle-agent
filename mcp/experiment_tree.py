"""The RSI experiment tree: a validated DAG, not a suggestion in a markdown file.

Why this is code and not prose
------------------------------
The experiment tree is the thing that stops the next iteration from silently re-running what was
already refuted. That value collapses the moment the tree becomes optional. If the shape is only
described in a skill, then an agent under time pressure can drop the hypothesis, merge two changes
into one node, write "it got better" as a verdict, or skip reading the tree and branch off a stale
base - and every one of those failures is invisible, because the file still parses.

So the shape is enforced here:

  - a node is rejected unless it carries the fields that make it judgeable;
  - an experiment node is rejected if its ``change`` contains an "and", because a node that
    changed two things measured neither;
  - a verdict without a reason is rejected, because the reason is what the next iteration reads;
  - a node whose parent does not exist is rejected, and cycles are refused.

Read-before-write
-----------------
The second rule is the one that makes the loop real. Every write invalidates the reader's claim
to have seen the tree: ``record`` refuses unless the tree was read at its current revision. So two
experiments cannot be recorded back to back - after the first one lands, the tree changed, and
planning the second one requires reading the tree again. That is the entire "read the tree, then
decide the next step" discipline, expressed as a precondition the tool enforces rather than a
step the agent is asked to remember.

What is deliberately NOT specified: how to think. The tree constrains the *shape* of a record and
the *order* records must happen in. It does not tell you what to try next, how many experiments
to run, when to branch, or when to stop. Those are judgement calls, and hard-coding them here
would produce a tree full of correctly-shaped nodes that explore nothing.

Storage: ``<home>/handoff/<slug>/tree.json`` - the same file the handoff document is derived
from, because a handoff that cannot see the tree is a handoff built on a fiction. ``<home>`` is
``KAGGLE_AGENT_HOME`` when set, else ``~/.kaggle-agent``. No credential, no token.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

NODE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.I)

# The four atomic program-evolution operators, from Frontis-MA1 / OpenMLE (arXiv 2607.28568).
# Recording WHICH operator produced a node is not bookkeeping: that paper's central finding is
# that Improve and Crossover produced 85-92% of the total measured gain, while Debug mostly
# just made a program executable and Draft mostly just started one. A tree that cannot see
# which operator did what cannot see where its gains actually came from.
OPERATORS = ("draft", "improve", "debug", "crossover")

# A method family is the label that makes novelty computable. Two nodes that changed the same
# kind of thing are the same family even if the text differs, so novelty means "a direction
# nobody has tried", not "a change nobody has made".
FAMILY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.I)

# Failure layers, from the ETCLOVG taxonomy's experiment-relevant subset. The motivation is
# measured: in Harness-Bench's 5194 failed traces, output-contract violations (36.4%) and
# tool/recovery failures (24.6%) together exceed 60% of failures, and neither is "the model was
# not smart enough" - both are the harness failing to validate output or recover. So a refuted
# node must say WHICH layer broke, or the next iteration has no idea whether to change the tool
# or the skill. (Figures are report-sourced; the layer names are what this plugin enforces.)
FAILURE_LAYERS = (
    "output-contract",       # reasoning right, shape wrong
    "tool-recovery",         # tool failed and nothing recovered
    "evidence-grounding",    # the claim had no support behind it
    "artifact-persistence",  # the run's output did not land
    "state-continuity",      # the run lost where it was
    "metric",                # the measurement itself was wrong
    "other",                 # allowed only with a note saying which
)

# Effective Feedback Compute flags, from arXiv 2605.29682. The paper's point is that raw tokens,
# tool calls, wall time and cost "cannot distinguish useful feedback from redundant or unstable
# interaction"; EFC is the coordinate for feedback that is informative, valid, non-redundant and
# retained. It is judged PER CRITERION here, because "no new information about correctness but a
# cheaper run" is a different finding from the reverse, and collapsing them loses it.
EFC_FLAGS = ("informative", "valid", "redundant", "retained")

VALID_DIRECTIONS = ("higher", "lower")

# ---------------------------------------------------------------- log diagnosis
# The loop this closes: read the run's log, name the bottleneck, and make the NEXT experiment
# cite that reading. Without it the tree knows what you scored and nothing about why a run was
# slow or where it broke, so the improvement step is guesswork wearing a DAG.
#
# The vocabulary is reused, not extended. FAILURE_LAYERS already says which layer of the harness
# broke, and a diagnosis uses exactly those. A run that is merely SLOW is not a layer failure,
# so `layer` is required only when a failure signature actually appears; `bottleneck` is free text
# because "70% of the wall time is data loading" is a finding, not a taxonomy entry.
_DIAG_LAYER_SIGNALS = (
    ("out of memory", "other"),
    ("OutOfMemoryError", "other"),
    ("CUDA error", "other"),
    ("Killed", "other"),
    ("JSONDecodeError", "output-contract"),
    ("KeyError", "output-contract"),
    ("TypeError", "output-contract"),
    # A ValueError raised by the code's own validation is the output contract complaining,
    # not a harness bug, so it belongs to that layer rather than to "other".
    ("ValueError", "output-contract"),
    ("ValidationError", "output-contract"),
    ("assert", "output-contract"),
    ("No such file", "artifact-persistence"),
    ("FileNotFoundError", "artifact-persistence"),
    ("PermissionError", "artifact-persistence"),
    ("timed out", "state-continuity"),
    ("timeout", "state-continuity"),
    ("Connection reset", "tool-recovery"),
    ("RateLimit", "tool-recovery"),
    ("Traceback", "other"),
)

_DIAG_BOTTLENECK_SIGNALS = (
    (r"(\d+(?:\.\d+)?)\s*%\s*of", "percent of the run reported in a single phase"),
    (r"elapsed[^:\n]*:\s*(\d+(?:\.\d+)?)", "an elapsed time was reported"),
    (r"took\s+(\d+(?:\.\d+)?)\s*s", "a phase duration was reported"),
    (r"step\s+(\d+)\s*/\s*(\d+)", "progress is reported per step"),
    (r"throughput[^:\n]*[:=]\s*(\S+)", "a throughput figure was reported"),
)


def diagnose_log(text: str, source: str = "", max_lines: int = 400) -> dict[str, Any]:
    """Read a run's log and return what is checkable, not what it feels like.

    This deliberately does NOT invent a performance taxonomy. It reports the failure layer when
    one of the existing signatures appears, quotes the lines that support it, and otherwise says
    what the log actually contained so a human or the agent can name the bottleneck. Returning
    a confident "bottleneck" that nobody read off the log would be worse than returning none.
    """
    body = (text or "").replace("\r\n", "\n")
    lines = body.split("\n")
    tail = lines[-max_lines:] if len(lines) > max_lines else lines

    layer: str | None = None
    hits: list[str] = []
    # Two passes, and the order matters. "Traceback" appears on the header line, above the
    # exception that actually names the failure, so scanning in file order would report
    # "some layer broke" for every run. A bare traceback says nothing about WHICH layer;
    # the exception type does. So the specific signals win, and the generic one is a fallback.
    specific = [s for s in _DIAG_LAYER_SIGNALS if s[0] != "Traceback"]
    generic = [s for s in _DIAG_LAYER_SIGNALS if s[0] == "Traceback"]
    for pass_signals in (specific, generic):
        for ln in lines:
            low = ln.lower()
            for needle, mapped in pass_signals:
                if needle.lower() in low:
                    hits.append(ln.strip())
                    if layer is None:
                        layer = mapped
                    break
            if layer is not None:
                break
        if layer is not None:
            break

    # the first traceback and the last non-empty line are the two things worth quoting
    traceback_at = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("Traceback (most recent call last)"):
            traceback_at = i
            break
    first_error = None
    if traceback_at is not None:
        for ln in lines[traceback_at + 1:]:
            s = ln.strip()
            if s and not s.startswith("File ") and not s.startswith(" "):
                first_error = s
                break
    last_line = next((ln.strip() for ln in reversed(lines) if ln.strip()), "")

    notes: list[str] = []
    for pattern, why in _DIAG_BOTTLENECK_SIGNALS:
        m = re.search(pattern, body, re.IGNORECASE)
        if m:
            notes.append(f"{why}: {m.group(0).strip()[:80]}")

    return {
        "source": source,
        "lines": len(lines),
        "readLines": len(tail),
        "layer": layer,
        "evidence": hits[-3:],
        "tracebackAt": traceback_at,
        "firstError": first_error,
        "lastLine": last_line[:300],
        "timing": notes[:3],
        "empty": not body.strip(),
        # a node is only a diagnosis if it says what it is a diagnosis OF
        "bottleneck": None,
    }


# Two node kinds, and the difference is the whole point of the second one.
#
# "experiment" changes one thing and measures it. "research" changes nothing and measures
# nothing: it goes back to a source (the forum, the code space, the web, a paper) because a
# result made the existing understanding insufficient. A tree that can only hold experiments
# forces an agent to fake a re-investigation as a change; being able to say "this node is a
# re-read of the forum" is more honest than pretending a question is an ablation.
NODE_KINDS = ("experiment", "research")

# A node with status "planned" is a declaration: an experiment that has been announced and may be
# run, but has no result yet. It exists so kaggle_kernel_launch can refuse a run nobody declared —
# the guarantee that no experiment result lives only in a chat transcript.
#
# The tree is append-only and ids are never reused or rewritten, so the result does NOT fill the
# declaration in place. It lands as a NEW node whose parent is the declaration. A declaration is
# therefore "settled" exactly when it has a child, which is derivable and needs no second writer.
NODE_STATUSES = ("planned", "settled")


def is_planned(node: Any) -> bool:
    """True for a declaration: announced, runnable, and carrying no result yet."""
    return isinstance(node, dict) and node.get("status") == "planned"

VERDICTS = ("keep", "revert", "inconclusive", "superseded")

# What a research node went back to. These are the sources the plugin can actually reach, so a
# research node cannot quietly name a source nothing can open. `ruler` is not an external source:
# it is the measuring surface itself, and a plateau whose remaining failures are not capability
# gaps can only be resolved by reading it. It is a research target precisely because the question
# it answers changes nothing and runs nothing.
RESEARCH_TARGETS = ("forum", "code", "web", "paper", "model", "dataset", "rules", "leaderboard",
                    "ruler")

# Fields every node carries regardless of kind. `parent` is deliberately absent from this list:
# a null parent is not a missing field, it is the way a node says "this is a brand-new direction,
# not a continuation of anything above" — and an explicit null does not survive the MCP transport
# anyway, so the field arrives absent whatever the caller spells. Its VALUE is still checked below:
# if it is set, it must name a node that exists.
COMMON_REQUIRED = ("kind", "reason")

EXPERIMENT_REQUIRED = ("change", "hypothesis", "metric", "verdict")
RESEARCH_REQUIRED = ("question", "targets", "verdict", "opens")

# Fields the paper's operator-conditioned context needs on every experiment node. A node without
# them cannot be scored, ranked, or compared for novelty, which means it cannot inform the next
# selection. They are required, not optional, because that is the mechanism, not a nicety.
SEARCH_REQUIRED = ("operator", "family")

# ------------------------------------------------------------------ what are we trying to optimise
# A tree with scores but no stated objective cannot answer "did this help?", only "did this
# number go up?". Those are different questions and the second one is how a search ends up
# optimising the thing that is easiest to move instead of the thing that matters. So the
# terminal objective is declared once, at the tree level, and a node that measures something
# else has to say why - the same forced-choice shape the evidence check already uses, because a
# silent divergence is exactly what makes a long run drift off its own goal.
CRITERION_ROLES = ("primary", "guard", "observe")
DEFAULT_CRITERION_ROLE = "observe"

# An expectation is a PREDICTION, not a hope: a direction and a floor. A direction with no floor
# is unfalsifiable, because every move can be read as "in the right direction". Requiring the
# floor is what makes "confirmed" and "refuted" different claims.
EXPECTATION_VERDICTS = ("confirmed", "partial", "refuted", "unreadable")

# --------------------------------------------------------------------- what an ablation compares
# "Changes one thing" was a promise in prose, and prose is not checkable. Representing a run as
# the SET of factors it had turns the promise into arithmetic: a child differs from its parent
# by a symmetric difference, and that difference has to be exactly one factor. Two changes in
# one node stops being a wording problem and becomes a set problem.
#
# The set is also what makes an ablation table possible at all. With A, A+B and B recorded as
# factor sets, the interaction - delta(A+B) minus delta(A) plus delta(B) - is a subtraction
# rather than a recollection, and a claim about B can finally be told apart from a claim about
# A+B: without a standalone B arm, "B helps" is really "B together with A helps", and those
# are different claims with different consequences for what to try next.
#
# `controls` exists for the confound that makes such a table quietly wrong: two arms measured
# under different seeds, budgets, eval sets, retrain policies or DATA did not differ by one
# factor, they differed by however many things changed between those two runs. Recording the
# controls makes that difference visible instead of invisible.
#
# `data` is the newest of them, and it answers a question the other four never asked: not "was
# the comparison fair" but "what was it a comparison OF". A run records the dataset it read and
# which version of it, so a delta measured across a re-uploaded or re-versioned file cannot be
# attributed to the factor that was being tested. The tree already pinned which set was held out
# (`metric.split` and the anchor) and which seed was used; what it never recorded was the input
# itself, so two arms could differ by a factor AND by a data version and the table would call it
# a clean win. `eval` is not a substitute - it names the evaluation surface, not the training
# bytes, and a competition that re-uploads its data changes one without touching the other.
FACTOR_RE = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")
# `rebuild` is the sixth because it is the one the other five cannot express. `seed` asks whether
# the comparison was fair; `data` asks what it was a comparison OF. Neither asks whether the thing
# being measured was rebuilt from scratch, and in a competition that is where the variance hides -
# feature caches, preprocessing folds and a previous round's prediction file all carry state
# forward. A repeat arm that rebuilt any of them is not measuring seed spread, and mixing the two
# contaminates every delta measured against it afterwards.
CONTROL_KEYS = ("seed", "budget", "eval", "retrain", "data", "rebuild")
VALID_RETRAIN = ("from-scratch", "re-eval")
# What a run says it is doing when the factor arithmetic alone cannot tell. A factorial arm
# changes two factors and means it; a repeat changes none and is measuring noise. Both are
# legitimate, and both used to be indistinguishable from a mistake at the only moment it
# mattered - after the run, when someone reads the delta.
VALID_INTENT = ("one-factor", "factorial", "repeat")

# Words that turn one experiment into two. "and" is the giveaway, but so are a few others that
# reliably mean the node is describing a pipeline rather than a single variable.
CONJUNCTIONS = (" and ", " then ", " plus ", " 同时 ", " 以及 ")

# Verdict reasons that say nothing. "better" is the classic.
EMPTY_REASONS = {
    "", "tbd", "todo", "n/a", "na", "none", "better", "worse", "good", "bad",
    "improved", "regressed", "no effect", "works", "fails", "?", "...",
}


def _home() -> str:
    return os.environ.get("KAGGLE_AGENT_HOME") or os.path.join(
        os.path.expanduser("~"), ".kaggle-agent"
    )


def _slug(competition: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", (competition or "").strip().lower()).strip("-._")


def comp_dir(competition: str) -> str:
    return os.path.join(_home(), "handoff", _slug(competition) or "unnamed")


# A route is a folder, not a label. Two lines of work on the same competition are two
# directories, so a later run on one cannot silently read, overwrite or ship the other's
# checkpoints - the failure this exists to prevent is not "forgetting to clean up", it is
# picking up a model that belongs to an approach that was already given up on.
BRANCHES_DIRNAME = "branches"
QUARANTINE_DIRNAME = "quarantine"


def branch_dir(competition: str, branch: str) -> str:
    """Where a named line of work keeps its own files."""
    return os.path.join(comp_dir(competition), BRANCHES_DIRNAME, _slug(branch) or "main")


def quarantine_dir(competition: str, branch: str) -> str:
    """Where a given-up line of work is moved, intact and recoverable."""
    return os.path.join(comp_dir(competition), QUARANTINE_DIRNAME, _slug(branch) or "main")


def tree_path(competition: str) -> str:
    return os.path.join(comp_dir(competition), "tree.json")


def _read_doc(path: str) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _sibling_trees() -> list[tuple[str, dict[str, Any]]]:
    """Every tree on disk, as (directory, raw document). Never raises."""
    root = os.path.join(_home(), "handoff")
    out: list[tuple[str, dict[str, Any]]] = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        p = os.path.join(root, name, "tree.json")
        if not os.path.isfile(p):
            continue
        doc = _read_doc(p)
        if doc is not None:
            out.append((os.path.join(root, name), doc))
    return out


def resolve_competition(competition: str) -> tuple[str, list[dict[str, Any]]]:
    """Resolve a requested competition key to the ONE tree that owns it.

    Returns (directory, forks). A tree is addressed by a slug derived from whatever string the
    caller passed, so ``arc-prize-2026-arc-agi-3`` and ``arc-agi-3`` sanitise to two different
    directories and would otherwise be two silent histories of one competition — the exact waste
    the tree exists to prevent. A key already registered on another tree therefore resolves HERE,
    and a key that has a tree of its own AND is claimed elsewhere is reported as a fork rather
    than silently resolved to one of them.
    """
    slug = _slug(competition)
    home = comp_dir(competition)
    own = os.path.join(home, "tree.json")
    if not os.path.isfile(own):
        for directory, doc in _sibling_trees():
            if slug in (doc.get("competitionKeys") or []):
                return directory, []
    if os.path.isfile(own):
        mine = _read_doc(own) or {}
        mine_keys = set(mine.get("competitionKeys") or [])
        forks = []
        for directory, doc in _sibling_trees():
            if directory == home:
                continue
            if slug in (doc.get("competitionKeys") or []):
                forks.append({
                    "dir": directory,
                    "competition": doc.get("competition") or "?",
                    "nodes": len(((doc.get("tree") or {}).get("nodes") or {})),
                })
        return home, forks
    return home, []


def tree_path_resolved(competition: str) -> str:
    return os.path.join(resolve_competition(competition)[0], "tree.json")


def identity(competition: str) -> dict[str, Any]:
    """Which tree a key resolves to, and who else claims it. Cheap, and makes forks visible."""
    directory, forks = resolve_competition(competition)
    doc = _read_doc(os.path.join(directory, "tree.json")) or {}
    return {
        "requested": competition,
        "slug": _slug(competition),
        "directory": directory,
        "exists": bool(doc),
        "competition": doc.get("competition") or "",
        "keys": list(doc.get("competitionKeys") or []),
        "nodes": len(((doc.get("tree") or {}).get("nodes") or {})),
        "forks": forks,
    }


def register_alias(competition: str, alias: str) -> dict[str, Any]:
    """Point another name at this tree, so one competition cannot fork into two histories."""
    if not _slug(alias):
        return {"ok": False, "code": "bad_alias",
                "message": f"{alias!r} is not a usable competition key"}
    directory, forks = resolve_competition(competition)
    if forks:
        return {"ok": False, "code": "forked",
                "message": f"this key is claimed by more than one tree: "
                           f"{forks}. Resolve that before adding another name."}
    path = os.path.join(directory, "tree.json")
    doc = _read_doc(path) or empty_tree()
    if not doc.get("competition"):
        doc["competition"] = _slug(competition)
    keys = list(doc.get("competitionKeys") or [])
    if _slug(competition) not in keys:
        keys.append(_slug(competition))
    if _slug(alias) not in keys:
        keys.append(_slug(alias))
    alias_dir = comp_dir(alias)
    other = _read_doc(os.path.join(alias_dir, "tree.json"))
    if alias_dir != directory and other is not None \
            and ((other.get("tree") or {}).get("nodes") or {}):
        return {"ok": False, "code": "alias_has_own_tree",
                "message": f"{alias!r} already has a tree of its own with "
                           f"{len((other.get('tree') or {}).get('nodes') or {})} nodes at "
                           f"{alias_dir}. Two histories of one competition is exactly what this "
                           f"prevents — move or delete that tree first, or pick the other name."}
    doc["competitionKeys"] = keys
    doc["revision"] = int(doc.get("revision") or 0) + 1
    doc["updatedAt"] = _now()
    save(competition, doc)
    return {"ok": True, "directory": directory, "competition": doc["competition"], "keys": keys,
            "revision": doc["revision"]}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def empty_tree() -> dict[str, Any]:
    """A v3 document: current tree, archived rounds, policy registry, anchor, ruler and journal.

    Everything lives in one file on purpose. Separate documents for the tree and the policy
    history would mean two writers and no way to make "archive the round and deploy the new
    policy" a single atomic step. One document plus the existing tmp+replace write means a
    reader never sees half of either.

    `anchor` and `ruler` answer different questions about the same metric, and that difference is
    why both are here. The anchor says which set the search must not score on, once, and is
    immutable afterwards. The ruler says how finely the metric can resolve a difference at all, and
    is re-derived whenever the measuring surface changes — a stale noise floor would start
    refusing correct experiments, so unlike the anchor it is meant to be overwritten.
    """
    return {
        "schemaVersion": 3,
        "currentRound": 1,
        "deployedPolicy": None,
        "competition": "",
        "competitionKeys": [],
        "anchor": {"declared": False, "heldOut": None, "rule": "", "declaredAt": None},
        "ruler": {},
        "tree": {"base": {"id": "", "label": "", "parent": None}, "nodes": {}},
        "rounds": [],
        "policies": {},
        "journal": [],
        "revision": 0,
        "updatedAt": None,
    }


def _migrate_v2(stored: dict[str, Any]) -> dict[str, Any]:
    """Lift a v2 document into v3. In v2 the whole file *was* the tree."""
    out = empty_tree()
    out["tree"] = {
        "base": stored.get("base") if isinstance(stored.get("base"), dict)
        else {"id": "", "label": "", "parent": None},
        "nodes": stored.get("nodes") if isinstance(stored.get("nodes"), dict) else {},
    }
    if isinstance(stored.get("revision"), int):
        out["revision"] = stored["revision"]
    if isinstance(stored.get("updatedAt"), str):
        out["updatedAt"] = stored["updatedAt"]
    return out


def load(competition: str) -> dict[str, Any]:
    """Read the v3 document. Older versions are migrated in memory; an unreadable file is empty.

    Migration is in memory only. A v2 file is not rewritten until something valid is written over
    it, so a tree that cannot be understood is never destroyed by the act of trying to read it.

    The read is also where a competition claims its key, so a tree always records the name it
    was first created under and every other name later pointed at it. A key that resolves to a
    different directory than the caller asked for — or that another tree also claims — is
    reported rather than resolved silently, because two histories of one competition is the
    failure this whole structure exists to prevent.
    """
    data = empty_tree()
    directory, forks = resolve_competition(competition)
    path = os.path.join(directory, "tree.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            stored = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        stored = None
    if not isinstance(stored, dict):
        # A tree that does not exist yet still knows what it is for. Leaving `competition` empty
        # here meant a brand new tree could not be told apart from a tree whose identity was
        # simply lost, which is exactly the ambiguity this is meant to remove.
        data["competition"] = _slug(competition)
        data["competitionKeys"] = [_slug(competition)]
        data["path"] = path
        data["identity"] = identity(competition)
        data["forks"] = forks
        return data

    version = stored.get("schemaVersion")
    if not isinstance(version, int) or version < 3:
        data = _migrate_v2(stored)
        data["migrated"] = True
        data["fromVersion"] = 2 if version is None else version
        data["path"] = path
        return data

    for key in ("currentRound", "revision"):
        if isinstance(stored.get(key), int):
            data[key] = stored[key]
    for key in ("deployedPolicy", "updatedAt", "competition"):
        if stored.get(key) is not None:
            data[key] = stored[key]
    if isinstance(stored.get("competitionKeys"), list):
        data["competitionKeys"] = [str(k) for k in stored["competitionKeys"] if isinstance(k, str)]
    if not data.get("competition"):
        data["competition"] = _slug(competition)
    if _slug(competition) not in data["competitionKeys"]:
        data["competitionKeys"].append(_slug(competition))
    if isinstance(stored.get("anchor"), dict):
        data["anchor"] = {**data["anchor"], **stored["anchor"]}
    if isinstance(stored.get("tree"), dict):
        inner = stored["tree"]
        data["tree"] = {
            "base": inner.get("base") if isinstance(inner.get("base"), dict)
            else data["tree"]["base"],
            "nodes": inner.get("nodes") if isinstance(inner.get("nodes"), dict) else {},
        }
    if isinstance(stored.get("rounds"), list):
        data["rounds"] = [r for r in stored["rounds"] if isinstance(r, dict)]
    if isinstance(stored.get("policies"), dict):
        data["policies"] = stored["policies"]
    if isinstance(stored.get("journal"), list):
        data["journal"] = [j for j in stored["journal"] if isinstance(j, dict)]
    # The goal, the curriculum, the current stage and the abandoned branches are document
    # state, not read-time decoration, so they have to be carried across the load/save pair
    # exactly like `anchor` and `policies`. Whitelisting the load path is the point - a key
    # missing from this list is a key that silently vanishes on the next read, which is how
    # a declared goal can appear to have been saved and then not be there.
    if isinstance(stored.get("goal"), dict):
        data["goal"] = stored["goal"]
    if isinstance(stored.get("curriculum"), list):
        data["curriculum"] = stored["curriculum"]
    if stored.get("stage") is not None:
        data["stage"] = stored["stage"]
    if isinstance(stored.get("abandoned"), list):
        data["abandoned"] = [a for a in stored["abandoned"] if isinstance(a, dict)]
    data["path"] = path
    data["identity"] = identity(competition)
    data["forks"] = forks
    return data


def save(competition: str, data: dict[str, Any]) -> str:
    """Persist the document. The only writer of tree.json in this package.

    Public because handoff.py delegates its tree writes here instead of keeping a second,
    unvalidated writer. tools/check_plugin.py asserts it is the only one.
    """
    path = tree_path_resolved(competition)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {k: v for k, v in data.items() if not k.startswith("_")}
    payload["schemaVersion"] = 3
    payload.pop("migrated", None)
    payload.pop("fromVersion", None)
    # These describe a read, not a document. Writing them would make the file claim a path and a
    # fork report that are only true at the moment it was read.
    for transient in ("identity", "forks", "path", "problems"):
        payload.pop(transient, None)
    if not payload.get("competition"):
        payload["competition"] = _slug(competition)
    keys = [str(k) for k in (payload.get("competitionKeys") or []) if isinstance(k, str)]
    if _slug(competition) not in keys:
        keys.append(_slug(competition))
    payload["competitionKeys"] = keys
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    # Replace rather than truncate, so a reader mid-cycle never sees a half-written file.
    os.replace(tmp, path)
    return path


def _write(competition: str, data: dict[str, Any]) -> str:
    return save(competition, data)


def base_node(tree: dict[str, Any]) -> dict[str, Any]:
    """The base node, with its id injected from the key it is stored under."""
    inner = _current(tree)
    base_id = (inner.get("base") or {}).get("id") or ""
    node = (inner.get("nodes") or {}).get(base_id) or {}
    return {"id": base_id, **node} if node else {}


def _current(tree: dict[str, Any]) -> dict[str, Any]:
    """The current-round tree, whichever document shape we were handed."""
    if isinstance(tree.get("tree"), dict) and "nodes" in tree["tree"]:
        return tree["tree"]
    return tree


# --------------------------------------------------------------------------- validation

def _norm_reason(value: Any) -> str:
    return str(value or "").strip().lower().rstrip(".")


def _has_conjunction(text: str) -> Optional[str]:
    low = " " + str(text or "").lower().strip() + " "
    for word in CONJUNCTIONS:
        if word in low:
            return word.strip()
    return None


def _validate_document(doc: dict[str, Any], existing: list[str]) -> list[str]:
    """Validate the v3 document shell: anchor, rounds, policies, journal.

    Split out so validate() reads as "the document, then the nodes in it", and so the shell
    rules can be checked without walking every node.
    """
    out: list[str] = []
    if not isinstance(doc.get("tree"), dict):
        out.append("'tree' must be an object")
    if not isinstance(doc.get("rounds"), list):
        out.append("'rounds' must be a list")
    else:
        for i, r in enumerate(doc["rounds"]):
            if not isinstance(r, dict):
                out.append(f"rounds[{i}] must be an object")
            elif not isinstance(r.get("round"), int):
                out.append(f"rounds[{i}] is missing an integer 'round'")
            elif not isinstance(r.get("tree"), dict):
                out.append(f"rounds[{i}] is missing its 'tree'")
    if not isinstance(doc.get("policies"), dict):
        out.append("'policies' must be an object")
    else:
        for pid, p in doc["policies"].items():
            if not isinstance(p, dict):
                out.append(f"policy {pid!r} must be an object")
                continue
            out.extend(_validate_policy(pid, p))
    deployed = doc.get("deployedPolicy")
    if deployed and deployed not in (doc.get("policies") or {}):
        out.append(
            f"deployedPolicy {deployed!r} is not in policies; a run must never point at a policy "
            "that does not exist"
        )
    if not isinstance(doc.get("journal"), list):
        out.append("'journal' must be a list")
    return out


def _validate_policy(pid: str, policy: dict[str]) -> list[str]:
    """Policies are DATA, never code. Nothing here is ever evaluated or executed.

    Only SUPPLIED fields are checked. A caller writing ``{"workers": 2}`` is asking for the
    defaults on everything else, which is an ordinary thing to do; what must be refused is a
    value that was given and is wrong, because clamping that into something valid would produce
    a policy nobody agreed to.
    """
    out: list[str] = []
    params = policy.get("params")
    if not isinstance(params, dict):
        return [f"policy {pid!r}: 'params' must be an object"]
    if "workers" in params:
        workers = params["workers"]
        if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
            out.append(f"policy {pid!r}: params.workers must be an integer >= 1")
    if "maxRounds" in params:
        rounds = params["maxRounds"]
        if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1:
            out.append(f"policy {pid!r}: params.maxRounds must be an integer >= 1")
    for beta in ("betaCost", "betaParallel"):
        if beta in params:
            value = params[beta]
            if not isinstance(value, (int, float)) or value != value or value < 0:
                out.append(f"policy {pid!r}: params.{beta} must be a number >= 0")
    if "weights" in params:
        weights = params["weights"]
        if not isinstance(weights, dict):
            out.append(f"policy {pid!r}: params.weights must be an object")
        else:
            for key, value in weights.items():
                if key not in DEFAULT_WEIGHTS:
                    out.append(f"policy {pid!r}: unknown weight {key!r}")
                elif not isinstance(value, (int, float)) or value != value:
                    out.append(f"policy {pid!r}: weights.{key} must be a finite number")
    return out


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _metric_sign(metric: Any) -> float:
    """+1 when a higher number is better, -1 when lower is, so one comparison serves both."""
    if not isinstance(metric, dict):
        return 1.0
    return -1.0 if str(metric.get("direction", "higher")).strip().lower().startswith("lower") else 1.0


def judge_expectation(expect: Any, metric: Any, ruler: Any = None) -> dict[str, Any]:
    """Did the run do what was predicted, or only what was hoped?

    This is the question "the result and the expectation agreed" has never been able to answer,
    because a hypothesis records why a change should matter and not what it should move. A node
    that gained for a reason nobody predicted is a different animal from one that gained as
    intended, and only the second one tells you the next change will compound.

    The verdict is deliberately three-valued rather than two. "partial" is where the useful
    judgement lives: the direction was right but the floor was not cleared, which reads as a
    success in a kept-node list and is in fact a much weaker result than it looks. Collapsing
    that into confirmed is how a search convinces itself it understands what it is doing.
    """
    if not isinstance(expect, dict):
        return {"verdict": "unreadable", "why": "no expectation was recorded, so there is "
                "nothing to compare the result against. A gain with no prediction is not "
                "evidence that the idea worked - only that the number moved."}
    if not isinstance(metric, dict):
        return {"verdict": "unreadable",
                "why": "the node recorded no metric, so the expectation cannot be judged."}

    direction = str(expect.get("direction") or "up").strip().lower()
    wanted_up = not direction.startswith("down")
    try:
        at_least = abs(float(expect.get("atLeast")))
    except (TypeError, ValueError):
        return {"verdict": "unreadable", "why": "expectation.atLeast is not a number, so the "
                "prediction has no floor and cannot be confirmed or refuted."}

    delta = _num(metric.get("delta"))
    sign = _metric_sign(metric)
    # Normalise both sides to "up is positive", so one comparison covers higher-is-better and
    # lower-is-better without a second code path that can disagree with the first.
    moved_up = (delta * sign) > 0
    # The floor is the largest of three terms, and the label names the terms that TIED for it
    # rather than the terms that were merely present. A ruler below the arm's own spread did not
    # cause this downgrade, and putting it in the explanation would send the reader off to fix
    # the metric when the arm was the noisy thing.
    floor = at_least
    contributors = ["declared"]
    samples = metric.get("samples") if isinstance(metric.get("samples"), dict) else {}
    std = samples.get("std")
    if std is not None:
        try:
            arm_std = abs(float(std))
            if arm_std > floor:
                floor, contributors = arm_std, ["arm"]
            elif abs(arm_std - floor) < 1e-12:
                contributors.append("arm")
        except (TypeError, ValueError):
            pass
    # The arm's own repeats cannot see the metric's resolution: running the same configuration ten
    # more times on the same splits does not make the splits finer. So the ruler's floor is a third
    # term, and it wins whenever it is the larger of the two. This is the difference between "this
    # delta is large compared to how much this one arm moved" and "this delta is resolvable at all".
    ruler_noise = _num((ruler or {}).get("noise")) if isinstance(ruler, dict) else None
    if ruler_noise is not None and ruler_noise > 0:
        if ruler_noise > floor:
            floor, contributors = ruler_noise, ["ruler"]
        elif abs(ruler_noise - floor) < 1e-12:
            contributors.append("ruler")
    floor_from = "+".join(contributors)

    if not moved_up:
        verdict = "refuted"
        why = (f"predicted the metric would go {'up' if wanted_up else 'down'}, and it moved "
               f"{'down' if delta < 0 else 'not at all'} ({delta:+.4g})."
               + (" A delta of exactly zero is not a weak win; it is no movement."
                  if delta == 0 else ""))
    elif abs(delta) < floor:
        verdict = "partial"
        # The sentence has to name the terms that TIED for the floor, because that sentence is
        # what the reader acts on. A floor set by the arm's own spread is fixed by repeating the
        # arm; one set by the metric's resolution is fixed by measuring more finely. Those are
        # different repairs, so the label is assembled from the terms rather than looked up.
        phrases = {
            "declared": "the declared atLeast",
            "arm": f"this arm's own spread, from {samples.get('n')} samples",
            "ruler": "the metric's calibrated resolution",
        }
        parts = [phrases[c] for c in contributors]
        which = (parts[0] if len(parts) == 1
                 else " and ".join(parts[:-1]) + " and " + parts[-1])
        why = (f"the direction was right ({delta:+.4g}) but the floor was not cleared: "
               f"{abs(delta):.4g} < {floor:.4g} ({which})")
    else:
        verdict = "confirmed"
        why = (f"predicted {'up' if wanted_up else 'down'} by at least {at_least:.4g}, and it "
               f"moved {delta:+.4g}.")

    return {
        "verdict": verdict,
        "why": why,
        "predicted": {"direction": direction, "atLeast": at_least},
        "delta": delta,
        "floor": floor,
        "floorFrom": floor_from,
    }


def goal_of(tree: Any) -> dict[str, Any]:
    """The terminal objective this tree declared, or an empty dict."""
    doc = tree if isinstance(tree, dict) else {}
    goal = doc.get("goal")
    return goal if isinstance(goal, dict) else {}


def curriculum_of(tree: Any) -> list[dict[str, Any]]:
    """The ordered stage ladder, simplest first. Empty when no curriculum was declared."""
    doc = tree if isinstance(tree, dict) else {}
    ladder = doc.get("curriculum")
    if not isinstance(ladder, list):
        return []
    out = []
    for i, stage in enumerate(ladder):
        if isinstance(stage, dict) and stage.get("name"):
            out.append({"index": i, "name": str(stage["name"]),
                        "passesWhen": str(stage.get("passesWhen") or ""),
                        "at": str(stage.get("at") or "")})
    return out


def _stage_index(ladder: list[dict[str, Any]], stage: Any) -> int:
    """Where a stage name sits in the ladder. Unknown names sort after everything known."""
    name = str(stage or "").strip()
    for i, s in enumerate(ladder):
        if s["name"] == name:
            return i
    return len(ladder)


def _validate_goal(where: str, node: dict[str, Any], goal: dict[str, Any]) -> list[str]:
    """A node that measures something other than the objective must say why.

    Same shape as the provenance check: a forced choice, not a forced answer. A node that
    measures a different metric is sometimes exactly right - you cannot improve a score you
    refuse to look at - so the escape is to say so, and the silence is what is unacceptable.
    """
    if not goal:
        return []
    metric = node.get("metric")
    if not isinstance(metric, dict):
        return []
    want = str(goal.get("metric") or "").strip()
    if not want or str(metric.get("name") or "").strip() == want:
        return []
    if str(node.get("offGoalReason") or "").strip():
        return []
    return [f"{where}: this tree optimises {want!r}, but the node measures "
            f"{metric.get('name')!r}. If that is deliberate, say why in offGoalReason - an "
            f"unexplained metric swap is how a long run stops optimising its own goal."]


def _validate_expectation(where: str, node: dict[str, Any]) -> list[str]:
    """A prediction must name a direction and a floor, or it cannot be judged."""
    expect = node.get("expect")
    if expect is None:
        return []
    if not isinstance(expect, dict):
        return [f"{where}: 'expect' must be an object, got {type(expect).__name__}"]
    out: list[str] = []
    direction = str(expect.get("direction") or "").strip().lower()
    if direction not in ("up", "down"):
        out.append(f"{where}: expect.direction must be 'up' or 'down', got "
                   f"{expect.get('direction')!r} - a prediction with no direction is not one")
    if expect.get("atLeast") is None:
        out.append(f"{where}: expect.atLeast is required. A direction with no floor cannot be "
                   f"refuted, because any movement can be read as 'in the right direction'.")
    else:
        try:
            if abs(float(expect["atLeast"])) <= 0:
                out.append(f"{where}: expect.atLeast must be a positive number of scale; 0 "
                           f"would be confirmed by any movement at all")
        except (TypeError, ValueError):
            out.append(f"{where}: expect.atLeast must be a number, got {expect['atLeast']!r}")
    if "metric" in expect and not str(expect.get("metric") or "").strip():
        out.append(f"{where}: expect.metric is blank; omit the field to use the node's own metric")
    return out


def _validate_criteria(where: str, node: dict[str]) -> list[str]:
    """Optional multi-criterion evaluation surface. Domain-agnostic on purpose.

    There is no discipline field and no criteria-set preset. What makes two criteria comparable
    is that they share a name, so a name that drifts shows up in `board` as two separate rows
    rather than silently merging - a visible fault is the safety property here.
    """
    out: list[str] = []
    criteria = node.get("criteria")
    if criteria is None:
        return out
    if not isinstance(criteria, list):
        return [f"{where}: 'criteria' must be a list"]
    seen: set[str] = set()
    for i, c in enumerate(criteria):
        tag = f"{where}: criteria[{i}]"
        if not isinstance(c, dict):
            out.append(f"{tag} must be an object")
            continue
        name = str(c.get("name") or "").strip()
        if not FAMILY_RE.match(name):
            out.append(
                f"{tag}: name {name!r} must match {FAMILY_RE.pattern} - criteria are aggregated by "
                "name, so it has to be a short stable slug, not a sentence"
            )
        elif name in seen:
            out.append(f"{tag}: duplicate criterion name {name!r} within one node")
        else:
            seen.add(name)
        if "value" not in c:
            out.append(f"{tag} is missing 'value'")
        direction = str(c.get("direction") or "higher")
        if direction not in VALID_DIRECTIONS:
            out.append(
                f"{tag}: direction must be one of {', '.join(VALID_DIRECTIONS)}, got {direction!r}"
            )
        efc = c.get("efc")
        if efc is not None:
            if not isinstance(efc, dict):
                out.append(f"{tag}: efc must be an object")
            else:
                for flag in EFC_FLAGS:
                    if not isinstance(efc.get(flag), bool):
                        out.append(f"{tag}: efc.{flag} must be true or false")
        samples = c.get("samples")
        if samples is not None and not isinstance(samples, dict):
            out.append(f"{tag}: samples must be an object")
        # A criterion without a role is indistinguishable from one that does not matter, and
        # a search cannot tell the difference either - it will optimise whatever moved. So the
        # role is stated, and absence means "observe", the weakest possible claim.
        role = c.get("role")
        if role is not None and str(role) not in CRITERION_ROLES:
            out.append(f"{tag}: role must be one of {', '.join(CRITERION_ROLES)}, got {role!r}"
                       f" - primary is what this tree optimises, guard is what must not get "
                       f"worse, observe is only worth knowing")
    return out


def _validate_provenance(where: str, node: dict[str, Any]) -> list[str]:
    """A concluded node must say what it rests on: a stored source, or an explicit local note.

    Not every experiment comes from a paper. A hyperparameter nudged because the previous run
    was slow has no literature behind it, and pretending otherwise would be worse. So the rule is
    a forced choice rather than a forced citation: link at least one source, or say plainly that
    the evidence was a local run. Silence is the only unacceptable answer, because it leaves the
    next reader unable to tell a considered decision from an unconsidered one.

    The sources may be supplied on the node itself at record time. That is deliberate: requiring
    the node to exist before it can be linked, while also refusing a node with no provenance,
    would make the two rules impossible to satisfy together.
    """
    out: list[str] = []
    if node.get("kind") != "experiment" or not node.get("verdict"):
        return out
    refs = node.get("sources")
    if refs:
        if not isinstance(refs, list):
            return [f"{where}: 'sources' must be a list of {source_id} references"]
        seen: set[tuple[str, str]] = set()
        for i, r in enumerate(refs):
            if not isinstance(r, dict) or not str(r.get("sourceId") or "").strip():
                out.append(f"{where}: sources[{i}] needs a sourceId")
                continue
            sid = str(r["sourceId"]).strip()
            rel = str(r.get("relation") or "supports")
            # the same paper may legitimately SUPPORT and CONTRADICT the same claim, so a
            # duplicate is the same (source, relation) pair, not merely the same source
            if (sid, rel) in seen:
                out.append(f"{where}: sources[{i}] repeats {sid} as '{rel}'")
            seen.add((sid, rel))
            # a typo'd id would leave a link that silently resolves to nothing, which is
            # exactly the failure this store exists to prevent
            if not source_exists(sid):
                out.append(
                    f"{where}: sources[{i}] points at {sid!r}, which is not in the evidence "
                    "store. Add it with kaggle_sources action=\"add\" first."
                )
        return out
    if str(node.get("evidence") or "").strip() == "local-only":
        return out
    return [
        f"{where}: a concluded node needs provenance - attach at least one source, or set "
        "evidence=\"local-only\" to say the evidence was a local run rather than a citation. "
        "Unstated provenance is what makes a review impossible."
    ]


def source_exists(source_id: str) -> bool:
    """Whether an id really resolves in the evidence store. Never raises."""
    try:
        import sources as _s
        return _s.get(str(source_id)) is not None
    except Exception:  # noqa: BLE001 - validation must not depend on the store being readable
        return False


LIST_NODE_FIELDS = ("targets", "sources", "criteria")
# Values that mean "no parent". The MCP transport has been observed to drop a null entirely,
# and a caller who sends a bare false, or the string "none", means the same thing.
NO_PARENT = ("", "none", "null", "unset", "false")


def _normalize_list(value: Any) -> list[Any]:
    """Accept the shapes a round trip through the tool boundary actually produces.

    A list sent as ["code"] has been observed arriving as a bare string, and a list sent inside
    an object arriving one level deeper than it was sent. A contract that breaks on that is not
    a contract. The node is repaired on the way in rather than rejected, because a node an
    agent cannot record is an experiment it cannot run.
    """
    if value is None:
        return []
    if isinstance(value, list):
        flat: list[Any] = []
        for item in value:
            if isinstance(item, list):
                flat.extend(item)
            else:
                flat.append(item)
        return flat
    if isinstance(value, tuple):
        return _normalize_list(list(value))
    return [value]


def normalize_node(node: Any) -> Any:
    """Make a node survive the tool boundary. Idempotent."""
    if not isinstance(node, dict):
        return node
    out = dict(node)
    # targets is a list of strings; sources and criteria are lists of OBJECTS, and coercing
    # those to str would destroy the very references the repair is meant to preserve. The
    # evidence-chain checks caught that one.
    for field in LIST_NODE_FIELDS:
        if field in out:
            items = _normalize_list(out[field])
            out[field] = [x if isinstance(x, str) else str(x) for x in items] \
                if field == "targets" else items
    parent = out.get("parent")
    if parent is not None and not isinstance(parent, str):
        out["parent"] = None if parent is False or parent == 0 else str(parent)
    if isinstance(out.get("parent"), str) and out["parent"].strip().lower() in NO_PARENT:
        out["parent"] = None
    metric = out.get("metric")
    if isinstance(metric, dict) and isinstance(metric.get("metric"), dict):
        out["metric"] = metric["metric"]          # the same accidental nesting
    recipe = _normalize_recipe(out.get("recipe"))
    if recipe is not None:
        out["recipe"] = recipe
    # Every criterion carries its role, so "which numbers matter" is answered by the data
    # rather than inferred from which ones moved. An absent role is the weakest claim
    # (observe), not an exemption.
    criteria = out.get("criteria")
    if isinstance(criteria, list):
        for c in criteria:
            if isinstance(c, dict) and not str(c.get("role") or "").strip():
                c["role"] = DEFAULT_CRITERION_ROLE
    # A factor set is repaired rather than rejected, like a list: the tool boundary collapses
    # single-element lists to bare strings, and a factors list that arrives mangled would
    # otherwise make an ablation unreadable exactly when it was being recorded.
    if out.get("factors") is not None:
        out["factors"] = sorted({str(x).strip().lower()
                                 for x in _normalize_list(out["factors"])
                                 if str(x or "").strip()})
    intent = out.get("factorsIntent")
    if intent is not None:
        out["factorsIntent"] = str(intent).strip().lower() or "one-factor"
    controls = out.get("controls")
    if isinstance(controls, dict):
        out["controls"] = {k: controls[k] for k in CONTROL_KEYS if controls.get(k) not in (None, "")}
    return out


def _comparison_parent(nodes: dict[str, Any], node: Any) -> Optional[str]:
    """The run this one is actually measured against, skipping over declarations.

    A declaration is an announcement, not an arm: it carries the factors the run WILL have,
    because that is the whole point of announcing it before the run. Measuring the result
    against its own declaration therefore always comes out as "nothing changed" - which the
    symmetric-difference gate correctly reads as an undeclared repeat, and refuses every
    settled node in the tree. The run is measured against whatever the declaration was
    measured against.
    """
    seen: set[str] = set()
    pid = (node or {}).get("parent") if isinstance(node, dict) else None
    while pid and pid in nodes and pid not in seen:
        seen.add(pid)
        candidate = nodes.get(pid) or {}
        if str(candidate.get("status") or "") != "planned":
            return pid
        pid = candidate.get("parent")
    return None


def _validate_factors(where: str, node: dict[str, Any], nodes: dict[str, Any]) -> list[str]:
    """Check that a run changed one factor, and that its comparison against its parent holds.

    These are the checks the word "and" cannot do. A node can describe a two-factor change in a
    single confident sentence, and the conjunction check will never see it; the factor sets
    will, because the symmetric difference comes out at two. Recording both arms as factor sets
    is also the only way the interaction term is ever computable, so this is not just a guard -
    it is the thing that makes the ablation table possible.

    Every refusal here has a named way out, because the honest exceptions are real: a factorial
    arm deliberately changes two factors, a repeat run deliberately changes none, and retraining
    instead of re-evaluating is a control change on purpose. What is refused is silence - the
    node that is one of those things and never says so.
    """
    out: list[str] = []
    factors = _factor_set(node)
    intent = str(node.get("factorsIntent") or "one-factor").strip().lower()
    if intent not in VALID_INTENT:
        out.append(f"{where}: factorsIntent must be one of {', '.join(sorted(VALID_INTENT))}, "
                   f"got {intent!r}. It is how a run says it deliberately changed two factors "
                   f"(factorial) or none at all (repeat), instead of leaving that to be guessed.")
    if factors is not None:
        for f in sorted(factors):
            if not FACTOR_RE.match(f):
                out.append(f"{where}: factor {f!r} must match {FACTOR_RE.pattern} end to end - "
                           f"factors are joined and compared as slugs, so keep them short and "
                           f"stable, and a slug is one token")
    controls = node.get("controls")
    if controls is not None:
        if not isinstance(controls, dict):
            out.append(f"{where}: controls must be an object with any of "
                       f"{', '.join(CONTROL_KEYS)}")
        else:
            for k in controls:
                if k not in CONTROL_KEYS:
                    out.append(f"{where}: unknown control {k!r}; the ones that actually move a "
                               f"delta are {', '.join(CONTROL_KEYS)}")
            retrain = controls.get("retrain")
            if retrain is not None and str(retrain) not in VALID_RETRAIN:
                out.append(f"{where}: controls.retrain must be one of "
                           f"{', '.join(VALID_RETRAIN)}, got {retrain!r}. Retraining measures a "
                           f"component's unique contribution; re-evaluating measures how much "
                           f"the trained solution leans on it, and the two answer different "
                           f"questions.")
    # the arithmetic, when both sides are checkable. The parent is the last CONCLUDED run:
    # a declaration carries the factors the run will have, so measuring against it would make
    # every settled experiment look like a run that changed nothing.
    parent_id = _comparison_parent(nodes, node)
    if parent_id and parent_id in nodes:
        parent = nodes[parent_id]
        delta = factor_delta(parent, node)
        if delta is not None and delta["changed"] > 1 and intent != "factorial":
            out.append(
                f"{where}: this run differs from its parent {parent_id} in {delta['changed']} "
                f"factors (added {delta['added'] or '[]'}, removed {delta['removed'] or '[]'}), "
                f"so it measured the combination rather than one thing. A node changes one "
                f"thing - split it, or record it as factorsIntent='factorial' so the pair is "
                f"recorded as a deliberate arm instead of passing for a single-factor result."
            )
        if delta is not None and delta["changed"] == 0 and intent != "repeat":
            out.append(
                f"{where}: this run has the same factors as its parent {parent_id}, so its "
                f"delta measures the seed and the run-to-run spread, not any factor. That is a "
                f"worthwhile measurement and it has a name: factorsIntent='repeat'. Without it "
                f"the repeat reads as a result about the pipeline."
            )
        mism = _control_diff(parent, node, missing_is_a_problem=False)
        if mism and not str(node.get("confoundReason") or "").strip():
            out.append(
                f"{where}: this run and its parent {parent_id} disagree about "
                f"{'; '.join(mism)}. A delta between them is not attributable to the factor, so "
                f"the ablation table will refuse to call it one. If the control change is the "
                f"experiment (re-evaluating vs retraining answers different questions), say so "
                f"in confoundReason."
            )
    return out


def _factor_set(node: Any) -> Optional[frozenset]:
    """The factors a run had, or None when the run never recorded any.

    An empty list is NOT the same as an absent one. factors=[] is the bare model with every
    component switched off - the arm every other arm is measured against - while an absent
    key means the run simply never said. Collapsing the two deletes the baseline from its own
    ablation table and, worse, returns None from factor_delta for every run whose parent is
    that baseline, which switches the symmetric-difference check off precisely where a
    one-factor-at-a-time ladder starts.
    """
    if not isinstance(node, dict):
        return None
    if node.get("factors") is None:
        return None
    raw = node.get("factors")
    items = raw if isinstance(raw, list) else [raw]
    return frozenset(str(x).strip().lower() for x in items if str(x or "").strip())


def factor_delta(parent: Any, child: Any) -> Optional[dict[str, Any]]:
    """Exactly which factors turned on and which turned off between two runs.

    Returning None means "not checkable" - one side never said what it had. Returning a dict
    with more than one changed factor is the whole point: it is the mechanical version of
    "this node changes one thing", and unlike the conjunction-word check it cannot be defeated
    by wording a pipeline in one sentence.
    """
    before, after = _factor_set(parent), _factor_set(child)
    if before is None or after is None:
        return None
    added = sorted(after - before)
    removed = sorted(before - after)
    return {"added": added, "removed": removed, "changed": len(added) + len(removed)}


def _control_diff(na: dict[str, Any], nb: dict[str, Any],
                  missing_is_a_problem: bool = True) -> list[str]:
    """Where two runs were not comparable because their controls differ.

    A delta between two arms is only attributable to the factor that differs if everything
    else did too. Seeds, budget, eval set, retrain policy and the dataset itself are the
    five that actually move numbers in practice, so those are the five compared - and a run
    that recorded no controls is compared as "unknown" rather than assumed to match, because
    assuming that is exactly how a table becomes a table of losses that could belong to any
    system.

    missing_is_a_problem is the difference between the two callers, and conflating them was a
    real bug: a tree reads as un-attributable when controls were never recorded, which is the
    normal state of most trees, but a WRITE must not refuse a node for silence about something
    the parent did not record either. So the table reports the unknown, and only a genuine
    disagreement between two recorded values blocks a write.

    The same distinction applies WITHIN a recorded pair, and getting it wrong is how a newly
    added control key would have broken every tree that predates it. Silence on both sides is
    not a disagreement: two arms that both fail to name their dataset are equally unknown, and
    reporting that as a mismatch claims a difference nobody observed. Only ONE side being
    silent is a real finding - one run says what it read and the other does not, so the two
    are not known to be comparable. That is the branch that fires for `data` on a tree whose
    nodes predate it, which is why it cannot be folded into the both-absent case.
    """
    ca, cb = na.get("controls"), nb.get("controls")
    if not isinstance(ca, dict) or not isinstance(cb, dict):
        if not missing_is_a_problem:
            return []
        return ["(controls were not recorded on both runs, so the delta is not attributable)"]
    out = []
    for key in CONTROL_KEYS:
        va, vb = ca.get(key), cb.get(key)
        if va is None and vb is None:
            continue                      # neither arm says: unknown, not a disagreement
        if va is None or vb is None:
            if missing_is_a_problem:
                out.append(f"{key} (recorded on one run but not the other)")
        elif str(va) != str(vb):
            out.append(f"{key}: {va!r} vs {vb!r}")
    return out


def control_mismatches(nodes: dict[str, Any], a: str, b: str) -> list[str]:
    """Control differences between two nodes of a tree, by id."""
    return _control_diff(nodes.get(a) or {}, nodes.get(b) or {})


def ablation_table(tree: dict[str, Any]) -> dict[str, Any]:
    """The ablation table, built from recorded factor sets rather than re-derived from prose.

    The primitive here is an EDGE: any two runs whose factor sets differ by exactly one, with
    the delta attributed to that one factor. An edge is direction-free, which is the only way
    one table can serve every ablation design - add-one-in reads its edges upward, leave-one-out
    reads the same edges downward, one-factor-at-a-time is the ladder of them, and a factorial
    family is a grid they cross. Picking a baseline and measuring everything against it only
    serves the first of those, and on a leave-one-out family it silently reports every delta
    against an arbitrary pair instead of against the full configuration.

    Two things this refuses to do. It will not report a delta whose two arms disagreed about
    seed, budget, eval set, retrain policy or the dataset itself, because that number belongs to
    more than one cause. And it will not report an interaction from a triple that is not three
    distinct arms:
    with a bare baseline and no standalone B, "A+B" is reachable as both the pair and the
    solo arm, and the arithmetic then prints a confident 0.00 interaction from a missing run.
    """
    inner = _current(tree)
    nodes = inner.get("nodes") or {}
    rows: list[dict[str, Any]] = []
    for nid, node in nodes.items():
        if not isinstance(node, dict) or is_abandoned(node):
            continue
        if str(node.get("status") or "") == "planned":
            continue          # an announcement, not a run: it has no result to place
        factors = _factor_set(node)
        metric = node.get("metric")
        if factors is None or not isinstance(metric, dict):
            continue
        rows.append({
            "node": nid,
            "factors": sorted(factors),
            "set": factors,
            "score": _metric_sign(metric) * _num(metric.get("result")),
            "metric": metric.get("name"),
            "controls": node.get("controls"),
            "verdict": node.get("verdict"),
        })
    if not rows:
        return {"ok": True, "rows": [], "baseline": None, "edges": [], "gaps": [],
                "interactions": [], "confounds": [], "repeats": [], "noise": None, "note": (
                    "no run recorded a `factors` set, so there is no ablation table to build. A "
                    "run declares factors=[...] - [] for the bare model - and the comparison "
                    "becomes arithmetic instead of a recollection.")}

    # Two runs claiming the same configuration are a repeat, not a new arm. They are also the
    # only free measurement of noise in the whole system, which is what makes it possible to
    # tell a real delta from a lucky seed later.
    groups: dict[frozenset, list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(r["set"], []).append(r)
    repeats, noise = [], None
    for fs, group in groups.items():
        if len(group) < 2:
            continue
        scores = [g["score"] for g in group]
        spread = max(scores) - min(scores)
        repeats.append({"factors": sorted(fs) or "(none)",
                        "nodes": [g["node"] for g in group],
                        "spread": round(spread, 6), "spreadLabel": f"{spread:+.4g}"})
        noise = spread if noise is None else max(noise, spread)

    # The bare configuration is the baseline when it was run. When it was not, that is said
    # plainly instead of being replaced by the shortest arm, because "everything is compared
    # against {a}" is a different and much weaker claim than "compared against nothing".
    bare = groups.get(frozenset())
    if bare:
        base = sorted(bare, key=lambda r: r["node"])[0]
        base_score = base["score"]
    else:
        base, base_score = None, None
    for r in rows:
        r["delta"] = None if base is None else round(r["score"] - base_score, 6)
        r["deltaLabel"] = "-" if base is None else f"{r['score'] - base_score:+.4g}"
        r["versusBaseline"] = ("no bare-model arm was run, so there is no baseline to measure "
                               "against; read the edges below instead" if base is None else
                               base["node"] if r["node"] != base["node"] else "(this is the baseline)")

    index: dict[frozenset, dict[str, Any]] = {}
    for fs, group in groups.items():
        index[fs] = sorted(group, key=lambda r: r["node"])[0]

    # Edges: every pair isolating exactly one factor, in whichever direction it was run.
    order = sorted(rows, key=lambda r: (len(r["factors"]), r["node"]))
    edges: list[dict[str, Any]] = []
    for i, r1 in enumerate(order):
        for r2 in order[i + 1:]:
            d = factor_delta(r1, r2)
            if d is None or d["changed"] != 1:
                continue
            factor = (d["added"] or d["removed"])[0]
            low, high = (r1, r2) if d["added"] else (r2, r1)
            delta = high["score"] - low["score"]
            mism = control_mismatches(nodes, low["node"], high["node"])
            # Direction is read off the parent link, not off which row happened to sort first.
            # Sorted order makes every leave-one-out family look like a set of additions, which
            # reads as the opposite of how the runs were actually declared - and the difference
            # is the whole content of "+b on top of a" versus "a without b".
            child, other = high["node"], low["node"]
            # the arms must be looked up as NODES here: a table row carries the score and the
            # factor set but no parent link, so asking it for a parent silently returns None
            # and every edge falls back to "either".
            if _comparison_parent(nodes, nodes.get(child) or {}) == other:
                direction = "add"        # the child is the richer arm: it turned the factor on
            elif _comparison_parent(nodes, nodes.get(other) or {}) == child:
                direction = "drop"       # the child is the poorer arm: it turned the factor off
            else:
                direction = "either"
            edges.append({
                "factor": factor,
                "direction": direction,
                "without": low["node"], "with": high["node"],
                "withoutFactors": low["factors"], "withFactors": high["factors"],
                "delta": round(delta, 6), "deltaLabel": f"{delta:+.4g}",
                "kind": ("indistinguishable" if noise is not None and abs(delta) <= noise
                         else "positive" if delta > 0 else "negative"),
                "attributable": not mism,
                "confound": mism,
            })
    edges.sort(key=lambda e: (e["factor"], e["direction"], e["without"]))

    # Interactions need four DISTINCT arms: the reference, each part alone, and the pair. The
    # distinctness is the point - when the bare arm is missing, {a,b} is reachable as both the
    # pair and a "solo", and summing those gives a flat zero that looks like independence.
    interactions: list[dict[str, Any]] = []
    all_factors = sorted({f for r in rows for f in r["factors"]})
    for a in all_factors:
        for b in all_factors:
            if a >= b:
                continue
            sa, sb, sab = index.get(frozenset({a})), index.get(frozenset({b})), index.get(frozenset({a, b}))
            if not (sa and sb and sab):
                continue
            for ref_fs in sorted(groups, key=lambda s: (len(s), sorted(s))):
                if a in ref_fs or b in ref_fs:
                    continue
                ref = index[ref_fs]
                arms = [ref, sa, sb, sab]
                mism = sorted({m for other in arms[1:] for m in control_mismatches(nodes, ref["node"], other["node"])})
                joint = sab["score"] - ref["score"]
                total = (sa["score"] - ref["score"]) + (sb["score"] - ref["score"])
                effect = joint - total
                interactions.append({
                    "factors": [a, b], "reference": sorted(ref_fs) or "(bare)",
                    "referenceNode": ref["node"],
                    "joint": round(joint, 6), "sumOfParts": round(total, 6),
                    "interaction": round(effect, 6), "interactionLabel": f"{effect:+.4g}",
                    "kind": ("indistinguishable" if noise is not None and abs(effect) <= noise
                             else "synergistic" if effect > 0 else "redundant"),
                    "attributable": not mism, "confound": mism,
                })
                break  # the simplest reference is the one a reader checks first

    # What an edge actually establishes is a CONDITIONAL effect: the delta for factor f was
    # measured with whatever the two arms had in common switched on. Reading that number as the
    # effect of f is the add-one-in error - "+b on top of a" is not "+b", and b may work only
    # because a is there. So a factor is reported with the contexts it was measured in, and a
    # factor that flips sign between two contexts is not summarised as one number at all.
    contexts: dict[str, list[dict[str, Any]]] = {}
    for e in edges:
        common = frozenset(e["withoutFactors"]) & frozenset(e["withFactors"])
        contexts.setdefault(e["factor"], []).append({"context": sorted(common), **e})
    gaps: list[str] = []
    conditional: list[dict[str, Any]] = []
    for f in all_factors:
        seen = contexts.get(f) or []
        if not seen:
            gaps.append(
                f"{f!r} never ran in a comparison that isolates it. A gain credited to it is a "
                f"gain of whatever it was combined with, not of {f!r} alone - and those are "
                f"different claims with different consequences. The arm that isolates it is this "
                f"configuration with {f!r} added, or with {f!r} removed; either one does it."
            )
            continue
        bare = [s for s in seen if not s["context"]]
        if not bare:
            tops = sorted({"+".join(s["context"]) for s in seen})
            signs = sorted({s["delta"] > 0 for s in seen})
            drops = {s["direction"] for s in seen}
            shape = ("leave-one-out" if drops == {"drop"} else
                     "add-one-in" if drops == {"add"} else "mixed")
            if shape == "leave-one-out":
                note = (
                    f"{f!r} is only ever measured by removing it, so the delta is the cost of "
                    f"removing {f!r} while {'+'.join(tops)} stayed on. These deltas do not add "
                    f"up: the cost of removing everything at once is a separate measurement, "
                    f"and it is usually larger than their sum."
                )
            elif shape == "add-one-in":
                note = (
                    f"{f!r} was only ever added on top of {'+'.join(tops)}, so its delta is its "
                    f"effect in that company, not its effect alone - {f!r} may work only because "
                    f"the rest is there. One arm with {f!r} on the bare model is what turns this "
                    f"into a statement about {f!r}."
                )
            else:
                note = (
                    f"{f!r} was measured both by adding and by removing it, on "
                    f"{', '.join(tops)}. Say which number belongs to which comparison before "
                    f"quoting either."
                )
            if len(signs) > 1:
                note += (" It does not even hold one sign across those contexts, so no single "
                         "number describes it.")
            conditional.append({
                "factor": f, "shape": shape, "contexts": tops,
                "deltas": sorted({s["deltaLabel"] for s in seen}),
                "signStable": len(signs) == 1, "note": note,
            })

    # A factorial family is 2^k runs. Naming the size stops a five-factor grid from being
    # discovered halfway through, one arm at a time, at the cost of the whole quota.
    k = len(all_factors)
    expected = 2 ** k
    size = {
        "factors": k, "fullFactorialArms": expected, "recordedArms": len(groups),
        "complete": len(groups) == expected,
        "note": (f"{k} factors need {expected} arms for a full factorial; {len(groups)} are "
                 f"recorded. Partial coverage is fine for an add-one-in or leave-one-out "
                 f"design - what it forbids is reading an interaction out of it."
                 if len(groups) < expected else
                 f"all {expected} arms of a {k}-factor factorial are present."),
    }
    if k > 4 and not size["complete"]:
        size["note"] += (f" {k} factors is past the point where a full factorial is "
                         f"practical; the {expected} runs it needs are usually better spent on "
                         f"the arms an edge says are missing.")

    confounds = [{"a": e["without"], "b": e["with"], "factor": e["factor"], "differ": e["confound"]}
                 for e in edges if not e["attributable"]]
    unattributed = [i for i in interactions if not i["attributable"]]

    return {
        "ok": True,
        "rows": sorted(rows, key=lambda r: (len(r["factors"]), r["node"])),
        "baseline": ({"node": base["node"], "factors": base["factors"], "score": base["score"]}
                     if base else None),
        "edges": edges,
        "interactions": interactions,
        "gaps": gaps,
        "conditional": conditional,
        "confounds": confounds,
        "repeats": repeats,
        "noise": noise,
        "size": size,
        "note": (
            "these edges were not comparable: their arms disagreed about seed, budget, eval "
            "set, retrain policy or the dataset, so the delta belongs to more than one cause and "
            "the table names each one instead of printing a number."
            if confounds else
            "every edge in this table has matching controls, so each delta belongs to the one "
            "factor it differs by."),
        "interactionNote": (
            "an interaction needs four distinct arms - a reference, each factor alone, and the "
            "pair. Computed from a degenerate triple it prints a flat zero that looks exactly "
            "like independence, which is why a missing arm is reported as a gap instead."),
        "noiseNote": (
            f"repeated configurations disagree by up to {noise:.4g}, so a delta smaller than "
            f"that is not evidence of anything. Edges inside that band are marked "
            f"indistinguishable." if noise is not None else
            "no configuration was run twice, so this table has no noise estimate of its own: "
            "treat every delta as unverified until one arm is repeated."),
    }


def ablation_table_response(competition: str) -> dict[str, Any]:
    """Render the ablation table for a competition."""
    res = ablation_table(load(competition))
    if not res.get("rows"):
        return {"content": [{"type": "text", "text": f"ablation table\n{res.get('note')}"}],
                "isError": False}
    lines: list[str] = []
    base = res.get("baseline")
    if base:
        lines.append(f"baseline: {base['node']} "
                     f"({'+'.join(base['factors']) or 'bare model, every factor off'}) "
                     f"score {base['score']:.4g}")
    else:
        lines.append("baseline: none - no bare-model arm was run, so nothing here is a delta "
                     "against nothing; read the edges instead")
    lines += ["", f"{'config':<24}{'score':<10}{'delta':<10}node"]
    for r in res["rows"]:
        label = "+".join(r["factors"]) or "(bare)"
        lines.append(f"{label:<24}{r['score']:<10.4g}{r['deltaLabel']:<10}{r['node']}")
    if res.get("edges"):
        lines += ["", "edges (each isolates exactly one factor, in the direction it was run):"]
        for e in res["edges"]:
            arrow = "add" if e["direction"] == "add" else "drop"
            flag = "" if e["attributable"] else "  NOT ATTRIBUTABLE: " + "; ".join(e["confound"])
            lines.append(f"  {e['factor']:<14}{e['deltaLabel']:<10}{arrow} "
                         f"{e['without']} -> {e['with']}  [{e['kind']}]{flag}")
    if res.get("interactions"):
        lines += ["", "interactions (joint minus the sum of the parts):"]
        for it in res["interactions"]:
            flag = "" if it["attributable"] else "  NOT ATTRIBUTABLE: " + "; ".join(it["confound"])
            lines.append(f"  {'+'.join(it['factors'])} on {it['reference']}: "
                         f"{it['interactionLabel']} ({it['kind']}: joint {it['joint']:+.4g} vs "
                         f"parts {it['sumOfParts']:+.4g}){flag}")
        lines.append(f"  {res['interactionNote']}")
    if res.get("gaps"):
        lines += ["", "the comparison set is incomplete:"]
        lines += [f"  {g}" for g in res["gaps"]]
    if res.get("conditional"):
        lines += ["", "effects measured only in company:"]
        for c in res["conditional"]:
            lines.append(f"  {c['factor']} ({c['shape']}): {c['note']}")
    if res.get("confounds"):
        lines += ["", "these arms were not comparable:"]
        for c in res["confounds"]:
            lines.append(f"  {c['a']} vs {c['b']} ({c['factor']}): " + "; ".join(c["differ"]))
    if res.get("repeats"):
        lines += ["", "repeated configurations (free noise):"]
        for rp in res["repeats"]:
            lines.append(f"  {rp['factors']}: {' vs '.join(rp['nodes'])} spread {rp['spreadLabel']}")
    if res.get("size"):
        lines += ["", f"size: {res['size']['note']}"]
    lines += ["", res["note"], res.get("noiseNote", "")]
    return {"content": [{"type": "text", "text": "\n".join(lines)}], "isError": False}


def _normalize_recipe(value: Any) -> Optional[dict[str, Any]]:
    """Coerce a node's recipe into {engine, command, ref, step} so reuse is mechanical.

    A recipe is how a run was actually launched, kept on the node so the next run starts from
    the version that worked instead of re-deriving the command. It is repaired on the way in
    for the same reason a node is: a field the caller cannot get through the boundary is a
    field that will be quietly absent on the node that needed it. Returns None when there is
    no recipe at all - absence is fine, a broken one is not, and validate() says so.
    """
    if value is None:
        return None
    if isinstance(value, str):
        import json as _json
        try:
            value = _json.loads(value)
        except (ValueError, TypeError):
            return {"engine": "", "command": [], "raw": value}  # validate() will refuse it
    if not isinstance(value, dict):
        return {"engine": "", "command": [], "raw": value}
    out = {
        "engine": str(value.get("engine") or "").strip(),
        "command": [str(c) for c in (_normalize_list(value.get("command")) or [])]
        if value.get("command") is not None else [],
    }
    for key in ("ref", "step", "folder", "logPath"):
        if value.get(key) not in (None, ""):
            out[key] = str(value[key])
    return out


def validate(tree: dict[str, Any]) -> list[str]:
    """Return every structural problem. Empty list means it is sound.

    Written as a whole-tree pass rather than a per-node check on write, so it also works on a
    tree that was hand-edited or arrived from another agent: the point is to be able to ask
    "is this tree trustworthy right now", not only "was my own write well-formed".
    """
    problems: list[str] = []
    doc = tree
    inner = _current(tree)
    nodes = inner.get("nodes") or {}
    if not isinstance(nodes, dict):
        return ["'nodes' must be an object"]

    base = inner.get("base") or {}
    base_id = base.get("id") if isinstance(base, dict) else None

    problems.extend(_validate_document(doc, problems))
    held_out = ((doc.get("anchor") or {}) if isinstance(doc.get("anchor"), dict) else {}).get("heldOut")
    goal = goal_of(doc)

    for nid, node in nodes.items():
        where = f"node '{nid}'"
        if not isinstance(node, dict):
            problems.append(f"{where}: must be an object")
            continue

        for field in COMMON_REQUIRED:
            if field not in node:
                problems.append(f"{where}: missing required field '{field}'")

        kind = node.get("kind")
        if kind not in NODE_KINDS:
            problems.append(
                f"{where}: kind must be one of {', '.join(NODE_KINDS)}, got {kind!r}"
            )

        verdict = node.get("verdict")
        if verdict is not None and verdict not in VERDICTS:
            problems.append(
                f"{where}: verdict must be one of {', '.join(VERDICTS)}, got {verdict!r}"
            )

        reason = node.get("reason")
        if verdict is not None and _norm_reason(reason) in EMPTY_REASONS:
            problems.append(
                f"{where}: reason says nothing ({reason!r}). The reason is what the next "
                "iteration reads, so 'better' is not a verdict."
            )

        parent = node.get("parent")
        if parent is not None:
            if not isinstance(parent, str) or parent not in nodes:
                problems.append(
                    f"{where}: parent {parent!r} is not a node in this tree"
                )
            elif parent == nid:
                problems.append(f"{where}: parent is itself")

        if kind == "experiment":
            # A declaration states what is about to be run, not what came back, so it owes no
            # metric and no verdict. What it still owes is the same identity every experiment
            # owes — a single change, a hypothesis, and an operator to attribute it to later.
            required = ("change", "hypothesis") if is_planned(node) else EXPERIMENT_REQUIRED
            for field in required:
                if field not in node:
                    problems.append(f"{where}: experiment missing required field '{field}'")
            for field in SEARCH_REQUIRED:
                if field not in node:
                    problems.append(
                        f"{where}: experiment missing '{field}'. Without it this node cannot be "
                        "scored for novelty or attributed to an operator, so it cannot inform "
                        "the next selection."
                    )
            operator = node.get("operator")
            if operator is not None and operator not in OPERATORS:
                problems.append(
                    f"{where}: operator must be one of {', '.join(OPERATORS)}, got {operator!r}"
                )
            family = node.get("family")
            if family is not None and not FAMILY_RE.match(str(family)):
                problems.append(
                    f"{where}: family {family!r} must match {FAMILY_RE.pattern} - it is the label "
                    "novelty is computed from, so it is a short slug, not a sentence"
                )
            change = node.get("change")
            conj = _has_conjunction(change)
            if conj:
                problems.append(
                    f"{where}: change contains '{conj}', so it is two experiments. "
                    "One node changes one thing."
                )
            problems.extend(_validate_factors(where, node, nodes))
            # A recipe is optional, but a half-written one is worse than none: it looks
            # reusable and is not. So its presence is checked, not its content's absence.
            if "recipe" in node:
                rcp = node.get("recipe")
                if not isinstance(rcp, dict):
                    problems.append(
                        f"{where}: recipe must be an object, got {type(rcp).__name__}"
                    )
                elif not rcp.get("engine") and not rcp.get("command") and not rcp.get("ref"):
                    problems.append(
                        f"{where}: recipe says nothing about how the run was launched. Give it "
                        "an engine and a command, or a ref - a recipe the next run cannot "
                        "reuse is a note, and notes do not belong here."
                    )
            metric = node.get("metric")
            if isinstance(metric, dict):
                for field in ("name", "parent", "result", "delta"):
                    if field not in metric:
                        problems.append(
                            f"{where}: metric missing '{field}' - a gain measured against a "
                            "different baseline is a false gain"
                        )
                # A result with no recorded standing cannot be placed against the field, which is
                # the whole reason the score is recorded rather than only compared locally.
                for field in ("rank", "rankSource"):
                    if field not in metric:
                        problems.append(
                            f"{where}: metric missing '{field}'. Record where this score stands "
                            "against the field, not just its local delta."
                        )
                # Repeated measurement makes the noise band computable instead of a matter of
                # opinion. BioAgent Bench measured Jaccard 0.43 across four identical runs, so
                # "is this delta even real" is a first-class question, not a footnote.
                samples = metric.get("samples")
                if samples is not None:
                    if not isinstance(samples, dict):
                        problems.append(f"{where}: metric.samples must be an object")
                    else:
                        if not isinstance(samples.get("n"), int) or samples["n"] < 1:
                            problems.append(f"{where}: metric.samples.n must be a positive integer")
                        if "mean" not in samples:
                            problems.append(f"{where}: metric.samples is missing 'mean'")
                        try:
                            if abs(float(samples["mean"]) - float(metric.get("result"))) > 1e-9:
                                problems.append(
                                    f"{where}: metric.result must equal metric.samples.mean when "
                                    "samples are given, or the noise band is measured against a "
                                    "different number than the one recorded"
                                )
                        except (TypeError, ValueError, KeyError):
                            problems.append(f"{where}: metric.samples.mean is not a number")
                # The protected anchor: a node may not be scored on the set held back from
                # evolution. Three independent teams converged on this; it is a precondition for
                # any claim that a replay score means anything.
                if held_out:
                    for field in ("split", "rankSource"):
                        textval = str(metric.get(field) or "")
                        if held_out in textval:
                            problems.append(
                                f"{where}: metric.{field} references the held-out anchor "
                                f"{held_out!r}. The evaluation set must stay disjoint from the "
                                "evolution set."
                            )
            elif metric is not None:
                problems.append(f"{where}: metric must be an object")

            # A refuted node that does not say which layer broke teaches the next iteration
            # nothing. Over 60% of measured harness failures were output-contract and
            # tool/recovery problems, i.e. harness bugs, not reasoning bugs.
            if node.get("verdict") == "revert":
                layer = node.get("failureLayer")
                if layer is None:
                    problems.append(
                        f"{where}: verdict 'revert' requires failureLayer. A negative score with "
                        "no layer says nothing about whether to change the tool or the skill."
                    )
                elif layer not in FAILURE_LAYERS:
                    problems.append(
                        f"{where}: failureLayer must be one of {', '.join(FAILURE_LAYERS)}, "
                        f"got {layer!r}"
                    )
                elif layer == "other" and not str(node.get("note") or "").strip():
                    problems.append(f"{where}: failureLayer 'other' requires a note saying which")

            problems.extend(_validate_criteria(where, node))
            problems.extend(_validate_provenance(where, node))
            problems.extend(_validate_expectation(where, node))
            problems.extend(_validate_goal(where, node, goal))

        if kind == "research":
            for field in RESEARCH_REQUIRED:
                if field not in node:
                    problems.append(f"{where}: research node missing required field '{field}'")
            targets = node.get("targets")
            if isinstance(targets, list):
                if not targets:
                    problems.append(f"{where}: research node must name at least one target")
                for t in targets:
                    if t not in RESEARCH_TARGETS:
                        problems.append(
                            f"{where}: target {t!r} is not one of "
                            f"{', '.join(RESEARCH_TARGETS)}"
                        )
            elif targets is not None:
                problems.append(
                    f"{where}: targets must be a list, but a "
                    f"{type(targets).__name__} arrived ({targets!r}). Send a plain string "
                    f"like \"code\" or a list; both are accepted."
                )
            # A research node that read a run's log is a diagnosis, and then it must say which
            # log. Without that it is an opinion wearing a citation, and the next experiment
            # would be "informed" by something nobody can re-read.
            if node.get("bottleneck") is not None:
                if not (node.get("logRef") or node.get("logPath")):
                    problems.append(
                        f"{where}: a node that names a bottleneck must name the log it read "
                        f"(logRef for a Kaggle kernel, logPath for a local run), or the "
                        f"finding cannot be re-checked"
                    )
                dl = node.get("layer")
                if dl is not None and dl not in FAILURE_LAYERS:
                    problems.append(
                        f"{where}: diagnosis layer must be one of "
                        f"{', '.join(FAILURE_LAYERS)}, got {dl!r}"
                    )
            opens = node.get("opens")
            if isinstance(opens, str) and not opens.strip():
                problems.append(
                    f"{where}: 'opens' is empty. A research node has to say what it changed, "
                    "or it is a note, not a node."
                )

    # cycle detection over the parent chain
    for nid in nodes:
        cur, hops, seen = nodes[nid].get("parent") if isinstance(nodes[nid], dict) else None, 0, set()
        while cur and isinstance(cur, str) and cur in nodes and hops < len(nodes) + 2:
            if cur in seen:
                problems.append(f"cycle in parent chain reaching '{nid}' via '{cur}'")
                break
            seen.add(cur)
            cur = (nodes[cur] or {}).get("parent")
            hops += 1

    if base_id and base_id not in nodes:
        problems.append(f"base '{base_id}' is not a node in this tree")

    # the base has to be reproducible, or the next comparison has nothing to compare against
    if base_id and isinstance(nodes.get(base_id), dict):
        bnode = nodes[base_id]
        if bnode.get("kind") == "experiment" and bnode.get("verdict") not in ("keep", None):
            problems.append(
                f"base '{base_id}' has verdict {bnode.get('verdict')!r}; the base must be a "
                "kept node"
            )
        if not bnode.get("artifacts"):
            problems.append(
                f"base '{base_id}' has no artifacts, so the next iteration cannot compare "
                "against a reproducible run"
            )

    return problems


# --------------------------------------------------------------------------- read gate

def read(competition: str) -> dict[str, Any]:
    """Read the tree and mark it as read at its current revision.

    The returned ``readRevision`` is what authorises the next write. It is a claim about this
    reader's currency, and it expires the moment the tree changes.
    """
    tree = load(competition)
    tree["problems"] = validate(tree)
    tree["readRevision"] = tree["revision"]
    tree["nextNodeId"] = _suggest_id(tree)
    tree["path"] = tree_path(competition)
    tree["recipes"] = latest_recipes(tree)
    return tree


def latest_recipes(competition_or_tree: Any) -> dict[str, Any]:
    """The most recent working recipe per engine, so a run can start from it.

    "Latest" is by tree order, not by timestamp: nodes are appended, so the last node that
    carries a recipe is the most recent thing that was actually run. Exposed as a rollup
    because the useful question is "what command launches this competition now", and that is
    a single answer, not something to reassemble by reading the chain.
    """
    tree = competition_or_tree
    if isinstance(competition_or_tree, str):
        tree = load(competition_or_tree)
    if not isinstance(tree, dict):
        return {}
    nodes = _current(tree).get("nodes") or {}
    out: dict[str, Any] = {}
    for nid, node in nodes.items():
        if not isinstance(node, dict):
            continue
        recipe = node.get("recipe")
        if not isinstance(recipe, dict):
            continue
        engine = str(recipe.get("engine") or "").strip() or "unknown"
        out[engine] = {"node": nid, "recipe": recipe,
                       "inherited": node.get("recipeInheritedFrom") or None}
    return out


def _suggest_id(tree: dict[str, Any]) -> str:
    """Next free node id. Numeric ids keep the tree readable in a diff."""
    used = set((_current(tree).get("nodes") or {}).keys())
    i = 1
    while f"n{i}" in used:
        i += 1
    return f"n{i}"


def pending_declarations(competition_or_tree: Any) -> list[dict[str, Any]]:
    """Declared-but-unsettled experiments, oldest first.

    "Unsettled" is derived, not stored: a declaration is done the moment it has a child. That
    keeps the tree append-only — nothing is ever rewritten in place to mark it finished.
    """
    tree = competition_or_tree
    if isinstance(competition_or_tree, str):
        tree = load(competition_or_tree)
    if not isinstance(tree, dict):
        return []
    inner = _current(tree)
    nodes = inner.get("nodes") or {}
    parents = {v.get("parent") for v in nodes.values() if isinstance(v, dict)}
    out = [
        {"id": nid, "change": n.get("change"), "hypothesis": n.get("hypothesis"),
         "parent": n.get("parent"), "operator": n.get("operator")}
        for nid, n in nodes.items()
        if is_planned(n) and nid not in parents
    ]
    return out


def declare(competition: str, node: dict[str, Any], read_revision: Optional[int]) -> dict[str, Any]:
    """Announce an experiment before running it, so the run can be tied to a node.

    This is the precondition kaggle_kernel_launch requires. It is two calls — read, then declare —
    and in exchange no experiment result can exist only in a transcript.

    A declaration must also say what it is based on. Either a diagnosis — a research node in this
    tree that actually read a run's log — or, when the tree has no completed run to learn from,
    the literal "none" with a reason. That is the link which makes the loop a loop: without it the
    tree holds scores and never holds what the runs taught.
    """
    node = normalize_node(node)
    if not isinstance(node, dict):
        return {"ok": False, "code": "bad_node", "message": "node must be an object"}

    # The read-gate first, because it is the more basic failure: telling someone their diagnosis
    # citation is wrong when they never read the tree would be a confusing way to say "you are out
    # of date". record() enforces the same rule; checking it here keeps the ordering honest.
    if read_revision is None:
        return {"ok": False, "code": "read_required",
                "message": f"read the tree before declaring. Call kaggle_experiment_tree "
                           f"action=\"read\" for {competition!r}, look at the base, the kept chain "
                           f"and the refuted list, then pass its readRevision back."}
    if int(read_revision) != int(load(competition)["revision"]):
        return {"ok": False, "code": "stale_read",
                "message": f"the tree has changed since you read it (you read revision "
                           f"{read_revision}). Read it again and re-plan from the current base "
                           f"before declaring."}

    tree = load(competition)
    nodes = _current(tree).get("nodes") or {}
    diagnosis = node.get("diagnosis")
    settled = [
        nid for nid, n in nodes.items()
        if isinstance(n, dict) and n.get("kind") == "experiment" and not is_planned(n)
    ]

    # ---- the curriculum gate. Checked BEFORE the diagnosis gate, because "you are trying the
    # hard stage first" and "you did not say what you learned" are different mistakes, and
    # reporting the wrong one first sends the agent to fix the wrong thing.
    ladder = curriculum_of(tree)
    if ladder:
        unlocked = str(tree.get("stage") or ladder[0]["name"])
        want_stage = str(node.get("stage") or unlocked).strip()
        here, there = _stage_index(ladder, want_stage), _stage_index(ladder, unlocked)
        if here > there:
            override = str(node.get("stageOverride") or "").strip()
            if not override:
                skipped = [s["name"] for s in ladder[there:here]]
                return {
                    "ok": False, "code": "stage_locked",
                    "message": (
                        f"stage {want_stage!r} is locked. This tree is working at "
                        f"{unlocked!r}, and the ladder is "
                        f"{' -> '.join(s['name'] for s in ladder)}. "
                        f"{'Pass ' + ', '.join(skipped) + ' first' if skipped else ''}, or set "
                        f"stageOverride with a real reason. A curriculum exists so the easy "
                        f"stages actually get run; skipping one on purpose is fine, skipping "
                        f"one by accident is how a run burns a day on a stage that was never "
                        f"going to work."
                    ),
                    "unlocked": unlocked,
                    "requested": want_stage,
                    "skipped": skipped,
                    "passesWhen": [s for s in ladder if s["name"] in skipped],
                }
            node = dict(node)
            node["stageOverrideReason"] = override
    if diagnosis is None:
        return {
            "ok": False, "code": "diagnosis_required",
            "message": (
                f"a declaration must say what it is based on. Cite a diagnosis - the id of a "
                f"research node that read a run's log - or set diagnosis=\"none\" with "
                f"diagnosisReason. This tree has {len(settled)} completed run(s), so \"none\" needs "
                f"a real reason."
            ),
            "settled": settled[:12],
        }
    if isinstance(diagnosis, str) and diagnosis.strip().lower() == "none":
        reason = node.get("diagnosisReason")
        if not reason or _norm_reason(reason) in EMPTY_REASONS:
            return {
                "ok": False, "code": "diagnosis_reason_required",
                "message": (
                    "diagnosis=\"none\" needs diagnosisReason saying why there is nothing to "
                    "learn from yet. 'no reason' is not a reason."
                ),
            }
    elif isinstance(diagnosis, str) and diagnosis not in nodes:
        return {
            "ok": False, "code": "unknown_diagnosis",
            "message": f"no node {diagnosis!r} in the tree for {competition!r}. "
                       f"Run action=\"diagnose\" first, or cite a diagnosis that exists.",
        }
    elif isinstance(diagnosis, str) and not (nodes[diagnosis].get("bottleneck")):
        return {
            "ok": False, "code": "not_a_diagnosis",
            "message": f"{diagnosis!r} is a research node but carries no bottleneck, so it is not a "
                       f"diagnosis of a run. Cite one that read a log, or set "
                       f"diagnosis=\"none\" with a reason.",
        }

    # ---- the prediction gate. A declaration says what you expect to happen, in a form that
    # can come out wrong: a direction and a floor. Without it the tree can only ever record
    # what did happen, and "did it work as intended" is unanswerable forever - the result and
    # the expectation are never compared by anything. The escape hatch exists because a
    # genuinely open exploration has no prediction; what is refused is the silence.
    if node.get("expect") is None and not str(node.get("expectOmitted") or "").strip():
        return {
            "ok": False, "code": "expect_required",
            "message": (
                "a declaration must predict what will happen, so the result can be compared "
                "against it afterwards. Give expect={} with a direction ('up' or 'down') and "
                "an atLeast floor in the metric's own units - the floor is what makes a "
                "prediction falsifiable, because any movement at all can be called 'in the "
                "right direction'. If this run is genuinely open exploration with nothing to "
                "predict, set expectOmitted saying what you are actually looking for."
            ),
        }
    if node.get("expect") is not None:
        bad_expect = _validate_expectation("this declaration", node)
        if bad_expect:
            return {"ok": False, "code": "expect_invalid", "message": "\n".join(bad_expect)}
        # ---- the resolution gate. A prediction the metric cannot resolve is not a weak
        # prediction, it is an unmeasurable one: the run spends quota and comes back with a
        # number that a re-roll would have produced too. The floor that catches this is the
        # CALIBRATED one, not this arm's own repeats - repeating one configuration on the same
        # splits does not make the splits finer. An uncalibrated tree is not gated, because
        # refusing everything is not a stricter tree, it is a tree nobody uses.
        _ruler = tree.get("ruler") or {}
        try:
            _noise = float(_ruler.get("noise")) if _ruler.get("noise") is not None else None
            _asked = abs(float(node["expect"].get("atLeast")))
        except (TypeError, ValueError):
            _noise, _asked = None, None
        if _noise is not None and _noise > 0 and _asked is not None and _asked < _noise:
            return {
                "ok": False, "code": "delta_below_resolution",
                "message": (
                    f"atLeast={_asked:.4g} sits inside this metric's resolution, so this run "
                    f"cannot be told apart from a re-roll. The calibrated noise floor is "
                    f"{_noise:.4g} (from {_ruler.get('noiseFrom') or 'a calibration'}); headroom "
                    f"is {_ruler.get('headroom')}; the smallest delta worth acting on is "
                    f"{_ruler.get('smallestActionable')}. More repetitions, more splits, or a "
                    f"finer-grained metric - or declare a run whose atLeast clears {_noise:.4g}."
                ),
            }

    prepared = dict(node)
    # A declaration is always an experiment. A research node changes nothing and runs nothing,
    # so it is never a precondition for a launch and is not what this action produces.
    prepared.setdefault("kind", "experiment")
    prepared["status"] = "planned"
    prepared.pop("metric", None)
    prepared.pop("verdict", None)
    # The version that worked is the one to start from. A declaration that does not name its
    # own recipe inherits the parent's, so the command that produced the last result is the
    # command this run starts with - reused rather than re-derived, and changed on purpose when
    # this run differs. That is the whole difference between a run and a re-typing of one.
    if not prepared.get("recipe"):
        parent_id = prepared.get("parent")
        inherited = (nodes.get(parent_id) or {}).get("recipe") if parent_id else None
        if isinstance(inherited, dict):
            prepared["recipe"] = dict(inherited)
            prepared["recipeInheritedFrom"] = parent_id
    return record(competition, prepared, read_revision)


def settle(competition: str, declared: str, node: dict[str, Any],
           read_revision: Optional[int]) -> dict[str, Any]:
    """Record the result of a declared experiment, as a new node parented by the declaration."""
    tree = load(competition)
    nodes = _current(tree).get("nodes") or {}
    if declared not in nodes:
        return {"ok": False, "code": "unknown_declaration",
                "message": f"no declaration {declared!r} in the tree for {competition!r}"}
    if not is_planned(nodes[declared]):
        return {"ok": False, "code": "not_a_declaration",
                "message": f"node {declared!r} is not a declaration"}
    if any(isinstance(v, dict) and v.get("parent") == declared for v in nodes.values()):
        return {"ok": False, "code": "already_settled",
                "message": f"declaration {declared!r} already has a result node; declare a new "
                           f"experiment rather than settling this one twice"}
    node = normalize_node(node)
    if not isinstance(node, dict):
        return {"ok": False, "code": "bad_node", "message": "node must be an object"}
    prepared = dict(node)
    prepared.setdefault("kind", "experiment")
    prepared.setdefault("parent", declared)
    # The prediction travels with the declaration and is judged HERE, once there is a number
    # to judge it against. Copying it onto the result rather than asking for it again is the
    # point: a prediction written after seeing the result is not a prediction.
    declaration = nodes.get(declared) or {}
    if isinstance(declaration.get("expect"), dict) and not isinstance(prepared.get("expect"), dict):
        prepared["expect"] = dict(declaration["expect"])
    judgment = judge_expectation(prepared.get("expect"), prepared.get("metric"),
                                 tree.get("ruler") or {})
    prepared["expectation"] = judgment
    if prepared.get("expectOmitted") or str(declaration.get("expectOmitted") or "").strip():
        prepared.setdefault("expectOmitted", declaration.get("expectOmitted"))
    res = record(competition, prepared, read_revision)
    # The judgement is returned even when the record was refused, because "your prediction was
    # refuted" is the answer the agent asked for and it is still true when the node is bad.
    if res.get("ok"):
        res["expectation"] = judgment
    return res


def _tokens(*values: Any) -> set[str]:
    words: set[str] = set()
    for v in values:
        for tok in re.findall(r"[a-z0-9]+", str(v or "").lower()):
            if len(tok) > 2:
                words.add(tok)
    return words


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def consider(competition: str, change: str, hypothesis: str = "",
             operator: str = "", family: str = "") -> dict[str, Any]:
    """Ask the tree whether a proposed step is worth a node — at every step, not only before a run.

    The point is that consulting the tree must be cheap enough to do every time. A node costs a
    read, a write and a validation pass, and a tree padded with "I looked at the docs" nodes is
    worse than no tree: the refuted list stops being a signal. So the tool does the mechanical
    part — matching what you are about to do against what the tree already knows — and returns a
    verdict. The judgement of whether it matters is left to the agent, because that is judgement
    and not lookup.
    """
    tree = read(competition)
    if tree.get("problems"):
        return {"ok": False, "code": "tree_has_problems", "tree": tree}
    inner = _current(tree)
    nodes = inner.get("nodes") or {}
    proposed = _tokens(change, hypothesis)
    base_id = (inner.get("base") or {}).get("id")
    inflight_ids = {p["id"] for p in pending_declarations(tree)}

    matches: list[dict[str, Any]] = []
    for nid, n in sorted(nodes.items()):
        if not isinstance(n, dict):
            continue
        score = _overlap(proposed, _tokens(n.get("change"), n.get("hypothesis")))
        if score < 0.34:
            continue
        matches.append({
            "id": nid, "kind": n.get("kind"), "score": round(score, 3),
            "verdict": n.get("verdict"), "change": n.get("change"),
            "failureLayer": n.get("failureLayer"), "reason": n.get("reason"),
            "family": n.get("family"), "operator": n.get("operator"),
            "inFlight": nid in inflight_ids,
        })
    matches.sort(key=lambda m: -m["score"])

    def _pick(pred):
        return next((m for m in matches if pred(m)), None)

    refuted_hit = _pick(lambda m: m["verdict"] == "revert")
    inflight_hit = _pick(lambda m: m["inFlight"])
    kept_hit = _pick(lambda m: m["verdict"] == "keep" and m["id"] != base_id)

    if inflight_hit:
        verdict, why, node = "in_flight", (
            f"{inflight_hit['id']} already declares this and has no result yet. Settle it or "
            f"wait; do not declare it twice."), inflight_hit
    elif refuted_hit:
        verdict, why, node = "already_refuted", (
            f"{refuted_hit['id']} was reverted{f' at layer {refuted_hit['failureLayer']}' if refuted_hit.get('failureLayer') else ''}. "
            f"Its reason: {refuted_hit['reason']!r}. Re-running it is the exact waste the tree "
            f"exists to prevent."), refuted_hit
    elif kept_hit:
        verdict, why, node = "already_known", (
            f"{kept_hit['id']} already kept this. You are re-deriving a result the tree holds; "
            f"if you think it no longer holds, that is a node with a reason, not a repeat."), kept_hit
    elif not change:
        verdict, why, node = "not_worth_a_node", (
            "no change was stated, so there is nothing to record."), None
    elif operator and family:
        verdict, why, node = "worth_declaring", (
            "nothing in the tree covers this, and it names one change, one operator and one "
            "family — that is what an experiment node is for. Declare it before you run it."), None
    else:
        verdict, why, node = "judge_it", (
            "nothing in the tree covers this, but it does not name an operator and a family, so "
            "the tree cannot score or attribute it. A node is worth it only when the result "
            "would change what you do next."), None

    return {
        "ok": True, "verdict": verdict, "why": why, "match": node,
        "matches": matches[:5],
        "worthANode": verdict == "worth_declaring",
        "base": base_id,
        "inFlight": sorted(inflight_ids),
        "revision": tree["revision"],
    }


def prune(competition: str, node_id: str, reason: str,
          read_revision: Optional[int] = None) -> dict[str, Any]:
    """Delete one research node — the collected material — and nothing else.

    Scoped deliberately narrow. A research node is a note that something was read; if it turned
    out to be useless, keeping it only makes the refuted list and the board harder to read. An
    experiment node is evidence that quota was spent, and evidence is not deleted. So this refuses
    anything that is not a research node, refuses a node anything still points at, and records the
    deletion in the journal so `undo` can put it back.
    """
    if read_revision is None:
        return {"ok": False, "code": "read_required",
                "message": f"read the tree before pruning, so you see what you are deleting. Call "
                           f"kaggle_experiment_tree action=\"read\" for {competition!r}."}
    tree = load(competition)
    if int(read_revision) != int(tree["revision"]):
        return {"ok": False, "code": "stale_read",
                "message": f"the tree changed since you read it (you read revision "
                           f"{read_revision}, it is now {tree['revision']}). Read it again."}
    if not _norm_reason(reason) or _norm_reason(reason) in EMPTY_REASONS:
        return {"ok": False, "code": "empty_reason",
                "message": "say why this research node is not worth keeping — 'it is useless' is "
                           "not a reason, 'the API it documents is deprecated' is."}

    inner = _current(tree)
    nodes = inner.get("nodes") or {}
    nid = str(node_id or "").strip()
    if nid not in nodes:
        return {"ok": False, "code": "unknown_node",
                "message": f"no node {nid!r} in the tree for {competition!r}"}
    node = nodes[nid]

    if node.get("kind") != "research":
        return {"ok": False, "code": "not_prunable",
                "message": f"{nid!r} is a {node.get('kind')!r} node, and only collected research "
                           f"material can be pruned. An experiment is evidence that quota was "
                           f"spent; if it was wrong, record a revert rather than deleting it."}
    if nid == (inner.get("base") or {}).get("id"):
        return {"ok": False, "code": "not_prunable",
                "message": f"{nid!r} is the current base. Promote another node with "
                           f"record(new_base=...) first."}
    children = [k for k, v in nodes.items() if isinstance(v, dict) and v.get("parent") == nid]
    if children:
        return {"ok": False, "code": "still_referenced",
                "message": f"{nid!r} still has children ({', '.join(sorted(children))}), so its "
                           f"question is not settled yet. Prune the branch, not the root."}
    for rnd in tree.get("rounds") or []:
        archived = ((rnd.get("tree") or {}).get("nodes") or {})
        if nid in archived:
            return {"ok": False, "code": "archived",
                    "message": f"{nid!r} is inside an archived round, which is the replay pool. "
                               f"Deleting it would make a replay score unreproducible."}
    if nid in (tree.get("anchor") or {}).get("nodes", []) or nid == (tree.get("anchor") or {}).get("heldOut"):
        return {"ok": False, "code": "protected",
                "message": f"{nid!r} is part of the held-out anchor."}

    before_nodes = dict(nodes)
    del before_nodes[nid]
    candidate = dict(tree)
    inner_candidate = dict(inner)
    inner_candidate["nodes"] = before_nodes
    candidate["tree"] = inner_candidate
    problems = validate(candidate)
    if problems:
        return {"ok": False, "code": "would_break_tree",
                "message": "deleting this node would leave the tree invalid",
                "problems": problems}

    inner_candidate["nodes"] = before_nodes
    tree["tree"] = inner_candidate
    tree.setdefault("journal", []).append({
        "op": "prune", "nodeId": nid, "reason": str(reason)[:400],
        "undoable": True, "before": {"node": node},
    })
    tree["revision"] = int(tree["revision"]) + 1
    save(competition, tree)
    return {"ok": True, "pruned": nid, "reason": str(reason)[:400],
            "revision": tree["revision"], "path": tree.get("path")}


def _subtree(nodes: dict[str, Any], root: str) -> list[str]:
    """Every node reachable from `root` by parent links, root included, in tree order."""
    out: list[str] = []
    frontier = [root]
    while frontier:
        nid = frontier.pop(0)
        if nid in out or nid not in nodes:
            continue
        out.append(nid)
        frontier.extend(k for k, v in nodes.items()
                        if isinstance(v, dict) and v.get("parent") == nid)
    return out


def summarise_branch(nodes: dict[str, Any], ids: list[str]) -> dict[str, Any]:
    """What a line of work turned out to be, in a form that survives losing its files.

    Written into the tree at abandon time, because the reason a route was given up on is the
    only thing about it that is worth carrying forward - and it is worth carrying forward
    precisely when the files are gone. Quota spent and what was learned outlive the artefacts.
    """
    scored = []
    for nid in ids:
        n = nodes.get(nid) or {}
        m = n.get("metric")
        if isinstance(m, dict) and m.get("result") is not None:
            scored.append((_signed_score(m) if _signed_score(m) is not None else float("-inf"),
                           nid, n, m))
    scored.sort(key=lambda row: -row[0])
    best = scored[0] if scored else None
    experiments = [n for n in ids
                   if (nodes.get(n) or {}).get("kind") == "experiment"
                   and not is_planned(nodes.get(n) or {})]
    refuted = [nid for nid in ids if (nodes.get(nid) or {}).get("verdict") == "revert"]
    confirmed = [nid for nid in ids
                 if ((nodes.get(nid) or {}).get("expectation") or {}).get("verdict") == "confirmed"]
    refuted_expect = [nid for nid in ids
                      if ((nodes.get(nid) or {}).get("expectation") or {}).get("verdict")
                      in ("refuted", "partial")]
    quota = 0.0
    for nid in ids:
        cost = (nodes.get(nid) or {}).get("cost")
        if isinstance(cost, dict):
            try:
                quota += float(cost.get("quotaHours") or 0.0)
            except (TypeError, ValueError):
                pass
    return {
        "nodes": len(ids),
        "experiments": len(experiments),
        "refuted": len(refuted),
        "quotaHours": round(quota, 3),
        "best": ({"id": best[1], "change": best[2].get("change"),
                  "metric": best[3].get("name"), "result": best[3].get("result"),
                  "delta": best[3].get("delta")} if best else None),
        "confirmedExpectations": len(confirmed),
        "refutedOrPartialExpectations": len(refuted_expect),
        "operators": sorted({str((nodes.get(n) or {}).get("operator")) for n in ids
                             if (nodes.get(n) or {}).get("operator")}),
        "families": sorted({str((nodes.get(n) or {}).get("family")) for n in ids
                            if (nodes.get(n) or {}).get("family")}),
    }


def abandon(competition: str, node_id: str, reason: str,
            branch: str = "", read_revision: Optional[int] = None) -> dict[str, Any]:
    """Give up a whole line of work: summarise it, mark it, and move its files aside.

    An abandoned route is not a refuted node. Refuting says "this idea was wrong and here is
    the evidence"; abandoning says "this whole direction is not worth more of my time", which
    is a different and much larger claim, and one that used to have no way to be expressed at
    all. Without it, the only options were to keep quietly extending something already known
    to be bad, or to hand-edit the tree - and hand-edits are not evidence.

    Nothing is deleted. The nodes are marked, so the cost and the reason stay on the record,
    and the branch's files are MOVED to quarantine, which is recoverable and inspectable. That
    is the whole point: quota was spent, and spent quota is the one thing that cannot be
    un-spent. If the files need to go for disk, deleting the quarantined folder is a decision
    the user makes with their own hands, not a side effect of giving up.
    """
    nid = str(node_id or "").strip()
    if not nid:
        return {"ok": False, "code": "no_node",
                "message": "abandon needs the id of the node that starts the line to give up."}
    tree = load(competition)
    if read_revision is not None and int(read_revision) != int(tree["revision"]):
        return {"ok": False, "code": "stale_read",
                "message": f"the tree has changed since you read it (you read revision "
                           f"{read_revision}, it is now {tree['revision']}). Read it again."}
    inner = _current(tree)
    nodes = inner.get("nodes") or {}
    if nid not in nodes:
        return {"ok": False, "code": "unknown_node",
                "message": f"no node {nid!r} in the tree for {competition!r}."}
    node = nodes[nid]
    if nid == (inner.get("base") or {}).get("id"):
        return {"ok": False, "code": "is_base",
                "message": f"{nid!r} is the current base, so abandoning it would leave the tree "
                           f"with no base. Promote another node with record(new_base=...) first, "
                           f"then abandon this one."}
    if str(node.get("abandonedAt") or "").strip():
        return {"ok": False, "code": "already_abandoned",
                "message": f"{nid!r} was already abandoned at {node.get('abandonedAt')!r}. "
                           f"action=\"undo\" if that was wrong."}
    if not str(reason or "").strip():
        return {"ok": False, "code": "reason_required",
                "message": "abandon needs a reason. The reason is the part that is worth keeping "
                           "after the files are gone - 'we are going a different direction' is a "
                           "reason, 'it failed' is not."}

    ids = _subtree(nodes, nid)
    summary = summarise_branch(nodes, ids)
    line = str(branch or node.get("branch") or "").strip()

    # The BEFORE state must record absence as well as presence. If a node had no
    # `abandonedAt` before, undo has to remove it - and a snapshot that only stores keys that
    # happened to be present cannot express "this was not here", so the mark survives undo and
    # the line stays abandoned forever.
    MARK_KEYS = ("abandonedAt", "abandonReason", "branch")
    before_marks = {n: {k: nodes[n].get(k) for k in MARK_KEYS} for n in ids}
    stamp = _now()
    inner = dict(inner)
    new_nodes = dict(nodes)
    for n in ids:
        marked = dict(new_nodes[n])
        marked["abandonedAt"] = stamp
        marked["abandonReason"] = str(reason)[:400]
        if line:
            marked["branch"] = line
        new_nodes[n] = marked
    inner["nodes"] = new_nodes
    abandoned = [dict(r) for r in (tree.get("abandoned") or [])]
    abandoned.append({
        "root": nid, "branch": line or None, "at": stamp, "reason": str(reason)[:400],
        "nodes": ids, "summary": summary,
        "filesMovedTo": None,   # filled in below, only if a move actually happened
    })
    tree["tree"] = inner
    tree["abandoned"] = abandoned
    entry = abandoned[-1]

    # The files move, and the move is recorded so undo can put them back. A branch with no
    # folder is not an error: plenty of runs are Kaggle kernels whose outputs never landed
    # locally, and inventing an empty directory would look like a successful move.
    moved: dict[str, str] = {}
    if line:
        src, dst = branch_dir(competition, line), quarantine_dir(competition, line)
        try:
            if os.path.isdir(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if os.path.isdir(dst):
                    dst = dst + "-" + stamp.replace(":", "").replace("-", "")
                shutil.move(src, dst)
                moved = {"from": src, "to": dst}
                entry["filesMovedTo"] = dst
        except OSError as exc:
            entry["filesMoveError"] = str(exc)

    tree.setdefault("journal", []).append({
        "op": "abandon", "nodeId": nid, "reason": str(reason)[:400],
        "undoable": True, "before": {"marks": before_marks, "moved": moved,
                                     "abandonedEntry": dict(entry)},
    })
    tree["revision"] = int(tree["revision"]) + 1
    problems = validate(tree)
    if problems:
        return {"ok": False, "code": "would_break_tree",
                "message": "abandoning this line would leave the tree invalid, so nothing was "
                           "changed.",
                "problems": problems}
    save(competition, tree)
    return {
        "ok": True, "abandoned": nid, "nodes": ids, "branch": line or None,
        "reason": str(reason)[:400],
        "summary": summary, "filesMovedTo": entry.get("filesMovedTo"),
        "filesMoveError": entry.get("filesMoveError"),
        "revision": tree["revision"], "path": tree.get("path"),
        "note": (
            "Nothing was deleted. The nodes are marked and the line no longer counts as kept, "
            "so it will not be selected or replayed. "
            + (f"Its files are at {entry['filesMovedTo']} - delete that folder yourself if you "
               f"need the disk back." if entry.get("filesMovedTo") else
               "It had no local folder, so there was nothing to move - delete nothing.")
            + " action=\"undo\" reverses this."
        ),
    }


def _gated_write(tree: dict[str, Any], read_revision: Optional[int]) -> Optional[dict[str, Any]]:
    if read_revision is not None and int(read_revision) != int(tree["revision"]):
        return {"ok": False, "code": "stale_read",
                "message": f"the tree has changed since you read it (you read revision "
                           f"{read_revision}, it is now {tree['revision']}). Read it again."}
    return None


def set_goal(competition: str, metric: str = "", target: Any = None,
             direction: str = "", note: str = "",
             read_revision: Optional[int] = None) -> dict[str, Any]:
    """Declare what this tree is ultimately optimising, or read back what it declared.

    The question "which metric is the terminal one" has never had an answer stored anywhere,
    so it is re-decided silently at every node - and a search that re-decides its objective
    per node drifts toward whatever is easiest to move. Declaring it once makes the drift
    visible: any node measuring a different metric has to say why.
    """
    tree = load(competition)
    stale = _gated_write(tree, read_revision)
    if stale:
        return stale
    if not str(metric or "").strip():
        current = goal_of(tree)
        return {"ok": True, "goal": current, "set": False,
                "message": (f"this tree optimises {current.get('metric')!r}"
                            f"{' toward ' + str(current['target']) if current.get('target') is not None else ''}."
                            if current else
                            "no goal is declared. Pass metric=<name> and, if you know it, "
                            "target=<number>. Until then nothing tells a kept node from a "
                            "kept node that moved the wrong number.")}
    goal: dict[str, Any] = {"metric": str(metric).strip()}
    if target is not None:
        try:
            goal["target"] = float(target)
        except (TypeError, ValueError):
            return {"ok": False, "code": "bad_target",
                    "message": f"target must be a number, got {target!r}"}
    if direction:
        if direction not in VALID_DIRECTIONS:
            return {"ok": False, "code": "bad_direction",
                    "message": f"direction must be one of {', '.join(VALID_DIRECTIONS)}, "
                               f"got {direction!r}"}
        goal["direction"] = direction
    if note:
        goal["note"] = str(note)[:400]
    goal["declaredAt"] = _now()
    tree["goal"] = goal
    tree["revision"] = int(tree["revision"]) + 1
    save(competition, tree)
    return {"ok": True, "goal": goal, "set": True, "revision": tree["revision"],
            "message": f"this tree now optimises {goal['metric']!r}. Any node measuring a "
                       f"different metric needs offGoalReason, so a metric swap has to be said "
                       f"out loud."}


def set_stage(competition: str, curriculum: Optional[list] = None, stage: str = "",
              read_revision: Optional[int] = None) -> dict[str, Any]:
    """Declare the easy-to-hard ladder, and which rung the search is allowed to work on.

    Working simple-to-hard is not advice, it is a gate. A hard experiment run before the easy
    one has passed usually fails for a reason that has nothing to do with the idea, and the
    failure gets recorded as evidence against the idea. So a declaration above the current
    stage is refused, with the same escape hatch the other gates use.
    """
    tree = load(competition)
    stale = _gated_write(tree, read_revision)
    if stale:
        return stale
    if curriculum:
        names = [str(s.get("name") or "").strip() for s in curriculum
                 if isinstance(s, dict) and str(s.get("name") or "").strip()]
        if len(names) < 2:
            return {"ok": False, "code": "curriculum_too_short",
                    "message": "a curriculum needs at least two named stages, simplest first. "
                               'e.g. [{"name":"smoke",...},{"name":"scale",...}]'}
        if len(set(names)) != len(names):
            return {"ok": False, "code": "duplicate_stage",
                    "message": f"stage names must be unique, got {names}"}
        tree["curriculum"] = curriculum
        tree.setdefault("stage", names[0])
        tree["revision"] = int(tree["revision"]) + 1
        save(competition, tree)
        return {"ok": True, "set": True, "revision": tree["revision"],
                "curriculum": curriculum_of(tree), "stage": tree.get("stage"),
                "message": f"curriculum declared: {' -> '.join(names)}. declare is now locked "
                           f"to {tree.get('stage')!r}; pass stageOverride to go further early."}
    ladder = curriculum_of(tree)
    if not ladder:
        return {"ok": False, "code": "no_curriculum",
                "message": "no curriculum is declared. Pass curriculum=[...] as a JSON array in "
                           "one string, simplest stage first, or pass action=\"read\" to see the "
                           "tree as it is."}
    if not stage:
        return {"ok": True, "set": False, "curriculum": ladder, "stage": tree.get("stage"),
                "message": f"curriculum: {' -> '.join(s['name'] for s in ladder)}; currently "
                           f"allowed: {tree.get('stage') or ladder[0]['name']}"}
    names = [s["name"] for s in ladder]
    if stage not in names:
        return {"ok": False, "code": "unknown_stage",
                "message": f"{stage!r} is not a stage. The ladder is {', '.join(names)}."}
    if _stage_index(ladder, stage) < _stage_index(ladder, tree.get("stage") or names[0]):
        return {"ok": False, "code": "stage_backwards",
                "message": f"the tree is already at {tree.get('stage')!r}; going back to "
                           f"{stage!r} is not supported. A stage you have passed is not a place "
                           f"to return to - record a new experiment instead."}
    tree["stage"] = stage
    tree["revision"] = int(tree["revision"]) + 1
    save(competition, tree)
    return {"ok": True, "set": True, "stage": stage, "revision": tree["revision"],
            "curriculum": ladder,
            "message": f"stage is now {stage!r}. declare may work at this stage and below."}


def branch_paths(competition: str, name: str, node: str = "",
                 read_revision: Optional[int] = None) -> dict[str, Any]:
    """Where a line of work keeps its files, created on demand.

    A route is a folder, not a word. Two approaches on one competition that share a directory
    will overwrite each other's checkpoints, and the one that survives is whichever ran last -
    which is exactly how a result from an approach you already gave up on gets shipped.
    """
    tree = load(competition)
    stale = _gated_write(tree, read_revision)
    if stale:
        return stale
    if not str(name or "").strip():
        branches = sorted({str(n.get("branch")) for n in _current(tree).get("nodes", {}).values()
                           if isinstance(n, dict) and n.get("branch")})
        return {"ok": True, "set": False, "branches": branches,
                "message": (f"branches in use: {', '.join(branches)}" if branches else
                            "no branch is in use. Pass name=<branch> to create one, or omit it "
                            "on a declaration to use the default.")}
    path = branch_dir(competition, name)
    os.makedirs(path, exist_ok=True)
    branches = sorted({str(n.get("branch")) for n in _current(tree).get("nodes", {}).values()
                       if isinstance(n, dict) and n.get("branch")} | {name})
    return {"ok": True, "set": True, "branch": name, "path": path, "branches": branches,
            "quarantine": quarantine_dir(competition, name),
            "message": f"branch {name!r} keeps its files in {path}. Put this path in the run's "
                       f"recipe so the next run on this line writes in the same place, and "
                       f"action=\"abandon\" branch={name!r} moves the whole folder aside when "
                       f"the line is given up on."}


def record(competition: str, node: dict[str, Any], read_revision: Optional[int],
           new_base: Optional[str] = None) -> dict[str, Any]:
    """Add one node, refusing unless the tree was read at its current revision.

    ``read_revision`` is the value the caller got from :func:`read`. If it does not match the
    tree's current revision, the tree changed since you looked, and planning on the version you
    remember is exactly how a branch goes off a stale base. The fix is to read again, which
    takes one call.
    """
    tree = load(competition)

    if read_revision is None:
        return {
            "ok": False,
            "code": "read_required",
            "message": (
                "read the tree before recording. Call "
                f"kaggle_experiment_tree action=\"read\" for {competition!r}, look at the base, "
                "the kept chain and the refuted list, then pass its readRevision back."
            ),
            "tree": read(competition),
        }

    if int(read_revision) != int(tree["revision"]):
        return {
            "ok": False,
            "code": "stale_read",
            "message": (
                f"the tree has changed since you read it (you read revision {read_revision}, "
                f"it is now {tree['revision']}). Read it again and re-plan from the current "
                "base before recording."
            ),
            "tree": read(competition),
        }

    node = normalize_node(node)
    if not isinstance(node, dict):
        return {"ok": False, "code": "bad_node", "message": "node must be an object"}

    nid = str(node.get("id") or "").strip()
    if not NODE_ID_RE.match(nid):
        return {
            "ok": False, "code": "bad_id",
            "message": f"node id {nid!r} must match {NODE_ID_RE.pattern}",
        }
    nodes = _current(tree)["nodes"]
    if nid in nodes:
        return {
            "ok": False, "code": "duplicate_id",
            "message": f"node {nid!r} already exists; ids are never reused or rewritten",
        }

    # Validate the candidate document before committing, so a rejected node leaves nothing
    # behind. The node has to be spliced into the CURRENT tree (doc["tree"]["nodes"]), not into
    # a top-level "nodes" key that _current() would never look at.
    candidate = dict(tree)
    inner_candidate = dict(_current(tree))
    inner_candidate["nodes"] = {**nodes, nid: node}
    if new_base:
        inner_candidate["base"] = {
            "id": new_base,
            "label": str(node.get("change") or node.get("question") or nid)[:120],
            "parent": node.get("parent") if node.get("parent") else None,
        }
    candidate["tree"] = inner_candidate
    problems = validate(candidate)
    if problems:
        return {
            "ok": False,
            "code": "invalid_node",
            "message": "the node was rejected; fix these and record again",
            "problems": problems,
            "node": node,
        }

    _current(tree)["nodes"] = inner_candidate["nodes"]
    if new_base:
        _current(tree)["base"] = inner_candidate["base"]
    tree["revision"] = int(tree["revision"]) + 1
    tree["updatedAt"] = _now()
    path = _write(competition, tree)

    return {
        "ok": True,
        "nodeId": nid,
        "kind": node.get("kind"),
        "revision": tree["revision"],
        "updatedAt": tree["updatedAt"],
        "path": path,
        "base": _current(tree)["base"].get("id"),
        "note": (
            "the tree changed, so the next node requires a fresh read "
            "(action=\"read\") before it can be recorded."
        ),
    }


def plan_prompt(competition: str) -> str:
    """The planning surface: what the next agent must look at before choosing a node."""
    tree = read(competition)
    nodes = _current(tree).get("nodes") or {}
    base_id = (_current(tree).get("base") or {}).get("id")
    kept, refuted_rows, research_rows = [], [], []
    inflight = []
    for nid, n in nodes.items():
        if not isinstance(n, dict):
            continue
        if is_planned(n):
            settled = any(isinstance(v, dict) and v.get("parent") == nid for v in nodes.values())
            if not settled:
                inflight.append(f"{nid}: {n.get('change')} (declared, no result yet)")
                continue
        if n.get("kind") == "research":
            research_rows.append(f"{nid}: {n.get('question')} -> opens: {n.get('opens')}")
        if n.get("verdict") == "revert":
            refuted_rows.append(
                f"{nid}: {n.get('change') or n.get('question')} -> {n.get('reason')}"
            )
        elif n.get("verdict") == "keep":
            kept.append(f"{nid}: {n.get('change') or n.get('question')} ({n.get('reason')})")

    lines = [
        f"tree: {tree['path']}",
        f"revision: {tree['revision']}   nodes: {len(nodes)}   base: {base_id or '(none yet)'}",
        f"problems: {len(tree['problems'])}",
        "",
        "KEPT (do not re-derive, and do not repeat):",
    ]
    lines += [f"  {k}" for k in kept] or ["  (none)"]
    lines += ["", "IN FLIGHT (declared and run, but no result recorded yet — settle these):"]
    lines += [f"  {i}" for i in inflight] or ["  (none)"]
    lines += ["", "REFUTED (never run again - this is the whole point of the tree):"]
    lines += [f"  {r}" for r in refuted_rows] or ["  (none)"]
    lines += ["", "RESEARCH NODES (a result said the current understanding was insufficient):"]
    lines += [f"  {r}" for r in research_rows] or ["  (none)"]
    lines += [
        "",
        "Choose the next node, then record it with:",
        f'  kaggle_experiment_tree action="record" read_revision={tree["revision"]} node={{...}}',
        f"  suggested id: {tree['nextNodeId']}",
        "",
        "An experiment node changes ONE thing and measures it.",
        "A research node goes back to a source (forum / code / web / paper / model / dataset /",
        "rules / leaderboard) because a result made the current picture insufficient, and it must",
        "say what it opens up ('opens') - that is what a later experiment will build on.",
    ]
    # What this tree is for, and how far along it is. Both are one read away, and both are the
    # first thing a node needs to know: a change is only measurable relative to an objective,
    # and a run is only in the right place relative to a stage.
    goal = goal_of(tree)
    if goal:
        target = (f" (target {goal['target']})" if goal.get("target") is not None else "")
        lines.insert(4, f"optimising: {goal.get('metric')}{target}"
                         f"{' - ' + goal['note'] if goal.get('note') else ''}")
    ladder = curriculum_of(tree)
    if ladder:
        here = str(tree.get("stage") or ladder[0]["name"])
        lines.insert(5, "curriculum: " + " -> ".join(
            ("*" if s["name"] == here else " ") + s["name"] for s in ladder)
            + f"   (declare may work at {here} and below)")
    abandoned = tree.get("abandoned") or []
    if abandoned:
        lines.insert(6, "\n".join(
            [f"given up (do not restart these): {len(abandoned)} line(s)"]
            + [f"  {a['root']} ({a.get('branch') or 'no branch'}): {a.get('reason')}"
               for a in abandoned[-4:]]))
    if lines[4].startswith("optimising") or ladder or abandoned:
        lines.insert(7, "")
    return "\n".join(lines)


# --------------------------------------------------------------------------- search
# The selection rule below is adapted from OpenMLE-Evo (arXiv 2607.28568, sec. 5.2), which
# replaced greedy score-maximising parent selection with a three-factor utility:
#
#     U_i = lambda_s * score_i + lambda_delta * progress_i + lambda_n * novelty_i
#     P(i | I) = exp(U_i / tau) / sum_j exp(U_j / tau)
#
# Their result is the reason this is here: a matched comparison against the original AIRA-Evo,
# which "selects parents primarily by scalar fitness", moved Medal Average from 53.03% to
# 60.61% under the same model and the same 12-hour budget. Greedy-by-score is not a neutral
# default; it concentrates the search on the incumbent and discards branches that are merely
# promising. The three factors are what widen the space back out.
#
# Weights default to the paper's emphasis (quality still leads, but progress and novelty are not
# decoration) and are exposed so a run can tune them rather than hard-coding a taste.
DEFAULT_WEIGHTS = {"score": 1.0, "progress": 0.5, "novelty": 0.35, "temperature": 1.0}

# Visit cooling: a node that has been expanded many times is worth less each time, so one
# incumbent cannot monopolise the budget. This is the paper's C_p term, kept as a multiplier
# rather than a separate factor because it damps an over-used node rather than promoting a
# different property of it.
COOLING_HALF_LIFE = 3

# Below this many distinct method families the novelty factor is not discriminating: the first
# node carrying a family always scores 1.0 and every later one 0.0, so the ranking is decided by
# a binary that a two-node tree cannot support. selection says so rather than pretending.
MIN_FAMILIES_FOR_NOVELTY = 3


def _norm(value: Optional[float], lo: float, hi: float) -> float:
    """Map a value into [0, 1] over the observed range. Degenerate range -> 0.5.

    The 0.5 for a flat range matters: a factor that cannot be discriminated should neither
    vanish nor dominate, or selection silently becomes score-only again.
    """
    if value is None:
        return 0.5
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.5
    if not (hi > lo):
        return 0.5
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
    """A finite float or None. Shared with the plotting layer so a non-numeric field fails the
    same way in a chart as it does in validation, instead of being silently coerced to zero."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if f != f or f in (float("inf"), float("-inf")):
        return default
    return f


def _signed_delta(metric: Any) -> Optional[float]:
    """The node's gain, oriented so larger is always better.

    Metrics differ in direction - a lower loss is better, a higher accuracy is - so a raw delta
    is meaningless across nodes. The paper handles this by converting every task score to a
    signed value first (their s-tilde); we do the same from the declared direction, defaulting to
    'higher is better' and letting a node state otherwise.
    """
    if not isinstance(metric, dict):
        return None
    try:
        delta = float(metric.get("delta"))
    except (TypeError, ValueError):
        return None
    if str(metric.get("direction", "higher")).strip().lower().startswith("lower"):
        return -delta
    return delta


def _signed_score(metric: Any) -> Optional[float]:
    """The node's result, oriented the same way, so scores are comparable across nodes."""
    if not isinstance(metric, dict):
        return None
    try:
        result = float(metric.get("result"))
    except (TypeError, ValueError):
        return None
    if str(metric.get("direction", "higher")).strip().lower().startswith("lower"):
        return -result
    return result


def score_nodes(tree: dict[str, Any]) -> list[dict[str, Any]]:
    """Give every scored node the three factors parent selection needs.

    Progress is measured against the strongest ancestor, not the immediate parent, because a
    chain of small local improvements can look busy while never beating anything.
    """
    nodes = _current(tree).get("nodes") or {}

    def best_ancestor_score(start: Optional[str]) -> Optional[float]:
        best, cur, seen = None, start, set()
        while cur and cur in nodes and cur not in seen:
            seen.add(cur)
            s = _signed_score((nodes[cur] or {}).get("metric"))
            if s is not None and (best is None or s > best):
                best = s
            cur = (nodes[cur] or {}).get("parent")
        return best

    rows = []
    for nid, node in nodes.items():
        if not isinstance(node, dict) or node.get("kind") != "experiment":
            continue
        metric = node.get("metric")
        score = _signed_score(metric)
        family = str(node.get("family") or "").strip()
        samples = metric.get("samples") if isinstance(metric, dict) else None
        rows.append({
            "id": nid,
            "score": score,
            "delta": _signed_delta(metric),
            "family": family,
            "operator": node.get("operator"),
            "verdict": node.get("verdict"),
            "std": samples.get("std") if isinstance(samples, dict) else None,
            "ancestorScore": best_ancestor_score(node.get("parent")),
        })

    scores = [r["score"] for r in rows if r["score"] is not None]
    s_lo, s_hi = (min(scores), max(scores)) if scores else (0.0, 1.0)
    deltas = [r["delta"] for r in rows if r["delta"] is not None]
    d_lo, d_hi = (min(deltas), max(deltas)) if deltas else (0.0, 1.0)

    for r in rows:
        progress = None
        if r["score"] is not None and r["ancestorScore"] is not None:
            progress = r["score"] - r["ancestorScore"]
        r["progress"] = progress
        r["scoreNorm"] = _norm(r["score"], s_lo, s_hi)
        r["progressNorm"] = _norm(progress, d_lo, d_hi) if progress is not None else 0.0

    # Novelty is positional, not absolute: a family counts as novel until the first time it is
    # seen while scoring, so the earliest node carrying a family keeps its credit.
    claimed: set[str] = set()
    for r in sorted(rows, key=lambda x: x["id"]):
        r["novelty"] = 0.0
        if r["family"] and r["family"] not in claimed:
            r["novelty"] = 1.0
            claimed.add(r["family"])
    return rows


def _score_rows(tree: dict[str, Any], seen_families: Optional[set[str]] = None) -> list[dict[str, Any]]:
    """Shared scoring pass. `seen_families` seeds novelty so a replay can judge novelty against
    what the simulated run has already observed, not against the whole tree."""
    rows = score_nodes(tree)
    if seen_families:
        for r in rows:
            r["novelty"] = 0.0 if r["family"] in seen_families else (1.0 if r["family"] else 0.0)
    return rows


def regressed_criteria(tree: dict[str, Any], nid: str) -> list[dict[str, Any]]:
    """Criteria that got materially worse than the parent, per their own direction and noise.

    REPORTED, NEVER BLOCKING. Deciding whether a regression acceptable is the agent's call - the
    user chose that explicitly - but the evidence has to be impossible to miss, because the
    failure mode this guards against is a composite score hiding a real drop: BioAgent Bench saw
    Jaccard 0.43 across four identical runs, which is exactly the kind of stability loss an
    average would swallow.
    """
    nodes = _current(tree).get("nodes") or {}
    node = nodes.get(nid) or {}
    parent = nodes.get(node.get("parent")) or {}
    parent_criteria = {c.get("name"): c for c in (parent.get("criteria") or [])
                       if isinstance(c, dict)}
    out = []
    for c in (node.get("criteria") or []):
        if not isinstance(c, dict):
            continue
        name = c.get("name")
        prev = parent_criteria.get(name)
        if prev is None or "value" not in c or "value" not in prev:
            continue
        try:
            new_v, old_v = float(c["value"]), float(prev["value"])
        except (TypeError, ValueError):
            continue
        higher = str(c.get("direction") or "higher") != "lower"
        worse = new_v < old_v if higher else new_v > old_v
        if not worse:
            continue
        drop = (old_v - new_v) if higher else (new_v - old_v)
        std = ((c.get("samples") or {}).get("std")
               if isinstance(c.get("samples"), dict) else None)
        try:
            std = float(std) if std is not None else None
        except (TypeError, ValueError):
            std = None
        if std is not None and drop <= 2 * std:
            continue  # inside the noise band, not a real regression
        out.append({"name": name, "from": old_v, "to": new_v, "std": std})
    return out



def visit_counts(tree: dict[str, Any]) -> dict[str, int]:
    """How many children each node has spawned - the raw material for the cooling term."""
    counts: dict[str, int] = {}
    for node in (_current(tree).get("nodes") or {}).values():
        parent = (node or {}).get("parent")
        if parent:
            counts[parent] = counts.get(parent, 0) + 1
    return counts


def select_next(tree: dict[str, Any], weights: Optional[dict[str, float]] = None) -> dict[str, Any]:
    """Rank candidate parents by quality + progress + novelty, with visit cooling.

    This is the 'which node do I expand next' question, answered non-greedily. The return value
    is the full ranking plus the top pick, because a single pick hides the reasoning that makes
    the pick auditable - which is the difference between widening the search and wandering.
    """
    w = dict(DEFAULT_WEIGHTS)
    if isinstance(weights, dict):
        for k, v in weights.items():
            if k in w:
                try:
                    w[k] = float(v)
                except (TypeError, ValueError):
                    pass

    rows = score_nodes(tree)
    counts = visit_counts(tree)
    nodes = _current(tree).get("nodes") or {}
    families = {r["family"] for r in rows if r["family"]}
    # Aspire's failure was over-trusting a narrow self-evaluation: with one or two families the
    # novelty factor is nearly noise (first node always 1.0, the rest 0.0), so we say so rather
    # than presenting a confident-looking ranking built on two data points.
    too_small = len(families) < MIN_FAMILIES_FOR_NOVELTY

    for r in rows:
        # A refuted node is evidence, not a candidate. It stays in the tree so it is never
        # repeated; it simply never wins selection again.
        r["eligible"] = r["verdict"] != "revert" and not is_abandoned(nodes.get(r["id"]))
        visits = counts.get(r["id"], 0)
        r["visits"] = visits
        r["cooling"] = 0.5 ** (visits / COOLING_HALF_LIFE)
        r["utility"] = (
            w["score"] * r["scoreNorm"]
            + w["progress"] * r["progressNorm"]
            + w["novelty"] * r["novelty"]
        ) * r["cooling"]
        r["effectiveCost"] = effective_cost(nodes.get(r["id"]) or {})
        r["regressed"] = regressed_criteria(tree, r["id"])
        # "is this delta even real" becomes computable once samples exist
        if r["std"] is not None and r["delta"] is not None:
            try:
                r["withinNoise"] = abs(float(r["delta"])) < 2 * float(r["std"])
            except (TypeError, ValueError):
                r["withinNoise"] = None
        else:
            r["withinNoise"] = None

    ranked = sorted(
        (r for r in rows if r["eligible"]),
        key=lambda r: (-r["utility"], r["id"]),
    )
    for i, r in enumerate(ranked, 1):
        r["rank"] = i

    if not ranked:
        return {
            "ok": False,
            "reason": "no eligible scored node to expand; record a scored node first",
            "candidates": [],
        }

    top = ranked[0]
    spread = len(ranked) > 1 and abs(ranked[0]["utility"] - ranked[1]["utility"]) > 1e-9
    workers = 1
    if isinstance(weights, dict):
        try:
            workers = max(1, min(16, int(weights.get("workers", 1))))
        except (TypeError, ValueError):
            workers = 1
    return {
        "ok": True,
        "weights": w,
        "coolingHalfLife": COOLING_HALF_LIFE,
        "recommendedParent": top["id"],
        # The action is a BATCH, not a single node: this is the decision interface the paper
        # uses online and in replay alike, and |batch| <= workers is what the parallelism term
        # in the objective rewards.
        "batch": [r["id"] for r in ranked[:workers]],
        "workers": workers,
        "marginal": spread,
        "confidence": "low" if too_small else "high",
        "familyCount": len(families),
        "treeTooSmallForNovelty": too_small,
        "note": (
            "the top node is not simply the highest score. If a lower-scoring node wins, it won "
            "on progress or novelty - that is the search being widened on purpose."
            if top["scoreNorm"] < max((r["scoreNorm"] for r in ranked), default=1.0) - 1e-9
            else "the current incumbent also leads on quality, progress and novelty."
        ),
        "confidenceNote": (
            f"only {len(families)} method family/families so far, so the novelty factor is close to "
            f"noise; treat this ranking as a hint until there are at least {MIN_FAMILIES_FOR_NOVELTY}"
            if too_small else
            f"{len(families)} method families; novelty is discriminating between them."
        ),
        "candidates": [
            {
                "id": r["id"],
                "utility": round(r["utility"], 4),
                "score": r["score"],
                "scoreNorm": round(r["scoreNorm"], 3),
                "progress": (
                    None if r["progress"] is None else round(r["progress"], 4)
                ),
                "progressNorm": round(r["progressNorm"], 3),
                "novelty": r["novelty"],
                "family": r["family"],
                "operator": r["operator"],
                "visits": r["visits"],
                "cooling": round(r["cooling"], 3),
                "std": r["std"],
                "withinNoise": r["withinNoise"],
                "effectiveCost": r["effectiveCost"],
                "regressedCriteria": r["regressed"],
            }
            for r in ranked[:10]
        ],
    }


def _expectation_tally(nodes: dict[str, Any]) -> dict[str, Any]:
    """How often predictions came true, and the cross-tab that actually teaches something.

    The single number "12 of 15 confirmed" is nearly useless on its own. The informative
    split is kept-vs-refuted against confirmed-vs-refuted, because the cell that matters is
    KEPT BUT NOT AS PREDICTED: the change is worth keeping and it worked for a reason nobody
    predicted. That is simultaneously good news (the gain is real) and bad news (the model of
    why is wrong, so the next change built on it is a guess). A board that only prints the
    confirmation rate hides exactly that.
    """
    tally = {"confirmed": 0, "partial": 0, "refuted": 0, "unreadable": 0,
             "keptConfirmed": 0, "keptNotAsPredicted": 0, "revertedAsPredicted": 0,
             "unreadableButKept": 0}
    surprise: list[str] = []
    for nid, node in nodes.items():
        if not isinstance(node, dict) or is_abandoned(node):
            continue
        judgment = node.get("expectation")
        if not isinstance(judgment, dict) and not isinstance(node.get("metric"), dict):
            continue  # a declaration has no result yet; it is not a failed prediction
        verdict = str((judgment or {}).get("verdict") or "unreadable")
        tally[verdict] = tally.get(verdict, 0) + 1
        kept = node.get("verdict") == "keep"
        if kept and verdict == "confirmed":
            tally["keptConfirmed"] += 1
        elif kept and verdict in ("refuted", "partial"):
            tally["keptNotAsPredicted"] += 1
            surprise.append(nid)
        elif node.get("verdict") == "revert" and verdict == "refuted":
            tally["revertedAsPredicted"] += 1
        elif kept and verdict == "unreadable":
            tally["unreadableButKept"] += 1
    judged = sum(tally[k] for k in ("confirmed", "partial", "refuted"))
    return {
        **tally,
        "judged": judged,
        "keptNotAsPredictedNodes": surprise[:10],
        "note": (
            f"{judged} node(s) carried a prediction. {tally['keptNotAsPredicted']} of the kept "
            f"ones worked for a reason that was not predicted - the gain is real but the "
            f"explanation is not, so the next change built on it is a guess."
            if tally["keptNotAsPredicted"] else
            f"{judged} node(s) carried a prediction and the kept ones did what they said."
            if judged else
            "no node carries a prediction, so nothing here can say whether the search "
            "understands why its changes work."
        ),
    }


def _criterion_role_summary(nodes: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per criterion, with the role that says whether it is meant to be optimised."""
    seen: dict[str, dict[str, Any]] = {}
    for node in nodes.values():
        if not isinstance(node, dict) or is_abandoned(node):
            continue
        for c in node.get("criteria") or []:
            if not isinstance(c, dict) or not c.get("name"):
                continue
            row = seen.setdefault(str(c["name"]), {
                "name": c["name"], "role": c.get("role") or DEFAULT_CRITERION_ROLE,
                "seen": 0, "best": None, "worst": None,
            })
            row["seen"] += 1
            if row["role"] == "observe" and c.get("role"):
                row["role"] = c["role"]
            try:
                v = float(c.get("value"))
            except (TypeError, ValueError):
                continue
            higher = str(c.get("direction", "higher")) != "lower"
            if higher:
                row["best"] = v if row["best"] is None else max(row["best"], v)
                row["worst"] = v if row["worst"] is None else min(row["worst"], v)
            else:
                row["best"] = v if row["best"] is None else min(row["best"], v)
                row["worst"] = v if row["worst"] is None else max(row["worst"], v)
    order = {"primary": 0, "guard": 1, "observe": 2}
    return sorted(seen.values(), key=lambda r: (order.get(r["role"], 3), r["name"]))


def experience_board(tree: dict[str, Any]) -> dict[str, Any]:
    """The population-level view: families, their best, what failed, what is unexplored.

    The paper's insight is that a node's own history is not enough - what matters is how its
    neighbours and method families did. This is that view, derived deterministically from the
    tree so it cannot be embellished by an agent that would rather report a tidy story.
    """
    nodes = _current(tree).get("nodes") or {}
    families: dict[str, dict[str, Any]] = {}
    failures: dict[str, list[str]] = {}
    operator_gain: dict[str, float] = {}

    for nid, node in nodes.items():
        if not isinstance(node, dict) or is_abandoned(node):
            continue
        fam = str(node.get("family") or "").strip()
        if node.get("kind") == "experiment" and fam:
            entry = families.setdefault(fam, {
                "family": fam, "nodes": 0, "best": None, "bestNode": None,
                "operators": [], "kept": 0, "refuted": 0,
            })
            entry["nodes"] += 1
            if node.get("operator"):
                entry["operators"].append(node["operator"])
            if node.get("verdict") == "keep":
                entry["kept"] += 1
            elif node.get("verdict") == "revert":
                entry["refuted"] += 1
            s = _signed_score(node.get("metric"))
            if s is not None and (entry["best"] is None or s > entry["best"]):
                entry["best"] = s
                entry["bestNode"] = nid
            d = _signed_delta(node.get("metric"))
            if d is not None and node.get("operator"):
                operator_gain[node["operator"]] = operator_gain.get(node["operator"], 0.0) + d

        if node.get("verdict") == "revert" and fam:
            failures.setdefault(fam, []).append(nid)

    ranked_families = sorted(
        families.values(),
        key=lambda e: (-(e["best"] if e["best"] is not None else float("-inf")), e["family"]),
    )
    return {
        "ok": True,
        "families": ranked_families,
        "familyCount": len(families),
        "refutedByFamily": failures,
        "expectations": _expectation_tally(nodes),
        "criterionRoles": _criterion_role_summary(nodes),
        "abandoned": [nid for nid, n in nodes.items() if is_abandoned(n)],
        "operatorGain": {
            k: round(v, 4) for k, v in sorted(
                operator_gain.items(), key=lambda kv: -kv[1])
        },
        "efc": _efc_summary(nodes),
        "failureLayers": _failure_layer_counts(nodes),
        "operatorNote": (
            "OpenMLE reports that Improve and Crossover produced 85-92% of total measured gain "
            "while Draft and Debug mostly established that a program ran at all. If your Draft "
            "and Debug nodes carry most of the gain, that is a finding, not a success."
        ),
    }


def _efc_summary(nodes: dict[str, Any]) -> dict[str, Any]:
    """Effective-vs-raw compute, and the per-criterion breakdown behind it.

    The breakdown is deliberately not collapsed: "no new information about correctness but a
    cheaper run" is a different finding from the reverse, and averaging them throws away the
    thing that makes the number worth computing.
    """
    raw = eff = 0.0
    per_criterion: dict[str, dict[str, int]] = {}
    for node in nodes.values():
        if not isinstance(node, dict) or node.get("kind") != "experiment":
            continue
        cost = node.get("cost")
        if isinstance(cost, dict):
            try:
                raw += float(cost.get("quotaHours") or 0.0)
            except (TypeError, ValueError):
                pass
        eff += effective_cost(node)
        for c in node.get("criteria") or []:
            if not isinstance(c, dict) or not c.get("name"):
                continue
            entry = per_criterion.setdefault(str(c["name"]), {
                "informative": 0, "redundant": 0, "valid": 0, "retained": 0,
            })
            flags = c.get("efc")
            if isinstance(flags, dict):
                for flag in EFC_FLAGS:
                    if flags.get(flag) is True:
                        entry[flag] += 1
    wasted = raw - eff
    return {
        "rawQuotaHours": round(raw, 4),
        "effectiveQuotaHours": round(eff, 4),
        "wastedQuotaHours": round(wasted, 4),
        "wastedFraction": (round(wasted / raw, 4) if raw > 0 else None),
        "perCriterion": per_criterion,
        "note": (
            "per-criterion EFC is reported separately, never merged into one boolean: a run can be "
            "redundant about correctness and informative about cost, and collapsing those loses "
            "the finding."
        ),
    }


def _failure_layer_counts(nodes: dict[str, Any]) -> dict[str, int]:
    """How the refutations break down by harness layer. This is the actionable split."""
    out: dict[str, int] = {}
    for node in nodes.values():
        if not isinstance(node, dict) or node.get("verdict") != "revert":
            continue
        layer = str(node.get("failureLayer") or "unclassified")
        out[layer] = out.get(layer, 0) + 1
    return out


# ------------------------------------------------------- replay (Dream-RSI)
# Adapted from arXiv 2609.14858 (Dream-RSI), which makes one move we do not: it treats a
# completed discovery history as a REPLAY SIMULATOR. An alternative exploration policy is
# evaluated by walking the pre-recorded tree - different branches, different order, different
# parallelism, different stopping point - and simply reading the outcomes already stored there.
# No agent is re-run, no evaluator is re-run, no quota is spent.
#
# THE ONE PLACE WE DEPART FROM THE PAPER, and it is not cosmetic:
# their tree gives every non-root node at most one child, so Child(v) returns "v's unique
# recorded child" and the structure is a bundle of chains hanging off the root. Ours is a real
# DAG: a parent can have several children. So a selected batch may reveal more than one node and
# N grows accordingly. Consequence: our replay score is NOT numerically comparable to theirs.
# `revealed` is returned in full so a reader can check the walk by hand.
#
# Policies are DATA (weights, betas, workers, maxRounds). Nothing here is ever eval'd or executed.

ROOT_ID = "__root__"


def _policy_params(params: Optional[dict[str, Any]]) -> dict[str, Any]:
    base = {
        "weights": dict(DEFAULT_WEIGHTS),
        "betaCost": 0.0,
        "betaParallel": 0.0,
        "workers": 1,
        "maxRounds": 12,
    }
    if isinstance(params, dict):
        w = params.get("weights")
        if isinstance(w, dict):
            for key in base["weights"]:
                value = w.get(key)
                if isinstance(value, (int, float)) and value == value:
                    base["weights"][key] = float(value)
        for key in ("betaCost", "betaParallel", "workers", "maxRounds"):
            value = params.get(key)
            if isinstance(value, (int, float)) and value == value:
                base[key] = value
    # clamp, so a hand-edited policy cannot ask for a nonsensical search
    base["workers"] = max(1, min(16, int(base["workers"])))
    base["maxRounds"] = max(1, min(200, int(base["maxRounds"])))
    base["betaCost"] = max(0.0, float(base["betaCost"]))
    base["betaParallel"] = max(0.0, float(base["betaParallel"]))
    return base


def effective_cost(node: dict[str, Any]) -> float:
    """Quota spent that actually bought information.

    EFC's point (arXiv 2605.29682) is that raw tokens, tool calls, wall time and cost
    "cannot distinguish useful feedback from redundant or unstable interaction". A node whose
    every criterion is `redundant` taught us nothing, so its compute counts as zero against the
    objective even though it was really spent. Absence of `criteria` falls back to the node count
    the objective used before, and says so via `costDataIncomplete`.
    """
    quota = 0.0
    cost = node.get("cost")
    if isinstance(cost, dict):
        try:
            quota = float(cost.get("quotaHours") or 0.0)
        except (TypeError, ValueError):
            quota = 0.0
    criteria = node.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        return quota
    flags = [c.get("efc") for c in criteria if isinstance(c, dict)]
    flags = [f for f in flags if isinstance(f, dict)]
    if not flags:
        return quota
    informative = any(f.get("informative") is True for f in flags)
    return quota if informative else 0.0


def _children_in_order(tree: dict[str, Any], nid: str) -> list[str]:
    """Child ids of a node, in insertion order, which is the order they were actually tried.

    A node with ``parent: None`` is a child of the virtual root - that is how a real tree
    records its first experiments, and replay has to agree with that or it would find no
    branches at all.
    """
    nodes = _current(tree).get("nodes") or {}
    # An abandoned line is not an edge the search may walk. Excluding it here rather than at
    # each caller means replay, select and the board all agree by construction - three
    # separate filters is three chances to forget one of them.
    alive = {k: v for k, v in nodes.items() if not is_abandoned(v)}
    if nid == ROOT_ID:
        return [k for k, v in alive.items() if not v.get("parent")]
    return [k for k, v in alive.items() if v.get("parent") == nid]


def replay(tree: dict[str, Any], params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Walk a recorded tree under one policy, reading only what is already stored.

    Pure: it never writes, never re-runs anything, and is deterministic - the same tree and the
    same params always give the same V, N and k, which is what makes two policies comparable.
    """
    p = _policy_params(params)
    inner = _current(tree)
    nodes = inner.get("nodes") or {}
    workers = p["workers"]
    max_rounds = p["maxRounds"]

    observed: set[str] = set()
    revealed_order: list[list[str]] = []
    spent = 0.0
    rounds = 0

    def best_observed() -> Optional[float]:
        best = None
        for nid in observed:
            if nid == ROOT_ID:
                continue
            s = _signed_score((nodes.get(nid) or {}).get("metric"))
            if s is not None and (best is None or s > best):
                best = s
        return best

    while rounds < max_rounds:
        rounds += 1
        # eligible = root plus the current leaves of the observed tree
        eligible = [ROOT_ID] + [n for n in observed
                                if n != ROOT_ID and not (set(_children_in_order(inner, n)) - observed)]
        if not eligible:
            break

        ranked = _rank_candidates(inner, eligible, p, observed)
        batch = [c["id"] for c in ranked[:workers]]

        newly: list[str] = []
        for nid in batch:
            if nid == ROOT_ID:
                # opening a further branch: the earliest recorded child not yet seen
                kids = [k for k in _children_in_order(inner, ROOT_ID) if k not in observed]
                targets = kids[:1]
            else:
                targets = [k for k in _children_in_order(inner, nid) if k not in observed]
            for k in targets:
                if k not in observed:
                    newly.append(k)
                    observed.add(k)
                    spent += effective_cost(nodes.get(k) or {})

        if not newly:
            break
        revealed_order.append(newly)

    n = len([x for x in observed if x != ROOT_ID])
    best = best_observed()
    quality = best if best is not None else 0.0
    value = quality - p["betaCost"] * spent + p["betaParallel"] * n / max(1, rounds)
    return {
        "ok": True,
        "params": p,
        "V": value,
        "quality": quality,
        "effectiveCost": spent,
        "N": n,
        "k": rounds,
        "revealed": revealed_order,
        "nodesInTree": len(nodes),
        "costDataIncomplete": _cost_incomplete(inner, observed),
        "batchesPerRound": workers,
    }


def _cost_incomplete(inner: dict[str, Any], observed: set[str]) -> bool:
    """True when any revealed node predates cost/criteria, so beta*understates the truth."""
    nodes = _current(inner).get("nodes") or {}
    for nid in observed:
        node = nodes.get(nid) or {}
        if not isinstance(node.get("cost"), dict) or not node.get("criteria"):
            return True
    return False


def compare(tree: dict[str, Any], candidates: list[dict[str, Any]],
            deployed: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Score several policies over the same history and refuse to leave things worse.

    The monotone guarantee comes from the candidate set containing the CURRENT policy. That is
    not a nicety: Dream-RSI's selection is argmax over {current} u candidates precisely so the
    result is never worse than what is deployed, and the same reasoning answers the "safe
    inheritance" failure in arXiv 2609.11873 - persistence is not the same as improvement.
    """
    baseline = deployed if isinstance(deployed, dict) else None
    if baseline is None:
        return {
            "ok": False,
            "code": "no_baseline",
            "message": (
                "no deployed policy was supplied, so there is no baseline to be monotone against. "
                "Pass the currently deployed policy as the first candidate."
            ),
        }

    results = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        r = replay(tree, c.get("params") or c)
        results.append({
            "id": c.get("id") or c.get("label"),
            "label": c.get("label"),
            "isCurrent": c is baseline or c.get("id") == baseline.get("id"),
            "V": r["V"], "N": r["N"], "k": r["k"],
            "effectiveCost": r["effectiveCost"],
            "costDataIncomplete": r["costDataIncomplete"],
        })

    if not results:
        return {"ok": False, "code": "no_candidates", "message": "no usable candidate policies"}

    base = next((r for r in results if r["isCurrent"]), None)
    if base is None:
        return {
            "ok": False,
            "code": "baseline_missing",
            "message": (
                "the candidate set does not contain the currently deployed policy, so nothing "
                "here can be shown to be an improvement. Include it and compare again."
            ),
            "results": results,
        }

    best = max(results, key=lambda r: r["V"])
    monotone = best["V"] >= base["V"] - 1e-9
    return {
        "ok": True,
        "results": results,
        "current": base,
        "best": best,
        "monotone": monotone,
        "guarantee": (
            f"selected policy scores {best['V']:.4f} vs current {base['V']:.4f}; the candidate set "
            "contains the current policy, so the choice is never worse than what is deployed."
            if monotone else
            f"selected policy scores {best['V']:.4f} vs current {base['V']:.4f}; rounding noise "
            "only - the deployed policy is kept."
        ),
    }


def _rank_candidates(inner: dict[str, Any], eligible: list[str], p: dict[str, Any],
                     observed: set[str]) -> list[dict[str, Any]]:
    """Score the eligible set with the policy's weights, so replay honours a tuned policy."""
    nodes = _current(inner).get("nodes") or {}
    pseudo = {
        "base": {"id": ROOT_ID, "label": "root", "parent": None},
        "nodes": {ROOT_ID: {"id": ROOT_ID, "kind": "experiment", "parent": None,
                            "metric": None, "family": "", "operator": None,
                            "verdict": None}},
    }
    for nid in eligible:
        if nid == ROOT_ID:
            continue
        node = nodes.get(nid) or {}
        pseudo["nodes"][nid] = node
    # novelty must be judged against what the replay has already seen, not the whole tree
    seen: set[str] = set()
    for nid in sorted(observed):
        fam = str((nodes.get(nid) or {}).get("family") or "")
        if fam:
            seen.add(fam)
    return _score_and_cool(pseudo, p, seen)


def _score_and_cool(pseudo: dict[str, Any], p: dict[str, Any],
                    seen_families: set[str]) -> list[dict[str, Any]]:
    rows = _score_rows(pseudo, seen_families)
    w = p["weights"]
    for r in rows:
        visits = len(_children_in_order(pseudo, r["id"]))
        r["cooling"] = 0.5 ** (visits / COOLING_HALF_LIFE)
        r["utility"] = (
            w["score"] * r["scoreNorm"]
            + w["progress"] * r["progressNorm"]
            + w["novelty"] * r["novelty"]
        ) * r["cooling"]
    return sorted(rows, key=lambda r: -r["utility"])


# ------------------------------------------------------- rounds, policies, anchor

def close_round(competition: str, policy_id: Optional[str] = None,
                read_revision: Optional[int] = None) -> dict[str, Any]:
    """Archive the current tree into rounds[] and open a fresh one.

    The archived rounds ARE the replay pool. Gated on the same read-revision as record, because
    archiving and recording race each other: a node written after the archive would land in the
    new round having been planned against the old one.
    """
    tree = load(competition)
    if read_revision is None:
        return {"ok": False, "code": "read_required",
                "message": "read the tree before closing a round"}
    if int(read_revision) != int(tree["revision"]):
        return {"ok": False, "code": "stale_read",
                "message": "the tree changed since you read it; read again before closing a round"}
    node_count = len(_current(tree).get("nodes") or {})
    if node_count == 0:
        return {"ok": False, "code": "empty_round",
                "message": "the current round has no nodes; there is nothing to archive"}
    tree["rounds"].append({
        "round": tree["currentRound"],
        "closedAt": _now(),
        "policyId": policy_id or tree.get("deployedPolicy"),
        "nodeCount": node_count,
        # A round archived before the node-shape rules existed is a biased sample, and the
        # replay built on it inherits that bias. Recorded so the report can say so.
        "compliant": all(_node_compliant(n) for n in (_current(tree).get("nodes") or {}).values()),
        "tree": _current(tree),
    })
    archived_tree = _current(tree)
    tree["tree"] = {"base": {"id": "", "label": "", "parent": None}, "nodes": {}}
    tree["currentRound"] = int(tree["currentRound"]) + 1
    tree["revision"] = int(tree["revision"]) + 1
    tree["updatedAt"] = _now()
    # the journal keeps the pre-close state so undo can put the tree back
    _journal(tree, "round_close",
             {"currentRound": tree["currentRound"] - 1, "tree": archived_tree},
             {"currentRound": tree["currentRound"]})
    save(competition, tree)
    return {"ok": True, "rounds": len(tree["rounds"]), "currentRound": tree["currentRound"],
            "archived": node_count, "revision": tree["revision"]}


def _node_compliant(node: Any) -> bool:
    if not isinstance(node, dict) or node.get("kind") != "experiment":
        return True
    return bool(node.get("operator") and node.get("family"))


def register_policy(competition: str, params: dict[str, Any], label: str = "",
                    note: str = "") -> dict[str, Any]:
    tree = load(competition)
    n = 1
    while f"p{n}" in tree["policies"]:
        n += 1
    pid = f"p{n}"
    entry = {
        "id": pid,
        "parent": tree.get("deployedPolicy"),
        "createdAt": _now(),
        "label": label or pid,
        # Validate the RAW params first. Clamping first would silently "fix" nonsense like
        # workers=0 into a valid policy, and a policy nobody was told had been altered is
        # worse than a rejected one.
        "params": dict(params) if isinstance(params, dict) else params,
        "note": note,
    }
    problems = _validate_policy(pid, entry)
    if problems:
        return {"ok": False, "code": "bad_policy", "message": "; ".join(problems)}
    entry["params"] = _policy_params(params)
    tree["policies"][pid] = entry
    tree["revision"] = int(tree["revision"]) + 1
    tree["updatedAt"] = _now()
    # Deliberately NOT journalled. Registering a policy is additive and changes no behaviour -
    # nothing is deployed, no run is affected. Journalling it would push every subsequent undo
    # off the operation that actually changed something.
    save(competition, tree)
    return {"ok": True, "policy": entry, "revision": tree["revision"]}


def deploy_policy(competition: str, policy_id: str) -> dict[str, Any]:
    tree = load(competition)
    if policy_id not in tree["policies"]:
        return {"ok": False, "code": "unknown_policy",
                "message": f"policy {policy_id!r} is not registered; create it first"}
    before = tree.get("deployedPolicy")
    tree["deployedPolicy"] = policy_id
    tree["revision"] = int(tree["revision"]) + 1
    tree["updatedAt"] = _now()
    _journal(tree, "deploy_policy", {"deployedPolicy": before}, {"deployedPolicy": policy_id})
    save(competition, tree)
    return {"ok": True, "deployedPolicy": policy_id, "previous": before,
            "revision": tree["revision"],
            "note": "undoable with action=\"undo\" if this was the wrong call"}


def _samples_consistent(samples: Any) -> bool:
    """Do the recorded repeats add up to the mean they claim?

    Same rule `validate` applies to `result == samples.mean`, applied here for the other reason:
    a noise floor computed from two readings is not a measurement, and a mean that disagrees with
    its own values is a transcription error rather than a spread.
    """
    if not isinstance(samples, dict):
        return False
    try:
        n = int(samples.get("n"))
        values = samples.get("values")
        mean = float(samples.get("mean"))
    except (TypeError, ValueError):
        return False
    if n < 3 or not isinstance(values, list) or len(values) != n:
        return False
    try:
        actual = sum(float(v) for v in values) / n
    except (TypeError, ValueError):
        return False
    return abs(actual - mean) <= 1e-9


def calibrate(competition: str, ruler: Any, read_revision: Optional[int] = None) -> dict[str, Any]:
    """Record how finely this metric can resolve a difference, and how much room is left.

    The anchor answers "which set may I not score on". This answers the other question, and it is
    the expensive one to get wrong: a run declared at a delta the metric cannot resolve spends a
    real quota slot and returns a number a re-roll would have produced too.

    Three requirements, and each one exists to stop this action degenerating into a field the
    caller fills with a number it made up:

    - at least three repeats, whose values agree with the mean they claim. Two readings do not
      describe a spread.
    - a noise figure, taken from those repeats when the caller does not state one.
    - at least one of `seedSpread` / `rebuildSpread`. One without the other does not say which
      layer the variance came from, and the two call for opposite remedies: a wide seed spread
      wants more repetitions, a wide rebuild spread wants the rebuild taken out of the measured
      path, because adding repetitions to the wrong layer measures nothing new.

    Unlike the anchor, this is meant to be overwritten. A stale noise floor starts refusing
    correct experiments, which is worse than having none.
    """
    tree = load(competition)
    if read_revision is not None and int(read_revision) != int(tree["revision"]):
        return {"ok": False, "code": "stale_read",
                "message": f"the tree has changed since you read it (you read revision "
                           f"{read_revision}, it is now {tree['revision']}). Read it again."}
    if not isinstance(ruler, dict):
        return {"ok": False, "code": "bad_ruler", "message": "calibrate takes a ruler object"}
    bad: list[str] = []
    samples = ruler.get("samples")
    if samples is not None and not _samples_consistent(samples):
        bad.append("ruler.samples must carry n >= 3 with a values list of that length whose mean "
                   "equals the stated mean - a spread computed from two readings is not a "
                   "measurement, and a mean that disagrees with its own values is a typo")
    try:
        seed = ruler.get("seedSpread")
        rebuild = ruler.get("rebuildSpread")
        seed = float(seed) if seed is not None else None
        rebuild = float(rebuild) if rebuild is not None else None
    except (TypeError, ValueError):
        seed, rebuild = None, None
    if seed is None and rebuild is None:
        bad.append("give seedSpread (repeats that varied only the seed) or rebuildSpread (repeats "
                   "that rebuilt the measured artifact - feature cache, preprocessing fold, a "
                   "previous round's prediction file), or both. Without one of these the variance "
                   "has no known layer and there is no way to tell which remedy applies.")
    try:
        noise = float(ruler["noise"]) if ruler.get("noise") is not None else None
    except (TypeError, ValueError):
        noise = None
    if noise is None and isinstance(samples, dict) and samples.get("std") is not None:
        try:
            noise = abs(float(samples["std"]))
        except (TypeError, ValueError):
            noise = None
    if noise is None or noise <= 0:
        bad.append("ruler.noise is required, and must be above zero. State it, or supply samples "
                   "with a std for it to be taken from. A noise floor of zero would gate nothing "
                   "while looking like a calibration.")
    if bad:
        return {"ok": False, "code": "bad_ruler", "message": "\n".join(bad)}

    before = tree.get("ruler") or {}
    stored = {
        "noise": noise,
        "noiseFrom": ruler.get("noiseFrom") or "calibration samples",
        "samples": samples,
        "headroom": ruler.get("headroom"),
        "smallestActionable": ruler.get("smallestActionable"),
        "seedSpread": seed,
        "rebuildSpread": rebuild,
        "calibratedAt": _now(),
    }
    tree["ruler"] = stored
    tree["revision"] = int(tree["revision"]) + 1
    _journal(tree, "calibrate", {"ruler": before}, {"ruler": stored})
    save(competition, tree)
    missing = [k for k in ("headroom", "smallestActionable") if stored.get(k) is None]
    return {"ok": True, "ruler": stored, "revision": tree["revision"],
            "sideCheck": ruler_side_check(tree, stored),
            "gates": ("declare now refuses an atLeast below this noise floor"
                      if noise > 0 else "this calibration gates nothing"),
            "incomplete": missing,
            "note": (f"declare quotes {', '.join(missing)} when it refuses. Until you fill them in, "
                     f"the refusal states the floor but cannot say how much room is left or how "
                     f"small a win would be worth acting on."
                     if missing else None)}


def ruler_side_check(tree: dict[str, Any], ruler: dict[str, Any]) -> dict[str, Any]:
    """Do the search side and the held-out side read alike, or is the split buying a re-roll?

    Not the same question the anchor asks. The anchor stops a node from scoring on the held-out
    set; this asks whether the two sides are even comparable, because a split chosen to look good
    buys regression to the mean - the cases were selected for being extreme, so they drift back
    toward the population on a re-run and the gain was never there.
    """
    noise = ruler.get("noise")
    if noise is None:
        return {"checked": False, "why": "no calibrated noise floor, so there is no band to "
                                         "compare the two sides against"}
    anchor = tree.get("anchor") or {}
    if not anchor.get("declared") or not anchor.get("heldOut"):
        return {"checked": False, "why": "no anchor is declared, so there is no second side to "
                                         "compare against"}
    inner = _current(tree)
    nodes = inner.get("nodes") or {}
    by_split: dict[str, list[float]] = {}
    for node in nodes.values():
        if not isinstance(node, dict) or is_planned(node) or is_abandoned(node):
            continue
        metric = node.get("metric")
        if not isinstance(metric, dict):
            continue
        split = str(metric.get("split") or "").strip()
        if not split:
            continue
        try:
            by_split.setdefault(split, []).append(float(metric.get("result")))
        except (TypeError, ValueError):
            continue
    held = str(anchor.get("heldOut"))
    named = {k: v for k, v in by_split.items() if held.lower() in k.lower()}
    others = {k: v for k, v in by_split.items() if held.lower() not in k.lower()}
    if not named or not others:
        return {"checked": False, "why": "both sides need settled nodes carrying metric.split "
                                         "before the two can be compared"}
    def mean(xs: list[float]) -> float:
        return sum(xs) / len(xs)
    h = mean(named[next(iter(named))])
    o = mean([v for xs in others.values() for v in xs])
    gap = abs(h - o)
    return {"checked": True, "holdoutMean": h, "searchMean": o, "gap": gap,
            "withinNoise": gap < float(noise),
            "holdoutNodes": sum(len(v) for v in named.values()),
            "searchNodes": sum(len(v) for v in others.values()),
            "note": ("the two sides read alike, so the split is not selecting for extremes"
                     if gap < float(noise) else
                     f"the sides differ by {gap:.4g}, which is outside the noise floor "
                     f"{float(noise):.4g}. A split chosen by score buys regression to the mean, "
                     f"so expect the held-out side to drift back on its own. Re-draw before the "
                     f"next run rather than after several have been read as wins.")}


def regrade(competition: str, read_revision: Optional[int] = None) -> dict[str, Any]:
    """Re-judge every recorded node under the CURRENT calibration, and report what moved.

    This does not rewrite anything. The tree is append-only and ids are never reused - that is the
    whole reason it can be cited as evidence - so re-judging is not editing history, it is running
    the same stored results past a different ruler and keeping both answers.

    Read it when the measuring surface changed underneath the tree: a new CV scheme, a different
    judging rule, or the discovery that the public leaderboard was already being fitted to.

    Two outcomes, and they are not the same. If the prior best is still best but its lead has
    collapsed into the noise, the earlier rounds were real and the resolution was not there to see
    them - keep going. If the ranking flipped, the search was optimising something the previous
    calibration could not distinguish, and the honest move is back to the baseline.
    """
    tree = load(competition)
    if read_revision is not None and int(read_revision) != int(tree["revision"]):
        return {"ok": False, "code": "stale_read",
                "message": f"the tree has changed since you read it (you read revision "
                           f"{read_revision}, it is now {tree['revision']}). Read it again."}
    ruler = tree.get("ruler") or {}
    inner = _current(tree)
    nodes = inner.get("nodes") or {}
    prior: dict[str, str] = {}
    now: dict[str, str] = {}
    detail: dict[str, Any] = {}
    for nid, node in sorted(nodes.items()):
        if not isinstance(node, dict) or is_planned(node) or is_abandoned(node):
            continue
        stored = node.get("expectation")
        expect, metric = node.get("expect"), node.get("metric")
        if not isinstance(expect, dict) and not isinstance(metric, dict):
            continue  # a declaration, not a failed prediction
        if isinstance(stored, dict) and stored.get("verdict"):
            prior[nid] = str(stored["verdict"])
        judgment = judge_expectation(expect, metric, ruler)
        now[nid] = str(judgment.get("verdict"))
        detail[nid] = {"prior": prior.get(nid), "now": now[nid],
                       "delta": judgment.get("delta"), "floor": judgment.get("floor"),
                       "floorFrom": judgment.get("floorFrom")}
    flipped = sorted(nid for nid in now if nid in prior and prior[nid] != now[nid])
    if not prior:
        return {"ok": False, "code": "nothing_to_regrade",
                "message": "no settled node carries a judged prediction yet, so there is nothing "
                           "to re-judge. Settle at least one run first."}
    if not ruler:
        return {"ok": False, "code": "no_ruler",
                "message": "this tree has no calibration, so re-judging would reproduce the "
                           "verdicts already stored. Run action=\"calibrate\" first - the point of "
                           "this action is to see the same results under a different floor."}

    def best_of(verdicts: dict[str, str]) -> tuple[str, float] | None:
        best: tuple[str, float] | None = None
        for nid, v in verdicts.items():
            node = nodes.get(nid) or {}
            if node.get("verdict") != "keep" or v != "confirmed":
                continue
            d = _num((node.get("metric") or {}).get("delta"))
            if best is None or d > best[1]:
                best = (nid, d)
        return best

    base_id = (inner.get("base") or {}).get("id") or ""
    base_result = _num(((nodes.get(base_id) or {}).get("metric") or {}).get("result"))
    prior_best, now_best = best_of(prior), best_of(now)
    noise = float(ruler.get("noise") or 0.0)
    prior_lead = (prior_best[1] - base_result) if prior_best else None
    now_lead = (now_best[1] - base_result) if now_best else None
    collapsed = bool(prior_lead is not None and now_lead is not None
                     and prior_lead >= 0 and now_lead < noise)
    still_best = bool(prior_best and now_best and prior_best[0] == now_best[0])
    if not prior_best:
        recommendation = ("nothing was a kept, confirmed node under the old calibration, so there "
                          "is no ranking to have flipped. Treat the stored verdicts as unreadable "
                          "and read the new ones as the first real judgement.")
    elif still_best and not collapsed:
        recommendation = (f"{prior_best[0]} is still the best node and its lead over the baseline "
                          f"({now_lead:+.4g}) is still outside the noise floor ({noise:.4g}). The "
                          f"earlier rounds hold.")
    elif still_best:
        recommendation = (f"{prior_best[0]} is still best, but its lead over the baseline collapsed "
                          f"from {prior_lead:+.4g} to {now_lead:+.4g}, which is inside the noise "
                          f"floor ({noise:.4g}). The gains were probably real and the old "
                          f"calibration could not resolve them. Keep the node, and size the next "
                          f"experiment against the resolution rather than the last delta.")
    else:
        recommendation = (f"the ranking flipped: {prior_best[0]} was best and "
                          f"{now_best[0] if now_best else 'nothing'} is best under the current "
                          f"calibration. Those rounds were optimising something this metric could "
                          f"not distinguish. Start again from the baseline rather than building "
                          f"on the old best.")
    return {"ok": True, "revision": tree["revision"], "rulerNoise": noise,
            "nodesJudged": len(now), "rankingFlipped": bool(prior_best and now_best
                                                            and prior_best[0] != now_best[0]),
            "verdictsFlipped": len(flipped), "flippedNodes": flipped,
            "priorBest": prior_best[0] if prior_best else None,
            "currentBest": now_best[0] if now_best else None,
            "priorBestStillBest": still_best,
            "leadOverBaseline": {"baseline": base_result, "prior": prior_lead, "now": now_lead},
            "collapsesIntoNoise": collapsed,
            "recommendation": recommendation, "detail": detail}


def declare_anchor(competition: str, held_out: str, rule: str = "") -> dict[str, Any]:
    tree = load(competition)
    tree["anchor"] = {
        "declared": True,
        "heldOut": held_out.strip(),
        "rule": rule or "no node may record a metric measured on the held-out set",
        "declaredAt": _now(),
    }
    tree["revision"] = int(tree["revision"]) + 1
    _journal(tree, "declare_anchor", {"anchor": {"declared": False}}, {"anchor": tree["anchor"]})
    save(competition, tree)
    return {"ok": True, "anchor": tree["anchor"], "revision": tree["revision"]}


def _journal(tree: dict[str, Any], op: str, before: Any, after: Any) -> None:
    tree.setdefault("journal", []).append({
        "seq": len(tree.get("journal") or []) + 1,
        "at": _now(),
        "op": op,
        "before": before,
        "after": after,
        "undoable": True,
    })


def undo(competition: str) -> dict[str, Any]:
    """Step back one recorded state change. The DeepSeek Harness rule: if you can change it, you
    must be able to register how to change it back. Nodes are append-only and never journalled."""
    tree = load(competition)
    journal = tree.get("journal") or []
    idx = next((i for i in range(len(journal) - 1, -1, -1)
                if journal[i].get("undoable")), None)
    if idx is None:
        return {"ok": False, "code": "nothing_to_undo",
                "message": "no undoable operation is recorded"}
    entry = journal.pop(idx)
    op = entry.get("op")
    before = entry.get("before") or {}
    if op == "deploy_policy":
        tree["deployedPolicy"] = before.get("deployedPolicy")
    elif op == "declare_anchor":
        tree["anchor"] = {**empty_tree()["anchor"], **(before.get("anchor") or {})}
    elif op == "round_close":
        # restore the tree that was open when the round was closed, and drop the archive again,
        # so undo really does put the document back rather than only rewinding a counter
        tree["rounds"].pop()
        tree["tree"] = before.get("tree") or tree["tree"]
        tree["currentRound"] = before.get("currentRound", tree["currentRound"])
    elif op == "prune":
        # Deleting collected material is reversible by construction: the node travels back in the
        # journal entry. A prune you cannot undo is just a loss.
        restored = before.get("node")
        if isinstance(restored, dict) and entry.get("nodeId"):
            inner_now = _current(tree)
            inner_now.setdefault("nodes", {})[entry["nodeId"]] = restored
    elif op == "abandon":
        # Undo has to put the FILES back, not just the marks. An abandon that can be undone in
        # the tree but not on disk leaves the next run on that line looking for a folder that
        # is sitting in quarantine - a half-undone state that is worse than either end of it.
        for nid, marks in (before.get("marks") or {}).items():
            node = (_current(tree).get("nodes") or {}).get(nid)
            if not isinstance(node, dict):
                continue
            for key, value in marks.items():
                if value is None:
                    node.pop(key, None)
                else:
                    node[key] = value
        moved = before.get("moved") or {}
        if moved.get("from") and moved.get("to") and os.path.isdir(moved["to"]):
            try:
                os.makedirs(os.path.dirname(moved["from"]), exist_ok=True)
                shutil.move(moved["to"], moved["from"])
            except OSError:
                pass  # the marks are still restored; say so rather than pretending otherwise
        abandoned = [a for a in (tree.get("abandoned") or [])
                     if not (isinstance(a, dict) and a.get("root") == entry.get("nodeId"))]
        tree["abandoned"] = abandoned
    tree["revision"] = int(tree["revision"]) + 1
    save(competition, tree)
    return {"ok": True, "undone": op, "entry": entry, "revision": tree["revision"]}


# ------------------------------------------------------------- plots and review

def analyze(competition: str) -> dict[str, Any]:
    """Draw the figures this tree has earned, and attach them to their nodes.

    Generating a chart is not decoration here. The band plot exists so a delta that sits inside
    the noise is visible as such; the Pareto plot exists so "what should we have tried next" has
    an answer that is not a guess. Both are the analysis, not a picture of it.

    Returns what it produced and, just as importantly, what it could not - a node with no
    samples cannot be given a band, and saying so is more useful than drawing a line through one
    point.
    """
    import plots

    tree = load(competition)
    inner = _current(tree)
    nodes = inner.get("nodes") or {}
    order = sorted(nodes.keys())
    produced: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    changed = False

    def keep(node_id: str, path: str) -> None:
        nonlocal changed
        node = nodes.get(node_id) or {}
        have = list(node.get("plots") or [])
        if path not in have:
            have.append(path)
            nodes[node_id] = {**node, "plots": have}
            changed = True

    # 1. progress: primary metric along the kept chain, in node order
    series = []
    for i, nid in enumerate(order, 1):
        s = _signed_score((nodes[nid] or {}).get("metric"))
        if s is not None:
            series.append((i, s, nid))
    if len(series) >= 2:
        r = plots.render("line", {
            "series": [{"label": "primary metric (signed)",
                        "points": [[i, v] for i, v, _ in series]}],
            "xlabel": "node (record order)", "ylabel": "metric",
            "title": f"metric progress — {competition}"}, f"{competition}-progress", "", None)
        if r.get("ok"):
            produced.append({"kind": "line", "path": r["path"], "what": "metric progress"})
            for i, _, nid in series:
                keep(nid, r["path"])
        else:
            skipped.append({"kind": "line", "reason": r.get("error")})

    # 2. per-node noise: mean +/- std, only where samples were actually recorded
    bands = []
    for nid in order:
        m = (nodes[nid] or {}).get("metric")
        s = m.get("samples") if isinstance(m, dict) else None
        if isinstance(s, dict) and s.get("mean") is not None:
            bands.append({"label": nid, "mean": s.get("mean"), "std": s.get("std"),
                          "n": s.get("n")})
    if bands:
        r = plots.render("band", {"bands": bands,
                                  "title": f"metric with noise band — {competition}"},
                         f"{competition}-noise", "", None)
        if r.get("ok"):
            produced.append({"kind": "band", "path": r["path"],
                             "what": "mean +/- 1 std where samples exist"})
            for b in bands:
                keep(b["label"], r["path"])
        else:
            skipped.append({"kind": "band", "reason": r.get("error")})

    # 3. where the gain actually came from
    board = experience_board(tree)
    if board.get("operatorGain"):
        items = [{"label": k, "value": v} for k, v in board["operatorGain"].items()]
        r = plots.render("bar", {"items": items, "ylabel": "summed delta",
                                 "title": f"gain by operator — {competition}"},
                         f"{competition}-operators", "", None)
        if r.get("ok"):
            produced.append({"kind": "bar", "path": r["path"], "what": "gain by operator"})
        else:
            skipped.append({"kind": "bar", "reason": r.get("error")})

    # 4. the frontier: what was worth its cost
    pts = []
    for nid in order:
        node = nodes[nid] or {}
        s = _signed_score(node.get("metric"))
        if s is None:
            continue
        cost = node.get("cost") or {}
        quota = cost.get("quotaHours") if isinstance(cost, dict) else None
        if quota is None:
            continue
        pts.append({"x": quota, "y": s, "label": nid})
    if len(pts) >= 2:
        r = plots.render("pareto", {"points": pts,
                                     "title": f"quality vs effective cost — {competition}"},
                         f"{competition}-frontier", "", None)
        if r.get("ok"):
            produced.append({"kind": "pareto", "path": r["path"],
                             "what": "non-dominated frontier over effective cost"})
        else:
            skipped.append({"kind": "pareto", "reason": r.get("error")})
    else:
        # say why, rather than silently drawing four figures and leaving the reader to
        # wonder where the fifth went
        skipped.append({
            "kind": "pareto", "reason":
                f"only {len(pts)} node(s) recorded a cost; a frontier needs at least 2. "
                "Add cost:{quotaHours, wallSeconds, agentCalls} to a node to enable it."})

    # 5. the multi-criterion picture, so a stable composite cannot hide one collapsing criterion
    crits: dict[str, dict[str, Any]] = {}
    for nid in order:
        for c in (nodes[nid] or {}).get("criteria") or []:
            if not isinstance(c, dict) or c.get("value") is None:
                continue
            name = str(c.get("name"))
            sd = _num(c.get("std"))
            cur = crits.get(name)
            if cur is None or _num(c["value"], 0.0) > _num(cur.get("value"), float("-inf")):
                crits[name] = {"name": name, "value": _num(c["value"]),
                                "std": sd, "direction": c.get("direction", "higher")}
    if len(crits) >= 2:
        r = plots.render("forest", {"rows": list(crits.values()),
                                    "title": f"criteria, best value per criterion — {competition}"},
                         f"{competition}-criteria", "", None)
        if r.get("ok"):
            produced.append({"kind": "forest", "path": r["path"],
                             "what": "per-criterion best with direction and spread"})
        else:
            skipped.append({"kind": "forest", "reason": r.get("error")})

    if changed:
        tree["revision"] = int(tree["revision"]) + 1
        tree["updatedAt"] = _now()
        save(competition, tree)

    return {
        "ok": bool(produced),
        "produced": produced,
        "skipped": skipped,
        "note": (
            "a node with no recorded samples cannot be given a noise band, and one with no "
            "recorded cost cannot be placed on the frontier. Both gaps are reported rather than "
            "approximated - a chart drawn through a single point is a picture, not evidence."
        ),
        "revisions": tree["revision"],
    }


def review(competition: str) -> dict[str, Any]:
    """A recap that reassembles the reasoning: what is kept, what failed, what it rests on.

    The point is to make the evidence chain readable again at review time. Every claim is shown
    with the sentence that supports it, and anything still unbacked is named, so a review can
    start from the weakest point rather than the newest node.
    """
    import sources as src

    tree = load(competition)
    inner = _current(tree)
    nodes = inner.get("nodes") or {}
    order = sorted(nodes.keys())
    cov = src.coverage(competition)

    kept, refuted, research, unsupported, no_quote = [], [], [], [], []
    for nid in order:
        node = nodes[nid] or {}
        m = node.get("metric") or {}
        entry = {
            "id": nid,
            "change": node.get("change") or node.get("question"),
            "operator": node.get("operator"),
            "family": node.get("family"),
            "delta": m.get("delta") if isinstance(m, dict) else None,
            "reason": node.get("reason"),
            "failureLayer": node.get("failureLayer"),
        }
        refs = node.get("sources") or []
        if refs:
            backed = []
            for r in refs:
                rec = src.get(r.get("sourceId")) or {}
                backed.append({
                    "sourceId": r.get("sourceId"), "relation": r.get("relation"),
                    "title": rec.get("title"), "url": rec.get("url"),
                    "licence": rec.get("licence"),
                    "quote": r.get("quote") or (rec.get("extracts") or [{}])[0].get("quote", ""),
                })
                if not (r.get("quote") or rec.get("extracts")):
                    no_quote.append(nid)
            entry["backedBy"] = backed
        else:
            if node.get("evidence") == "local-only":
                entry["evidence"] = "local-only"
            else:
                unsupported.append(nid)
        if node.get("kind") == "research":
            research.append(entry)
        elif node.get("verdict") == "revert":
            refuted.append(entry)
        elif node.get("verdict") == "keep":
            kept.append(entry)

    failures_by_layer: dict[str, list[str]] = {}
    for e in refuted:
        layer = e.get("failureLayer") or "unclassified"
        failures_by_layer.setdefault(layer, []).append(e["id"])

    return {
        "ok": True,
        "competition": competition,
        "revision": tree["revision"],
        "base": (inner.get("base") or {}).get("id"),
        "kept": kept,
        "refuted": refuted,
        "research": research,
        "failuresByLayer": failures_by_layer,
        "coverage": cov,
        "unsupportedNodes": sorted(set(unsupported)),
        "linksWithoutQuote": sorted(set(no_quote)),
        "nextQuestions": _next_questions(competition, cov, refuted, research, nodes),
        "note": (
            "re-reading a stored source is cheap and the ledger is what makes it possible; a node "
            "whose sources have no stored quote is a claim nobody can check yet."
        ),
    }


def report(competition: str) -> dict[str, Any]:
    """The evidence pack a technical report is written FROM. It does not write the report.

    Everything a reader will be told is assembled here from what is already recorded - the kept
    chain, what was refuted and at which layer, the sources with the sentence that supports each
    claim, the figures the data actually supports, the artifacts a release would carry, and the
    quota it cost. The report is prose; this is the ledger under it, and it is assembled the same
    way every time so two reports of the same run cannot disagree.

    It also returns what the report MAY NOT claim. That section is the point: a technical report
    is the one document whose failure mode is a confident sentence nobody can check, and the
    cheapest defence is to make the unsupported claims enumerable before anyone writes a sentence.
    """
    import sources as src

    tree = load(competition)
    if tree.get("problems"):
        return {"ok": False, "code": "tree_unsound",
                "message": "the tree has structural problems; fix them before writing a report "
                           "about it", "problems": tree.get("problems")}
    inner = _current(tree)
    nodes = inner.get("nodes") or {}
    rev = review(competition)
    ident = identity(competition)

    # --- figures: what the data supports, and what it does not
    figs = analyze(competition)
    produced = figs.get("produced") or []
    figure_paths = [f.get("path") for f in produced if isinstance(f, dict) and f.get("path")]
    could_not_draw = figs.get("skipped") or []

    # --- artifacts and cost, from the kept chain
    release: list[dict[str, Any]] = []
    quota_hours = 0.0
    for e in rev.get("kept") or []:
        node = nodes.get(e["id"]) or {}
        arts = node.get("artifacts")
        if isinstance(arts, str):
            arts = [arts]
        cost = node.get("cost") or {}
        qh = cost.get("quotaHours")
        if isinstance(qh, (int, float)):
            quota_hours += float(qh)
        release.append({
            "id": e["id"], "change": e.get("change"),
            "artifacts": list(arts or []),
            "quotaHours": qh,
            "metric": node.get("metric"),
            "backedBy": e.get("backedBy") or e.get("evidence"),
        })

    # --- the bibliography, deduplicated, with the node each source carries
    biblio: dict[str, dict[str, Any]] = {}
    for e in (rev.get("kept") or []) + (rev.get("refuted") or []) + (rev.get("research") or []):
        for b in e.get("backedBy") or []:
            sid = b.get("sourceId")
            if not sid:
                continue
            row = biblio.setdefault(sid, {
                "sourceId": sid, "title": b.get("title"), "url": b.get("url"),
                "licence": b.get("licence"), "relations": {}, "quote": b.get("quote") or "",
                "nodes": [],
            })
            if b.get("relation"):
                row["relations"][e["id"]] = b.get("relation")
            row["nodes"].append(e["id"])
            if not row.get("quote") and b.get("quote"):
                row["quote"] = b.get("quote")

    # --- runs that were declared and never settled: a report must not claim them
    unsettled = pending_declarations(competition)

    anchor_block = tree.get("anchor") or {}
    may_not_claim: list[dict[str, Any]] = []
    for nid in rev.get("unsupportedNodes") or []:
        may_not_claim.append({"kind": "unbacked-claim", "node": nid,
                              "because": "no source and no evidence='local-only'; add one before "
                                        "it appears in a report"})
    for nid in rev.get("linksWithoutQuote") or []:
        may_not_claim.append({"kind": "no-quote", "node": nid,
                              "because": "the source link carries no sentence that supports the "
                                         "claim, so the claim cannot be checked"})
    for d in unsettled:
        may_not_claim.append({"kind": "unsettled-run", "node": d.get("id"),
                              "because": "declared and run, but no result was recorded; settle it "
                                         "or the report says nothing about it"})
    if not rev.get("kept"):
        may_not_claim.append({"kind": "no-kept-chain", "node": None,
                              "because": "nothing has been kept, so there is no result to report"})
    if not anchor_block.get("declared"):
        may_not_claim.append({"kind": "no-anchor", "node": None,
                              "because": "the held-out evaluation set was never declared, so no "
                                         "claim in this report has been checked against data it "
                                         "was not tuned on"})

    criteria: dict[str, Any] = {}
    for e in rev.get("kept") or []:
        for c in (nodes.get(e["id"]) or {}).get("criteria") or []:
            if isinstance(c, dict) and c.get("name"):
                criteria.setdefault(str(c["name"]), []).append(e["id"])

    return {
        "ok": True,
        "competition": competition,
        "identity": ident,
        "revision": tree["revision"],
        "base": rev.get("base"),
        "anchor": anchor_block,
        "kept": rev.get("kept"),
        "refuted": rev.get("refuted"),
        "research": rev.get("research"),
        "failuresByLayer": rev.get("failuresByLayer"),
        "coverage": rev.get("coverage"),
        "criteria": criteria,
        "figures": figure_paths,
        "couldNotDraw": could_not_draw,
        "artifacts": release,
        "quotaHours": round(quota_hours, 3),
        "bibliography": sorted(biblio.values(), key=lambda r: str(r.get("title") or "")),
        "unsettled": unsettled,
        "mayNotClaim": may_not_claim,
        "nextQuestions": rev.get("nextQuestions"),
        "note": ("This is the ledger, not the report. Every sentence in the report must trace to a "
                 "row here; anything that cannot is in mayNotClaim and stays out of the prose."),
    }


# A number the report pins to a node, with an explicit separator. "n3: 0.61" or "n3 = 0.61".
# Bare adjacency is deliberately not matched: "the 3 runs took 0.4 quota hours" is ordinary prose,
# and a check that fires on ordinary prose is a check people learn to ignore.
_ATTRIBUTED = re.compile(r"(?P<nid>\b[nr]\d+\b)\s*(?::|=|->|→)\s*(?P<val>[+-]?\d+(?:\.\d+)?)")


def _numbers_a_node_holds(node: dict[str, Any]) -> dict[str, float]:
    """Every number one node recorded, keyed by where it came from.

    The provenance is kept because the audit has to be able to say "n3 does not hold 0.61; it
    holds result=0.556 and delta=+0.046", which is a sentence a human can act on, rather than
    "number not found", which is not.
    """
    out: dict[str, float] = {}
    m = node.get("metric") if isinstance(node.get("metric"), dict) else {}
    for field in ("result", "delta", "parent"):
        v = _num(m.get(field))
        if v is not None:
            out[f"metric.{field}"] = v
    samples = m.get("samples") if isinstance(m.get("samples"), dict) else {}
    for field in ("mean", "std", "n"):
        v = _num(samples.get(field))
        if v is not None:
            out[f"metric.samples.{field}"] = v
    cost = node.get("cost") if isinstance(node.get("cost"), dict) else {}
    for field in ("quotaHours", "wallSeconds", "agentCalls"):
        v = _num(cost.get(field))
        if v is not None:
            out[f"cost.{field}"] = v
    for c in node.get("criteria") or []:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "?")
        for field in ("value", "std"):
            v = _num(c.get(field))
            if v is not None:
                out[f"criteria[{name}].{field}"] = v
    return out


def _same_number(a: float, b: float) -> bool:
    """Equal to six decimals, so 0.61 and 0.610 are the same number and 0.611 is not."""
    return round(float(a), 6) == round(float(b), 6)


def audit_report(competition: str, path: str = "", text: str = "",
                 review_report: bool = True) -> dict[str, Any]:
    """Stage three of a claim audit: does the finished prose still match the ledger?

    The deterministic half and the review half are different in kind, and keeping them apart is
    the whole point of this function.

    **It can refuse, and refusing needs no second opinion.** Two things are refusals because
    they are mechanical: a sentence the tree itself put in `mayNotClaim` has appeared verbatim in
    the prose, and an artifact the tree claims is not on disk. Neither needs judgement, and
    routing them through a model would only make a certain answer an uncertain one.

    **It cannot acquit, and a passing number is not an acquittal.** A number that matches the
    tree proves the evidence EXISTS. Whether the sentence it sits in is *supported* by that
    evidence is a judgement, and the only version of that judgement worth having comes from
    something that did not write the report. So an attributed number that matches nothing is
    reported, not cleared: it is the single most likely place for a fabricated figure to sit,
    and it goes to the reviewer as work, not as a footnote.

    The reviewer packet therefore carries FILE PATHS. Not this function's reading of the
    report, not a summary, not a list of findings phrased as conclusions. A reviewer handed a
    summary is reviewing the summary.
    """
    tree = load(competition)
    problems = validate(tree)
    if problems:
        return {
            "ok": False, "code": "tree_invalid",
            "message": ("the tree does not validate, so a report about it cannot be audited into "
                        "safety. Fix the tree first - see the problems below."),
            "problems": problems,
            "tree": tree_path_resolved(competition),
        }

    ledger = report(competition)
    inner = _current(tree)
    nodes = inner.get("nodes") or {}

    if path:
        p = Path(path).expanduser()
        if not p.is_file():
            return {"ok": False, "code": "report_missing",
                    "message": f"no report at {p}. An audit of a file that is not there is a "
                               f"verdict on nothing."}
        body = p.read_text(encoding="utf-8")
        report_path = str(p.resolve())
    elif text:
        body, report_path = str(text), ""
    else:
        return {"ok": False, "code": "nothing_to_audit",
                "message": "pass path=<file> or text=<the report body>."}

    refusals: list[dict[str, Any]] = []

    # A. a forbidden sentence, copied in. The ledger wrote it out precisely so this could be
    # mechanical, and a substring match is exact by construction, so this cannot misfire.
    # The entries are objects, not sentences: the sentence is in `because`.
    lowered = body.lower()
    forbidden = 0
    for claim in ledger.get("mayNotClaim") or []:
        if isinstance(claim, dict):
            text_claim = str(claim.get("because") or "").strip()
            kind = str(claim.get("kind") or "")
        else:
            text_claim, kind = str(claim or "").strip(), "claim"
        if len(text_claim) >= 12 and text_claim.lower() in lowered:
            refusals.append({"kind": "forbidden_claim", "claimKind": kind, "detail": text_claim})
            forbidden += 1

    # B. a phantom artifact. The tree says a run produced this file; the file is not there.
    # `artifacts` is a list of node entries, each carrying its own list of paths.
    phantom = 0
    for entry in ledger.get("artifacts") or []:
        paths = entry.get("artifacts") if isinstance(entry, dict) else [entry]
        for art in paths or []:
            if not isinstance(art, str) or not art.strip():
                continue
            ap = Path(art).expanduser()
            if not ap.exists():
                refusals.append({
                    "kind": "phantom_artifact",
                    "node": entry.get("id") if isinstance(entry, dict) else None,
                    "detail": art,
                })
                phantom += 1

    # C. an attributed number. Checked against the node it names; unmatched is reviewer work.
    matched, unmatched = [], []
    seen: set[tuple[str, str]] = set()
    for m in _ATTRIBUTED.finditer(body):
        nid, raw = m.group("nid"), m.group("val")
        key = (nid, raw)
        if key in seen:
            continue
        seen.add(key)
        held = _numbers_a_node_holds(nodes.get(nid) or {})
        try:
            claimed = float(raw)
        except ValueError:
            continue
        if not held:
            unmatched.append({"node": nid, "claimed": raw,
                              "note": f"{nid} records no numbers at all"})
            continue
        hit = next((where for where, v in held.items() if _same_number(v, claimed)), None)
        if hit:
            matched.append({"node": nid, "claimed": raw, "field": hit})
        else:
            unmatched.append({
                "node": nid, "claimed": raw,
                "note": (f"{nid} holds " + ", ".join(f"{k}={v:g}" for k, v in held.items())
                         + " - none of them is this"),
            })

    tree_path = tree_path_resolved(competition)
    packet = {
        "report": report_path or "(passed inline as text - give a path so the reviewer reads the file)",
        "tree": tree_path,
        "ledgerFields": ["kept", "refuted", "failuresByLayer", "bibliography", "mayNotClaim",
                         "anchor", "quotaHours", "unsettled"],
        "instruction": ("Read both files yourself. Do not accept any summary of them, including "
                        "this one. Then judge whether the evidence supports each claim in the "
                        "report, and whether any sentence overstates what the tree records. The "
                        "deterministic pass below already refused what it could; it did NOT "
                        "clear anything."),
        "alreadyRefused": refusals,
        "attributedNumbersChecked": len(matched) + len(unmatched),
    }
    return {
        "ok": not refusals,
        "code": "clean" if not refusals else "refused",
        "read": {"chars": len(body), "nodes": len(nodes),
                 "mayNotClaim": len(ledger.get("mayNotClaim") or []),
                 "forbiddenClaimCopies": forbidden,
                 "artifacts": len(ledger.get("artifacts") or []),
                 "phantomArtifacts": phantom},
        "refusals": refusals,
        "attributedNumbers": {"matched": matched, "unmatched": unmatched},
        "reviewerPacket": packet,
        "reviewNeeded": bool(review_report and (matched or unmatched)),
        "note": ("A deterministic pass can refuse and can prove a number EXISTS. It cannot say "
                 "the evidence supports the sentence. That half is the reviewer's, and the "
                 "packet hands over paths rather than conclusions."),
    }



def _next_questions(competition: str, cov: dict[str, Any], refuted: list[dict[str, Any]],
                     research: list[dict[str, Any]],
                     nodes: dict[str, Any]) -> list[str]:
    """Where to go next, stated as questions the tree itself raises.

    These are openings, not instructions. The tree knows what has been tried and what it cost;
    it does not know the domain well enough to say which of these is worth spending quota on.
    """
    out: list[str] = []
    if cov.get("unsupported"):
        out.append(
            f"{len(cov['unsupported'])} node(s) have no source and no local-only note "
            f"({', '.join(cov['unsupportedNodeIds'][:6])}): which of these can be justified, and "
            "which were just nudges that happened to help?"
        )
    by_layer: dict[str, int] = {}
    for e in refuted:
        by_layer[e.get("failureLayer") or "unclassified"] = \
            by_layer.get(e.get("failureLayer") or "unclassified", 0) + 1
    for layer, n in sorted(by_layer.items(), key=lambda kv: -kv[1]):
        if layer != "unclassified" and n >= 2:
            out.append(
                f"{n} refutations all landed in the '{layer}' layer: is that a tool-level fault "
                "that would keep biting, or a property of the approach?"
            )
    if research:
        out.append(
            f"{len(research)} research node(s) say the picture was insufficient: have the "
            "sources they named actually been read, and did that change the plan?"
        )
    if not out:
        out.append("the tree has no exposed gap: every conclusion is backed and no layer repeats")
    return out


# --------------------------------------------------------------------------- queries

def refuted(tree: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": nid,
            "kind": n.get("kind"),
            "change": n.get("change") or n.get("question"),
            "reason": n.get("reason"),
            "delta": (n.get("metric") or {}).get("delta") if isinstance(n.get("metric"), dict) else None,
        }
        for nid, n in (_current(tree).get("nodes") or {}).items()
        if isinstance(n, dict) and n.get("verdict") == "revert"
    ]


def is_abandoned(node: Any) -> bool:
    """Whether a node was given up on as part of a line of work.

    Distinct from `verdict == "revert"`, which says the idea was wrong. Abandonment says the
    direction was not worth continuing, and the two must not be treated alike: a refuted node
    is still evidence worth reading, while an abandoned branch should stop costing the search
    anything while remaining on the record of what was paid for.
    """
    return isinstance(node, dict) and bool(str(node.get("abandonedAt") or "").strip())


def kept_chain(tree: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = _current(tree).get("nodes") or {}
    out, cur, seen = [], (_current(tree).get("base") or {}).get("id"), set()
    while cur and cur in nodes and cur not in seen:
        seen.add(cur)
        n = nodes[cur]
        out.append({"id": cur, "change": n.get("change") or n.get("question"),
                    "verdict": n.get("verdict"), "cost": n.get("cost"),
                    "abandoned": is_abandoned(n)})
        cur = n.get("parent")
    return out


def status(competition: str) -> dict[str, Any]:
    tree = load(competition)
    problems = validate(tree)
    inner = _current(tree)
    nodes = inner.get("nodes") or {}
    board = experience_board(tree)
    families = {str(n.get("family") or "") for n in nodes.values()
                if isinstance(n, dict) and n.get("family")}
    anchor = tree.get("anchor") or {}
    return {
        "path": tree_path(competition),
        "exists": os.path.isfile(tree_path(competition)),
        "schemaVersion": 3,
        "revision": tree["revision"],
        "nodes": len(nodes),
        "base": (inner.get("base") or {}).get("id"),
        "kept": len([n for n in nodes.values()
                     if isinstance(n, dict) and n.get("verdict") == "keep"]),
        "refuted": len(refuted(tree)),
        "research": len([n for n in nodes.values()
                         if isinstance(n, dict) and n.get("kind") == "research"]),
        "sound": not problems,
        "problems": problems,
        # v3 surface
        "currentRound": tree.get("currentRound"),
        "rounds": len(tree.get("rounds") or []),
        "policies": sorted((tree.get("policies") or {}).keys()),
        "deployedPolicy": tree.get("deployedPolicy"),
        "anchor": anchor,
        "anchorDeclared": bool(anchor.get("declared")),
        "ruler": tree.get("ruler") or {},
        "rulerCalibrated": (tree.get("ruler") or {}).get("noise") is not None,
        "efc": board.get("efc"),
        "failureLayers": board.get("failureLayers"),
        "treeTooSmallForNovelty": len(families) < MIN_FAMILIES_FOR_NOVELTY,
        "staticChecksOnly": (
            "a clean validation proves this tree is well-formed, not that the runs behind it were "
            "good. Harness effects are large enough to cover model-generation gaps, and an effect "
            "can destroy quality with no failing check at all - so treat a green tree as evidence "
            "about the record, never about the result."
        ),
    }


# ----------------------------------------------------------------- tool-facing renderers
# The three new capabilities each have a shape the tool layer has to render, and each of them
# answers a question that used to be unanswerable rather than adding a field nobody reads.

def set_goal_response(competition: str, metric: str = "", target: Any = None,
                      direction: str = "", note: str = "",
                      read_revision: Optional[int] = None) -> dict[str, Any]:
    res = set_goal(competition, metric, target, direction, note, read_revision)
    if not res.get("ok"):
        return {"content": [{"type": "text", "text": (
            f"kaggle_experiment_tree goal ({res.get('code')})\n{res.get('message')}")}],
            "isError": True}
    g = res.get("goal") or {}
    body = [res.get("message") or ""]
    if g:
        body.append(f"  metric: {g.get('metric')}")
        if g.get("target") is not None:
            body.append(f"  target: {g['target']}"
                        + (f" ({g['direction']} is better)" if g.get("direction") else ""))
        if g.get("note"):
            body.append(f"  note: {g['note']}")
    return {"content": [{"type": "text", "text": "\n".join(x for x in body if x)}],
            "isError": False}


def set_stage_response(competition: str, curriculum: Optional[list] = None, stage: str = "",
                       read_revision: Optional[int] = None) -> dict[str, Any]:
    res = set_stage(competition, curriculum, stage, read_revision)
    if not res.get("ok"):
        return {"content": [{"type": "text", "text": (
            f"kaggle_experiment_tree stage ({res.get('code')})\n{res.get('message')}")}],
            "isError": True}
    lines = [res.get("message") or ""]
    for s in res.get("curriculum") or []:
        here = " <- current" if s["name"] == res.get("stage") else ""
        lines.append(f"  {s['name']}{here}")
        if s.get("passesWhen"):
            lines.append(f"      passes when: {s['passesWhen']}")
    return {"content": [{"type": "text", "text": "\n".join(x for x in lines if x)}],
            "isError": False}


def branch_response(competition: str, name: str = "", node: str = "",
                    read_revision: Optional[int] = None) -> dict[str, Any]:
    res = branch_paths(competition, name, node, read_revision)
    if not res.get("ok"):
        return {"content": [{"type": "text", "text": (
            f"kaggle_experiment_tree branch ({res.get('code')})\n{res.get('message')}")}],
            "isError": True}
    lines = [res.get("message") or ""]
    if res.get("path"):
        lines.append(f"  files:       {res['path']}")
        lines.append(f"  on abandon:  {res.get('quarantine')}")
    return {"content": [{"type": "text", "text": "\n".join(x for x in lines if x)}],
            "isError": False}
