---
name: rsi-experiment-tree
description: Use when iterating on a solution that improves by repeated experiment - tracking which change gained what, which side effects it caused, and building the next iteration on the kept gains while discarding the harms. Maintains a validated DAG of nodes where an experiment node changes one thing and measures it, and a research node goes back to a source when a result made the current understanding insufficient. The node shape is enforced by kaggle_experiment_tree, every node must be read before recording, archived rounds are a replay simulator for offline policy comparison, and a held-out evaluation anchor must be declared before experimenting. Covers deciding keep versus revert, which parent to expand next, which operator produced the gain, when to stop, and what is deliberately left to the agent's judgement.
---

# RSI experiment tree

Repeated self-improvement is a search, not a sequence. Each node changes exactly one thing and
measures it, or — when a result proves the current picture wrong — goes back to a source and says
what that reopened. The tree is the record; without it the next iteration silently re-learns what
was already refuted.

The invariant: **a node's gain belongs to the base only when the node is kept, and a node's side
effect never propagates.** A kept experiment may still cost something — that cost is recorded on
the node and carried forward as a known constraint, not silently dropped.

## The tree is a simulator, not a prompt

Two findings from the research this is built on, both of which change how you use this skill:

**History as prompt guidance makes things worse.** Abstracting past runs into "try X next" and
injecting it into the prompt consistently underperformed leaving the agent unguided. So this skill
never tells you where to go next. It tells you what has already been tried, what it cost, and
what regressed — and leaves the direction to you.

**History as a replay simulator makes things much better.** A completed discovery tree is walked
to score alternative exploration policies without re-running anything: no agent call, no
evaluation, no quota. That is `action="replay"`, and it is the reason closing a round is worth
doing on its own.

## The tree is enforced by a tool, not by prose

`kaggle_experiment_tree` validates every node and refuses to store a malformed one. The parts
that make a node judgeable cannot be quietly skipped:

| Refused | Because |
|---|---|
| no `hypothesis` | an experiment with no stated expectation cannot be judged |
| a `metric` without the parent's number | a gain measured against a different baseline is a false gain |
| a `reason` of "better" / "works" / "n/a" | the reason is what the next iteration reads |
| a `change` containing "and" / "then" / "plus" | that is two experiments; it measured neither |
| an experiment with no `operator` or no `family` | it cannot be scored for novelty or attributed |
| a `metric` with no `rank` / `rankSource` | a score with no standing is only a local delta |
| a `revert` with no `failureLayer` | a negative score with no layer says nothing about what to change |
| a `parent` not in the tree | the DAG would not be connected |
| a base with no `artifacts` | the next comparison has nothing reproducible to compare against |

## Read before every node. The tool makes you.

```
kaggle_experiment_tree action="read" competition="<competition>"
```

This returns the base, the kept chain, the refuted list, the research nodes, and a
`readRevision`. **`record` refuses a missing or stale `readRevision`.**

That is the whole loop, enforced rather than requested: once a node lands, the tree has changed,
so the next node cannot be planned from the version you remember. Planning the second experiment
requires reading the tree again.

## Every step consults the tree. Only worthwhile steps become nodes.

Consulting the tree has to be cheap enough that you do it every time, not only before a run — and
a node has to stay expensive enough that you do not make one of everything. `consider` is both at
once:

```
kaggle_experiment_tree action="consider" competition="<slug>"
  change="<the one thing you are about to do>"
  hypothesis="<why you expect it to matter>"
  operator="draft|improve|debug|crossover"     # if you already know
  family="<method family>"                      # if you already know
```

| Verdict | What it means | What you do |
|---|---|---|
| `in_flight` | a run of this is already declared and has no result | settle it or wait. Do not declare it twice. |
| `already_refuted` | this was tried and reverted, with the layer that broke | **do not run it.** Read the reason; it is the whole point of the tree. |
| `already_known` | this is already in the kept chain | you are re-deriving a result the tree holds |
| `worth_declaring` | nothing covers it, and it names one change, one operator, one family | declare it, then run it |
| `judge_it` | nothing covers it, but it names no operator or family | your call: a node is worth it only if the result would change what you do next |
| `not_worth_a_node` | nothing was stated | nothing to record |

Run it at the start of any step that would spend quota, read a source, or change an approach — and
also at the start of a step you think is trivial. The cheap case is exactly the one worth checking,
because "this is obviously fine" is how a refuted branch gets re-run.

**The tool matches; you judge.** `consider` is mechanical — it compares what you said against the
refuted list, the kept chain and the open declarations, and hands you the evidence. Whether a
finding matters enough to become a node is judgement, and that is deliberately left to you. What
the tool removes is the excuse: after `already_refuted`, running it anyway is no longer an
oversight, it is a decision you made against a record.

## Useless collected material can be pruned. Nothing else can.

A research node is a note that something was read. When it turns out to be worthless, keeping it
makes the refuted list and the board harder to read, and a tree padded with dead notes stops being
a signal. So:

```
kaggle_experiment_tree action="prune" competition="<slug>" node="r7"
  reason="<why this is not worth keeping>"  read_revision=<current>
```

It is scoped hard on purpose:

- **Only `research` nodes.** An experiment is evidence that quota was spent. If it was wrong,
  record a `revert` — do not delete it. Declarations are the same.
- **Nothing points at it.** A research node with children has an open question under it; prune the
  branch, not the root.
- **Not the base**, and not inside an archived round — deleting it there would make a replay score
  unreproducible. Nor the held-out anchor.
- **A real reason**, and a current read, same as every other write.
- **`undo` restores it**, because the deleted node travels back in the journal entry. A prune you
  cannot undo is just a loss.

A vague reason is refused. "This is useless" says nothing; "the API it documents is deprecated" is
a finding the next agent can use.

## Every node records which operator produced it

Four atomic operators, from OpenMLE (arXiv 2607.28568): `draft` · `improve` · `debug` ·
`crossover`. The measured reason this is mandatory: **Improve and Crossover produced 85–92% of
total gain**, while Draft and Debug mostly made a program executable. A tree that cannot see
which operator did what cannot see where its gains came from.

```json
{"id": "n7", "kind": "experiment", "parent": "n6",
 "change": "add a verifier pass before submission",
 "hypothesis": "malformed actions are the dominant error mode",
 "metric": {"name": "score", "parent": 0.72, "result": 0.81, "delta": 0.09,
            "rank": 0.77, "rankSource": "leaderboard percentile 0.77",
            "direction": "higher"},
 "operator": "crossover", "family": "verifier",
 "verdict": "keep", "reason": "+0.09, errors shifted from malformed to genuine dead ends"}
```

`family` is a short slug naming the *kind* of change. Novelty is computed from it, so a new family
is a new direction and a repeat of one is not.

## The evaluation surface is multi-criterion, and EFC is per criterion

A competition has one number; a real judgement rarely does. So `metric` stays as the primary
metric (the tree base and the replay objective both depend on it), and `criteria` adds any number
of named criteria with their own direction, rank, samples and **Effective Feedback Compute**:

```json
"cost": { "quotaHours": 3.5, "wallSeconds": 12600, "agentCalls": 1 },
"criteria": [
  { "name": "correctness", "value": 0.92, "direction": "higher",
    "efc": { "informative": true, "valid": true, "redundant": false, "retained": true } },
  { "name": "stability",   "value": 0.43, "direction": "higher",
    "samples": { "n": 4, "values": [0.51, 0.38, 0.44, 0.39], "mean": 0.43, "std": 0.057 } },
  { "name": "wallcost",    "value": 1.0, "direction": "lower",
    "efc": { "informative": false, "valid": true, "redundant": true, "retained": true } }
]
```

**EFC is judged per criterion, never collapsed into one boolean.** From arXiv 2605.29682: raw
tokens, tool calls, wall time and cost "cannot distinguish useful feedback from redundant or
unstable interaction". A run that is redundant about correctness but informative about cost is a
different finding from the reverse, and averaging them throws it away. A node whose every
criterion is `redundant` cost real quota and taught nothing, so its `effectiveCost` is zero.

- `informative` — this criterion produced new knowledge: a new family, a new best, a corrected
  belief, or the first real cost of a method
- `redundant` — repeated a same-family change on this criterion with nothing new
- `valid` — the run produced a scoreable result at all
- `retained` — the conclusion stayed in the tree rather than being discarded

**A regression is reported, never blocked.** `select` and `board` list `regressedCriteria` next to
the score — a criterion that got worse than the parent by more than its own noise band. Deciding
whether that trade is acceptable is your call, and you record it in `reason`. What you must not
do is let a composite hide it.

There is no discipline field and no criteria-set preset. Criteria are compared by name, so a
name that drifts shows up in `board` as two rows rather than silently merging.

## Declare the evaluation anchor before you experiment

```
kaggle_experiment_tree action="anchor" held_out="<the set you are not allowed to score on>"
```

After that, a node whose `metric.split` or `rankSource` names the held-out set is **rejected**.

The reason is not bureaucracy. Every route through this literature answers the same question —
"after the change, on what grounds do you say it got better?" — and the answer is only credible
if the evaluation set is one the improving system cannot reach. Three independent teams converged
on that as a precondition, and Dream-RSI's replay inherits the problem: scoring a policy on data
the search has already seen only re-ranks what you knew.

Declare it **before** the first run. Splitting afterwards does not make anything disjoint.

**What this cannot do:** give you an anchor that is truly external. This plugin can enforce
disjointness; it cannot supply an evaluation that is independent of you. That limit is real and
belongs in the plan's known-limits, not in a claim of solved.

## Selecting the next node is not "take the highest score"

```
kaggle_experiment_tree action="select" weights={"workers": 2}
```

Greedy score-maximising selection concentrates the search on the incumbent and discards branches
that are merely promising. OpenMLE measured this: replacing scalar-fitness selection with a
quality + progress + novelty utility moved Medal Average from **53.03% to 60.61%** under the same
model and the same 12-hour budget.

```
U_i = λ_s·score_i + λ_Δ·progress_i + λ_n·novelty_i     (× cooling)
```

- `score` — current quality, oriented so larger is better
- `progress` — improvement over the **strongest ancestor**, not the immediate parent, because a
  chain of small local gains can look busy while never beating anything
- `novelty` — whether this node's `family` is one nobody has tried
- cooling — decays as a node is visited, so one incumbent cannot monopolise the budget

The action is a **batch** of at most `workers` nodes, not a single node. A refuted node is never
selected: it stays as evidence so it is never repeated, but it cannot win again.

**Expect the top pick to sometimes not be the top score.** That is the mechanism working. If it
*always* is, the weights are wrong. With fewer than three method families the tool reports
`confidence: "low"`, because novelty is not yet discriminating between anything.

## Dreaming: replaying a policy for free

```
kaggle_experiment_tree action="round_close" read_revision=<from the read>
kaggle_experiment_tree action="replay"   policy={"id": "greedy", "params": {...}}
kaggle_experiment_tree action="compare"  policy=[<candidates...>]
```

Closing a round archives the current tree into `rounds[]` — **that archive is the replay pool**.
`replay` walks it under one policy; `compare` walks it under several and picks the best.

The objective, from Dream-RSI (arXiv 2609.14858):

```
V = max(reached score) − β₁·effectiveCost + β₂·N / max(1, k)
```

where `N` is how many nodes the walk revealed and `k` how many decision rounds it took. Note the
cost term is **effective** cost, not raw quota: repeating a family that has already been measured
is nearly free in the objective, which is the whole point of separating useful from redundant
feedback.

**One deliberate departure from the paper, and it is not cosmetic.** Their tree gives every
non-root node at most one child, so revealing a node reveals exactly one continuation. Ours is a
real DAG: a parent can have several children, so one selected batch may reveal several nodes and
`N` grows faster. Our replay score is therefore not numerically comparable to theirs. `replay`
returns the full `revealed` list so the walk can be checked by hand.

**A history with no archived round cannot be replayed, and the tool says so** rather than
returning a zero. Replay is free because the work is already done; before the first round there
is nothing to read.

**Monotone selection.** `compare` refuses to run unless the deployed policy is in the candidate
set, and the winner is never worse than what is deployed. That is the direct answer to the "safe
inheritance" failure in arXiv 2609.11873: persisting across rounds is not the same as improving,
and the guarantee is that a round can never make the policy worse.

Policies are **data** — weights, betas, workers, maxRounds. Nothing is executed.

## Repeat measurements, and the noise band

BioAgent Bench measured Jaccard 0.43 across four identical runs of the same task. The
bottleneck is not "can it finish", it is "does it finish the same way". So:

```json
"samples": { "n": 3, "values": [6.08, 6.15, 6.12], "mean": 6.117, "std": 0.029 }
```

`result` must equal `samples.mean` when samples are given, or the noise band is being measured
against a different number than the one recorded. With samples present, `select` ranks on the mean
and flags `withinNoise` when `|delta| < 2·std` — which is how "is this delta even real" stops
being a matter of opinion.

## A refuted node must name the layer that broke

```
"verdict": "revert", "failureLayer": "tool-recovery"
```

Layers: `output-contract` · `tool-recovery` · `evidence-grounding` · `artifact-persistence` ·
`state-continuity` · `metric` · `other` (with a note).

The measured reason: over 60% of failed harness runs failed on output-contract violations and
tool/recovery, **not** on the model being insufficient. Those are harness bugs. A refuted node
that names its layer tells the next iteration whether to change the tool or the skill; one that
does not only says "worse".

## When the old direction is dead: change direction, do not keep digging

A tree that has been refuted at the base is not a tree to keep improving. Three mechanisms let you
move, and they are different things — picking the wrong one is how a dead direction survives two
more weeks.

| Situation | Do | What it does |
|---|---|---|
| This change was wrong, the direction is fine | `verdict: "revert"` with a `failureLayer` | stays in the same lineage; the refuted list stops it being retried |
| This change is a different **approach** to the same goal | a new experiment whose `parent` is the current base | a new branch, still comparable |
| **The goal itself was wrong** | a node with `parent: null` | a new lineage, not comparable to the old base |
| The new direction has earned it | `record(new_base="<node id>")` | promotes it, and the kept chain and replay objective move with it |

`parent: null` is legal and means exactly what it looks like: this node answers to nothing before
it. The tree validates it, and `base.parent` may be `null` too, so a base can itself be the start
of a lineage.

**A `parent: null` node is the honest move when the old base was refuted on a layer like
`metric` or `state-continuity`** — that is the runtime reporting the number was wrong, not the idea.
And `metric.parent` may be `null` as well, with `delta` recorded as the first reading rather than a
change, because there is nothing before it to be a delta from.

Two things not to do: do not re-derive the old direction's numbers under a new node id, and do not
edit history to make the pivot look continuous. The old lineage stays in the tree, refuted, and
`plan` keeps showing it — that is the record of why you moved, and it is what stops the next agent
from walking back into it.

**Declare before you run.** A run is only allowed against a declared experiment:

```
kaggle_experiment_tree action="declare" competition="<slug>" read_revision=<rev>
  node = {"id":"e2","change":"...","hypothesis":"...","parent":null,
          "operator":"crossover","family":"...","reason":"..."}
kaggle_kernel_launch ... declares="e2"
kaggle_experiment_tree action="settle"  competition="<slug>" declared="e2" read_revision=<rev>
  node = {"id":"e2-result", ... ,"metric":{...},"verdict":"...","evidence":"..."}
```

The result lands as a **new** node parented by the declaration, because the tree is append-only and
ids are never reused. A declaration is settled the moment it has a child, so `plan`'s **IN FLIGHT**
list is exactly the set of runs whose result was never recorded.

## The experience board

```
kaggle_experiment_tree action="board"
```

Reports, derived from the tree rather than from your retelling: every method family with its
best node and its kept/refuted counts, which families failed, **gain by operator**, the raw vs
effective compute split, the per-criterion EFC breakdown, and the refutations by layer.

The self-check is the operator line. OpenMLE found Improve and Crossover carrying 85–92% of
gain. If in your tree Draft and Debug carry most of it, that is a finding, not a success: you
have been re-establishing a working baseline rather than improving anything.

## When to stop

Stop and consolidate when:

- two or three consecutive experiments produce deltas inside the noise band, so the base is at a
  local plateau for this metric, or
- the recorded cost has become the binding constraint, or
- the remaining ideas need a different metric rather than another tweak, or
- the last few nodes are all `research`, which means you are re-reading sources instead of
  measuring — the picture is under-researched and running something is cheaper than reading more.

A plateau is a result. Record it so the next session does not re-run the same mutations.

## What is deliberately left to you

This skill constrains the **shape of a record** and the **order records happen in**. It does not
decide, and you should not expect it to:

- what to try next, and in which direction
- when to branch into parallel hypotheses
- when a reported regression is worth accepting
- when to stop, and which criterion matters most for this competition
- which source is worth re-reading when you record a research node

A policy hard-coded into the tool would produce a tree full of correctly-shaped nodes that
explore nothing. The shape is a floor, never a ceiling.

## Also worth knowing

- `action="undo"` steps back the last state change. Registering a policy is not undoable —
  nothing is deployed — but deploying one, declaring an anchor and closing a round are, because
  each of those changes what the next run will do.
- `action="status"` says whether the tree is sound, and carries a standing warning: a clean
  validation proves the record is well-formed, **not** that the runs behind it were good.
  Harness effects are large enough to cover model-generation gaps, and one can destroy
  behaviour with no failing check at all.
- A round archived before these node rules existed is recorded as non-compliant, and `replay`
  says so — that pool is a biased sample and a policy tuned on it is tuned on older, less
  complete runs.

## Cross-references

- `experiment-launch` is how a node's run starts; the tree records what it measured.
- `log-monitor` auto-advances to the next node when the user is away.
- `handoff` derives its document from this tree, so a bad tree becomes a bad handoff.
- `kaggle-competition-research` is what a research node goes back to — and it is where the
  held-out anchor gets declared.
- `presence-mode` decides whether a node is auto-recorded unattended.
- `../relationships.json` holds the `needs`, `enforces` and `loops` edges for this skill, and
  `tools/check_plugin.py` keeps them honest.
