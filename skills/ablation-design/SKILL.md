---
name: ablation-design
description: Use when choosing which experiments to run next, or when a tree is growing fast and the results have become hard to interpret - covers how to structure a batch of ablations so their effects are separable, why repeated runs of one config are not independent evidence, how to block nuisance variation, and how to keep an exploration from collapsing onto one promising branch. Adapted for a competition experiment tree from the K-Dense scientific-agent-skills experimental-design and uncertainty-and-units bodies, which are MIT licensed and whose scripts are deliberately not vendored here.
---

# Ablation design: making the tree say something

## The problem this solves

A tree that grows by "try a tweak, keep it if the number went up" produces a number without a
reason. Two things go wrong, and both are structural rather than bad luck:

- **Confounded nodes.** Change three things, the metric moves, and now nobody can say which one
  did it. The next iteration inherits a mystery.
- **Pseudoreplication.** Run the same configuration four times, treat the four numbers as four
  pieces of evidence, and conclude the effect is real. It may not be — those runs share every
  source of variation except the one you were trying to measure.

An analysis cannot rescue a confounded or pseudoreplicated design afterwards. Both are decided when
the node is created.

## The three rules, and what each maps to in the tree

### 1. One node, one variable

The tree already refuses a `change` containing "and", "then" or "plus" — a node that changed two
things measured neither. When you feel the need for two, the answer is **two nodes off the same
parent**, not one node with both changes. That is what the branching is for.

### 2. Replication must be independent

This is the one that bites quietly. Repeating a run is replication; repeating it **on the same
seed, the same data order, the same machine state** is a re-measurement of one number. Record it
honestly:

```json
"samples": { "n": 3, "values": [6.08, 6.15, 6.12], "mean": 6.117, "std": 0.029 }
```

If you vary nothing between repeats, you have not got `n=3` — you have got one number written
three times, and the std you compute will be near zero and will make a noise band that says
nothing. **Vary the seed, or do not claim a band.**

### 3. Block what you cannot control

Quota state, machine load, data order and a warm cache all move the metric without being what you
changed. You cannot block all of them, so do two things instead: spread the compared
configurations across the same window rather than running A fully then B fully, and record
`cost.quotaHours` and `wallSeconds` so the board can show whether a gain came with a cost change
nobody was looking at.

## Keeping the search from collapsing

A tree that only ever expands the current best is hill climbing, and it stops early. The tree's
`select` already ranks by quality **+ progress over the strongest ancestor + novelty of the method
family**, with visit cooling, so one branch cannot monopolise the budget. Two things you own:

- **A new `family` is a new direction.** Naming two tweaks of the same idea with the same family
  slug tells the selector they are the same direction, and it will treat the second as redundant.
  If it genuinely is a different idea, name it differently.
- **A `research` node is a legitimate move.** When a result shows the approach is wrong, going
  back to a source is not a failure of discipline. It is recorded as one, with what it opened.

## The cost side

Record `cost: {quotaHours, wallSeconds, agentCalls}` on every node you would want to compare
later. Two things depend on it and both are unavailable without it:

- `effectiveCost`, which drives the replay objective — a repeat that taught nothing costs nothing
  in the objective, which is the entire point of separating useful from redundant feedback;
- the Pareto figure, which is how "what should we have tried next" becomes a reading rather than
  an opinion.

A frontier needs at least two costed nodes. Below that, `analyze` says so instead of drawing a
one-point chart.

## What this skill does not decide

Which experiment is worth running is your judgement, informed by what the tree shows. This skill
constrains the *structure* of a node and the honesty of its bookkeeping. Hard-coding a policy here
would produce a tree full of well-formed nodes that explore nothing.

---

## Provenance and attribution

Adapted for a competition experiment tree from two MIT-licensed skills in
[K-Dense scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills)
(pinned commit `1dd0fccf46fc3c9855c4a0c313a0c57fe4319883`):

- `experimental-design` (v1.1) — randomization, replication, blocking, and the
  pseudoreplication failure
- `uncertainty-and-units` (v0.1 / upstream) — uncertainty reporting and plausibility discipline

**Their scripts are deliberately not vendored here.** Every one of them imports `numpy`, `pandas`,
`scipy`, `statsmodels` or `pyDOE3`, none of which this plugin requires or may assume — and on the
machine this was built for, none of them is installed. Copying methodology while leaving out the
dependency-bound scripts keeps the knowledge and drops the dead code. Where you need those
computations, `kaggle_sources action="doctor"` shows what is absent and what installing it would
buy, and nothing is installed without the user's agreement.

## Cross-references

- `rsi-experiment-tree` — the tree these rules shape, and the tool that enforces the one-variable
  and provenance requirements.
- `scientific-plotting` — draws the noise band and the frontier these rules make possible.
- `evidence-sources` — where a literature-grounded node's citations are stored.
