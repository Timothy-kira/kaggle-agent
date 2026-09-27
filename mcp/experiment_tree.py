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
from datetime import datetime, timezone
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
# research node cannot quietly name a source nothing can open.
RESEARCH_TARGETS = ("forum", "code", "web", "paper", "model", "dataset", "rules", "leaderboard")

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


def comp_dir(competition: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", (competition or "").strip().lower()).strip("-._")
    return os.path.join(_home(), "handoff", slug or "unnamed")


def tree_path(competition: str) -> str:
    return os.path.join(comp_dir(competition), "tree.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def empty_tree() -> dict[str, Any]:
    """A v3 document: current tree, archived rounds, policy registry, anchor and journal.

    Everything lives in one file on purpose. Separate documents for the tree and the policy
    history would mean two writers and no way to make "archive the round and deploy the new
    policy" a single atomic step. One document plus the existing tmp+replace write means a
    reader never sees half of either.
    """
    return {
        "schemaVersion": 3,
        "currentRound": 1,
        "deployedPolicy": None,
        "anchor": {"declared": False, "heldOut": None, "rule": "", "declaredAt": None},
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
    """
    data = empty_tree()
    try:
        with open(tree_path(competition), "r", encoding="utf-8") as fh:
            stored = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return data
    if not isinstance(stored, dict):
        return data

    version = stored.get("schemaVersion")
    if not isinstance(version, int) or version < 3:
        data = _migrate_v2(stored)
        data["migrated"] = True
        data["fromVersion"] = 2 if version is None else version
        return data

    for key in ("currentRound", "revision"):
        if isinstance(stored.get(key), int):
            data[key] = stored[key]
    for key in ("deployedPolicy", "updatedAt"):
        if stored.get(key) is not None:
            data[key] = stored[key]
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
    return data


def save(competition: str, data: dict[str, Any]) -> str:
    """Persist the document. The only writer of tree.json in this package.

    Public because handoff.py delegates its tree writes here instead of keeping a second,
    unvalidated writer. tools/check_plugin.py asserts it is the only one.
    """
    path = tree_path(competition)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {k: v for k, v in data.items() if not k.startswith("_")}
    payload["schemaVersion"] = 3
    payload.pop("migrated", None)
    payload.pop("fromVersion", None)
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
                problems.append(f"{where}: targets must be a list")
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
    return tree


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
    """
    if not isinstance(node, dict):
        return {"ok": False, "code": "bad_node", "message": "node must be an object"}
    prepared = dict(node)
    # A declaration is always an experiment. A research node changes nothing and runs nothing,
    # so it is never a precondition for a launch and is not what this action produces.
    prepared.setdefault("kind", "experiment")
    prepared["status"] = "planned"
    prepared.pop("metric", None)
    prepared.pop("verdict", None)
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
    if not isinstance(node, dict):
        return {"ok": False, "code": "bad_node", "message": "node must be an object"}
    prepared = dict(node)
    prepared.setdefault("kind", "experiment")
    prepared.setdefault("parent", declared)
    return record(competition, prepared, read_revision)


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
        r["eligible"] = r["verdict"] != "revert"
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
        if not isinstance(node, dict):
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
    if nid == ROOT_ID:
        return [k for k, v in nodes.items() if isinstance(v, dict) and not v.get("parent")]
    return [k for k, v in nodes.items() if isinstance(v, dict) and v.get("parent") == nid]


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


def kept_chain(tree: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = _current(tree).get("nodes") or {}
    out, cur, seen = [], (_current(tree).get("base") or {}).get("id"), set()
    while cur and cur in nodes and cur not in seen:
        seen.add(cur)
        n = nodes[cur]
        out.append({"id": cur, "change": n.get("change") or n.get("question"),
                    "verdict": n.get("verdict"), "cost": n.get("cost")})
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
