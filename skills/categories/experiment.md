# experiment — 实验与运行

**What this category answers:** how do I run it, watch it without drowning in it, and learn
from what happened.

This is where quota is actually spent, so the rules here are the strictest in the plugin.

| Skill | Use it when |
|---|---|
| [`experiment-launch`](../experiment-launch/SKILL.md) | About to start a real run: which engine, which accelerator, how long, whose quota. Includes the verify-after-launch step that is not optional. |
| [`log-monitor`](../log-monitor/SKILL.md) | A run is producing logs and you want a subagent to watch it instead of the main agent. Also defines the only three conditions that justify interrupting the main agent. |
| [`log-monitor-visualizer`](../log-monitor-visualizer/SKILL.md) | The interval slider. **Load and emit it — do not describe it in prose.** |
| [`rsi-experiment-tree`](../rsi-experiment-tree/SKILL.md) | Repeated iteration: which change gained what, which side effects it caused, what to branch next, when to stop. The node shape is **enforced** by `kaggle_experiment_tree`, and two node kinds exist: an experiment, or a `research` node that goes back to a source when a result invalidated the picture. |

## The tree is a simulator, not a prompt

`kaggle_experiment_tree` validates every node and refuses to store a malformed one, so the parts
that make a node judgeable cannot be quietly skipped: no `hypothesis`, a `metric` that does not
carry the parent's number, a reason of "better", a `change` containing "and", a dangling parent,
a base with no reproducible `artifacts`, **no `operator`/`family`**, **no recorded `rank`**, and a
`revert` with **no `failureLayer`**.

**The loop is read-gated.** `action="read"` returns a `readRevision` and `action="record"` refuses
a missing or stale one, so after every completed run the tree must be read again before the next
node is planned. That is the loop enforced rather than requested — a branch planned off a stale
base is exactly what the tree exists to prevent.

**Archived rounds are a replay simulator.** `action="round_close"` archives the current tree into
`rounds[]`; `action="replay"` then walks that history under an exploration policy and scores it
**without re-running anything** — no agent call, no evaluation, no quota. `action="compare"` scores
several policies and picks the best, and refuses unless the deployed policy is among the
candidates, so a round can never make the policy worse. This is Dream-RSI (arXiv 2609.14858).

**Selection is non-greedy.** `action="select"` ranks parents by quality + progress + novelty with
visit cooling, not by score alone — from OpenMLE (arXiv 2607.28568 sec. 5.2), whose matched
comparison moved Medal Average 53.03% → 60.61% over score-greedy selection with the same model
and the same budget. The action is a **batch** of at most `workers` nodes. The top pick is *not*
always the top score, and that is the mechanism rather than a bug.

**Every experiment node says which operator produced it** — `draft` / `improve` / `debug` /
`crossover` — plus a `family` slug, and its metric records a `rank`. The same paper found Improve
and Crossover produced 85–92% of total gain while Draft and Debug mostly just made a program
executable. `action="board"` attributes the gain per operator, so that finding is checkable on
your own tree rather than assumed.

**Cost is measured as effective feedback, not raw quota.** `criteria[].efc` is judged per
criterion, so a run that is redundant about correctness but informative about cost stays two
findings instead of one averaged one. A node whose every criterion is redundant has
`effectiveCost` 0. A **regression is reported, never blocked** — `regressedCriteria` appears next
to the score and the judgement is yours, to be written into `reason`.

**A held-out anchor must be declared before experimenting.** The research skill declares it; from
then on a node scored on that set is rejected. Disjointness is a precondition for "it got better"
meaning anything, and splitting after the fact does not make anything disjoint.

**A `research` node** is the second node kind: it names the source it went back to (`forum`,
`code`, `web`, `paper`, `model`, `dataset`, `rules`, `leaderboard`) and what that `opens` for a
later experiment. Re-reading a source because a result told you to is the tree recording why,
instead of a re-investigation being fudged into an ablation.

**What is left to the agent:** what to try next, when to branch, when to stop, and whether a
reported regression is acceptable. The shape is a floor, never a ceiling.

## Two rules that are not negotiable

**Verify the accelerator after every launch.** `kernels push --accelerator` is not honoured by
the platform, and even writing `enable_gpu` into `kernel-metadata.json` has been observed not
to stick. A run that came back on CPU burns no quota but also does no work, and it sits there
looking alive. `kaggle_kernel_verify` reads the live kernel record and is the only reliable
check.

**Back up before you retire, then confirm the delete.** A kernel that errors keeps existing,
and an existing kernel keeps spending quota. `kaggle_kernel_retire` records status, backs up
source and output, deletes, and then re-queries to confirm the kernel is really gone. The
backup is written before the delete, never after.

## The live interval, and why it is not a restart

The fetch interval lives in a file the monitoring subagent re-reads at the top of *every*
cycle. That is the whole mechanism behind "I moved the slider and it took effect": nothing is
cached, so an edit lands on the next poll. A subagent that captures the interval once at
launch freezes the cadence for the rest of the run, which is exactly the failure the GUI is
supposed to prevent.

## Report only when it matters

A monitoring subagent that stays silent for an hour has behaved correctly. It reports on three
things only: an error in the log, a terminal state, or a decision the user must make. Progress
that is merely progress is not news, and "the log is quiet" is the most common false alarm in
log monitoring — silence is the healthy state.

## Cross-references

- `identity` decides whose quota pays; this category spends it.
- `research` produces the plan this category executes.
- `handoff` receives the tree this category maintains.

## Bound edges (rendered from `../relationships.json`)

These rows are **rendered from `skills/relationships.json`**, the single source of truth for
how the skills in this package relate, and for the live state those relationships are gated
on. `tools/check_plugin.py` fails the build if this table drifts from that file, so the graph
cannot rot back into loose prose. To change a relationship, edit `relationships.json` and
re-run the checker.

Edge types: `needs` = prerequisite, `dispatches` = launched at runtime, `produces` = this
skill's output is the other's input, `browses` = must actually open this source, `asks` =
presence-mode decides whether to ask here, `enforces` = a tool validates this, `loops` = a
read-then-decide cycle gated by the tool, `widget`/`gates` = the GenUI binding.

Where two mechanisms legitimately touch the same skill, the split of labour is declared under
`divisionOfLabour` in the same file, and the checker refuses a duplicated claim that has no
such declaration.

| From -> type -> To | When this edge is live |
|---|---|
| `experiment-launch` -> needs `kaggle-cli` | quota, accelerator market state and the push itself | <!-- edge:experiment-launch->kaggle-cli:needs --> |
| `experiment-launch` -> needs `kaggle-account-switch` | whose quota pays for this run is decided before anything is pushed | <!-- edge:experiment-launch->kaggle-account-switch:needs --> |
| `experiment-launch` -> produces `log-monitor` | a run that produces logs is a run that needs watching | <!-- edge:experiment-launch->log-monitor:produces --> |
| `experiment-launch` -> produces `rsi-experiment-tree` | the first result is the first node of the tree | <!-- edge:experiment-launch->rsi-experiment-tree:produces --> |
| `log-monitor` -> needs `experiment-launch` | there has to be a live ref to poll and a terminal state to watch for | <!-- edge:log-monitor->experiment-launch:needs --> |
| `log-monitor` -> dispatches `log-monitor-visualizer` | the fetch interval is a pending decision, so it is rendered, never guessed | <!-- edge:log-monitor->log-monitor-visualizer:dispatches --> |
| `log-monitor-visualizer` -> needs `log-monitor` | it sets a value the subagent re-reads every cycle; it does not own the polling | <!-- edge:log-monitor-visualizer->log-monitor:needs --> |
| `log-monitor` -> produces `rsi-experiment-tree` | an error or a terminal state is the observation a node records | <!-- edge:log-monitor->rsi-experiment-tree:produces --> |
| `rsi-experiment-tree` -> needs `experiment-launch` | a node without a run behind it is a claim, not an experiment | <!-- edge:rsi-experiment-tree->experiment-launch:needs --> |
| `rsi-experiment-tree` -> produces `handoff` | the base, its cost and the refuted set are read from the tree, never from recollection | <!-- edge:rsi-experiment-tree->handoff:produces --> |
| `rsi-experiment-tree` -> needs `experiment-tree` | the skill defines what a node must mean; the tool is what makes that definition binding | <!-- edge:rsi-experiment-tree->experiment-tree:needs --> |
| `experiment-tree` => enforces `rsi-experiment-tree` | the node shape is validated in code, so a missing hypothesis, an empty reason, a two-change 'and', a dangling parent or an unreproducible base is refused rather than written | <!-- edge:experiment-tree->rsi-experiment-tree:enforces --> |
| `experiment-tree` => loops `rsi-experiment-tree` | record refuses a missing or stale readRevision, so the tree is read before every node: two records cannot happen back to back without an intervening read | <!-- edge:experiment-tree->rsi-experiment-tree:loops --> |
| `rsi-experiment-tree` -> needs `research-sources` | a research node names the source it went back to, because a result made the current picture insufficient; the tool refuses a target outside this group | <!-- edge:rsi-experiment-tree->research-sources:needs --> |
| `rsi-experiment-tree` -> needs `openmle-uvi` | the four atomic operators, the three-factor parent selection and the Human Rank convention all come from here; it is why an operator and a rank are mandatory on every experiment node | <!-- edge:rsi-experiment-tree->openmle-uvi:needs --> |
| `rsi-experiment-tree` -> needs `rsi-autonomy-levels` | the L1-L5 ladder explains which decisions the agent is allowed to take itself, and its three failure modes explain why refuted nodes are kept as evidence rather than deleted | <!-- edge:rsi-experiment-tree->rsi-autonomy-levels:needs --> |
| `rsi-experiment-tree` -> needs `dream-rsi` | the replay simulator is the reason the tree is archived into rounds: a completed history is walked to score alternative exploration policies without re-running anything | <!-- edge:rsi-experiment-tree->dream-rsi:needs --> |
| `rsi-experiment-tree` -> needs `efc-2605-29682` | raw quota and wall time cannot tell useful feedback from redundant work, so the cost term is computed on effective feedback and judged per criterion | <!-- edge:rsi-experiment-tree->efc-2605-29682:needs --> |
| `rsi-experiment-tree` -> needs `modular-rsi-coupling` | two mechanisms owning one decision point is the documented failure that cost ModularRSI eight points; divisionOfLabour is the guard | <!-- edge:rsi-experiment-tree->modular-rsi-coupling:needs --> |
| `rsi-experiment-tree` -> dispatches `kaggle-competition-research` | a research node re-runs the same research sweep on purpose; re-reading the forum because a result told you to is the tree recording why | <!-- edge:rsi-experiment-tree->kaggle-competition-research:dispatches --> |
| `experiment-launch` -> needs `experiment-tree` | a completed run is a node, and the node cannot be written without first reading the tree | <!-- edge:experiment-launch->experiment-tree:needs --> |
| `log-monitor` -> needs `experiment-tree` | an unattended auto-advance records a node, and the read-gate is what keeps an overnight loop off a stale base | <!-- edge:log-monitor->experiment-tree:needs --> |
| `handoff` -> needs `rsi-experiment-tree` | the document is derived from the tree so it cannot claim a base the tree lacks | <!-- edge:handoff->rsi-experiment-tree:needs --> |
| `genui-scenarios` -> gates `log-monitor` | decides whether the interval is a real pending decision | <!-- edge:genui-scenarios->log-monitor:gate --> |
| `genui-scenarios` -> gates `experiment-launch` | engine and accelerator are choices; the local slider has no widget yet, so that one answers in text | <!-- edge:genui-scenarios->experiment-launch:gate --> |
| `presence-mode` -> asks `experiment-launch` | engine and time limit are auto-decided when away only within a stated, conservative cap; never past remaining quota | <!-- edge:presence-mode->experiment-launch:asks --> |
| `presence-mode` -> asks `log-monitor` | a terminal state auto-advances to the next tree node when away, and reports when present | <!-- edge:presence-mode->log-monitor:asks --> |

## Bound edges (rendered from `../../relationships.json`)

These rows are **rendered from `skills/relationships.json`**, the single source of truth for
how the skills in this package relate, and for the live state those relationships are gated
on. `tools/check_plugin.py` fails the build if this table drifts from that file, so the graph
cannot rot back into loose prose. To change a relationship, edit `relationships.json` and
re-run the checker.

Edge types: `needs` = prerequisite, `dispatches` = launched at runtime, `produces` = this
skill's output is the other's input, `browses` = must actually open this source, `asks` =
presence-mode decides whether to ask here, `enforces` = a tool validates this, `loops` = a
read-then-decide cycle gated by the tool, `widget`/`gates` = the GenUI binding.

Where two mechanisms legitimately touch the same skill, the split of labour is declared under
`divisionOfLabour` in the same file, and the checker refuses a duplicated claim that has no
such declaration.

| From -> type -> To | When this edge is live |
|---|---|
| `experiment-launch` -> needs `kaggle-cli` | quota, accelerator market state and the push itself | <!-- edge:experiment-launch->kaggle-cli:needs --> |
| `experiment-launch` -> needs `kaggle-account-switch` | whose quota pays for this run is decided before anything is pushed | <!-- edge:experiment-launch->kaggle-account-switch:needs --> |
| `experiment-launch` -> produces `log-monitor` | a run that produces logs is a run that needs watching | <!-- edge:experiment-launch->log-monitor:produces --> |
| `experiment-launch` -> produces `rsi-experiment-tree` | the first result is the first node of the tree | <!-- edge:experiment-launch->rsi-experiment-tree:produces --> |
| `log-monitor` -> needs `experiment-launch` | there has to be a live ref to poll and a terminal state to watch for | <!-- edge:log-monitor->experiment-launch:needs --> |
| `log-monitor` -> dispatches `log-monitor-visualizer` | the fetch interval is a pending decision, so it is rendered, never guessed | <!-- edge:log-monitor->log-monitor-visualizer:dispatches --> |
| `log-monitor-visualizer` -> needs `log-monitor` | it sets a value the subagent re-reads every cycle; it does not own the polling | <!-- edge:log-monitor-visualizer->log-monitor:needs --> |
| `log-monitor` -> produces `rsi-experiment-tree` | an error or a terminal state is the observation a node records | <!-- edge:log-monitor->rsi-experiment-tree:produces --> |
| `rsi-experiment-tree` -> needs `experiment-launch` | a node without a run behind it is a claim, not an experiment | <!-- edge:rsi-experiment-tree->experiment-launch:needs --> |
| `rsi-experiment-tree` -> produces `handoff` | the base, its cost and the refuted set are read from the tree, never from recollection | <!-- edge:rsi-experiment-tree->handoff:produces --> |
| `experiment-tree` => enforces `rsi-experiment-tree` | the node shape is validated in code, so a missing hypothesis, an empty reason, a two-change 'and', a dangling parent or an unreproducible base is refused rather than written | <!-- edge:experiment-tree->rsi-experiment-tree:enforces --> |
| `experiment-tree` => loops `rsi-experiment-tree` | record refuses a missing or stale readRevision, so the tree is read before every node: two records cannot happen back to back without an intervening read | <!-- edge:experiment-tree->rsi-experiment-tree:loops --> |
| `rsi-experiment-tree` -> needs `research-sources` | a research node names the source it went back to, because a result made the current picture insufficient; the tool refuses a target outside this group | <!-- edge:rsi-experiment-tree->research-sources:needs --> |
| `rsi-experiment-tree` -> needs `openmle-uvi` | the four atomic operators, the three-factor parent selection and the Human Rank convention all come from here; it is why an operator and a rank are mandatory on every experiment node | <!-- edge:rsi-experiment-tree->openmle-uvi:needs --> |
| `rsi-experiment-tree` -> needs `rsi-autonomy-levels` | the L1-L5 ladder explains which decisions the agent is allowed to take itself, and its three failure modes explain why refuted nodes are kept as evidence rather than deleted | <!-- edge:rsi-experiment-tree->rsi-autonomy-levels:needs --> |
| `rsi-experiment-tree` -> needs `dream-rsi` | the replay simulator is the reason the tree is archived into rounds: a completed history is walked to score alternative exploration policies without re-running anything | <!-- edge:rsi-experiment-tree->dream-rsi:needs --> |
| `rsi-experiment-tree` -> needs `efc-2605-29682` | raw quota and wall time cannot tell useful feedback from redundant work, so the cost term is computed on effective feedback and judged per criterion | <!-- edge:rsi-experiment-tree->efc-2605-29682:needs --> |
| `rsi-experiment-tree` -> needs `modular-rsi-coupling` | two mechanisms owning one decision point is the documented failure that cost ModularRSI eight points; divisionOfLabour is the guard | <!-- edge:rsi-experiment-tree->modular-rsi-coupling:needs --> |
| `rsi-experiment-tree` -> needs `experiment-tree` | the skill defines what a node must mean; the tool is what makes that definition binding | <!-- edge:rsi-experiment-tree->experiment-tree:needs --> |
| `rsi-experiment-tree` -> dispatches `kaggle-competition-research` | a research node re-runs the same research sweep on purpose; re-reading the forum because a result told you to is the tree recording why | <!-- edge:rsi-experiment-tree->kaggle-competition-research:dispatches --> |
| `experiment-launch` -> needs `experiment-tree` | a completed run is a node, and the node cannot be written without first reading the tree | <!-- edge:experiment-launch->experiment-tree:needs --> |
| `log-monitor` -> needs `experiment-tree` | an unattended auto-advance records a node, and the read-gate is what keeps an overnight loop off a stale base | <!-- edge:log-monitor->experiment-tree:needs --> |
| `handoff` -> needs `rsi-experiment-tree` | the document is derived from the tree so it cannot claim a base the tree lacks | <!-- edge:handoff->rsi-experiment-tree:needs --> |
| `genui-scenarios` -> gates `log-monitor` | decides whether the interval is a real pending decision | <!-- edge:genui-scenarios->log-monitor:gate --> |
| `genui-scenarios` -> gates `experiment-launch` | engine and accelerator are choices; the local slider has no widget yet, so that one answers in text | <!-- edge:genui-scenarios->experiment-launch:gate --> |
| `presence-mode` -> asks `experiment-launch` | engine and time limit are auto-decided when away only within a stated, conservative cap; never past remaining quota | <!-- edge:presence-mode->experiment-launch:asks --> |
| `presence-mode` -> asks `log-monitor` | a terminal state auto-advances to the next tree node when away, and reports when present | <!-- edge:presence-mode->log-monitor:asks --> |
| `rsi-experiment-tree` -> needs `evidence-sources` | the tree refuses a concluded node with no provenance, and this is how provenance is supplied - neither half works alone | <!-- edge:rsi-experiment-tree->evidence-sources:needs --> |
| `evidence-sources` -> needs `rsi-experiment-tree` | a source is stored so a node can cite it; a store nobody cites is a reading list, not evidence | <!-- edge:evidence-sources->rsi-experiment-tree:needs --> |
| `rsi-experiment-tree` -> needs `scientific-plotting` | a score, a spread, a per-operator gain and a cost frontier are numbers a table cannot argue with; the figures are the analysis | <!-- edge:rsi-experiment-tree->scientific-plotting:needs --> |
| `scientific-plotting` -> needs `rsi-experiment-tree` | every figure is drawn from the tree, and action='analyze' attaches each one back to the nodes it came from | <!-- edge:scientific-plotting->rsi-experiment-tree:needs --> |
| `rsi-experiment-tree` -> needs `ablation-design` | one-variable nodes, honest replication and recorded cost are what make a kept node interpretable rather than merely true | <!-- edge:rsi-experiment-tree->ablation-design:needs --> |
| `ablation-design` -> needs `rsi-experiment-tree` | it adapts design methodology to this tree's node shape; without the tree there is nothing for it to shape | <!-- edge:ablation-design->rsi-experiment-tree:needs --> |
| `sources-store` => enforces `experiment-tree` | a node citing a source id that is not in the store is refused, so provenance can never dangle | <!-- edge:sources-store->experiment-tree:enforces --> |
| `plot-engine` => enforces `rsi-experiment-tree` | action='analyze' turns the recorded samples and costs into figures, and reports every figure it could not draw rather than omitting it silently | <!-- edge:plot-engine->rsi-experiment-tree:enforces --> |
| `ablation-design` -> needs `k-dense-methods` | it is an adaptation; the upstream bodies and their licence are recorded here so the provenance is not lost | <!-- edge:ablation-design->k-dense-methods:needs --> |
