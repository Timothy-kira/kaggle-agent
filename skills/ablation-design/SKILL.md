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

The tree enforces this as **arithmetic, not as wording**. Each run records `factors` — the
components it switched on — and the set must differ from its parent by exactly one factor. That
is the check no sentence can defeat: a node can describe a two-component change in one confident
line ("swap the cache while widening the context window") and no keyword scan will see it, while
the symmetric difference comes out at two.

```json
"factors": ["aug-a", "cache"],
"controls": {"seed": 1, "budget": "1h", "eval": "holdout", "retrain": "re-eval", "data": "train-v3"}
```

`factors=[]` is the **bare model with everything off**, not an absent field — it is the arm every
other arm is measured against. `controls` records what was held fixed; if it disagrees with the
parent's, the delta belongs to more than one cause and the table will refuse to call it a
component's effect.

`"data"` is the one worth remembering, because it is the only control that names the *input* rather
than the fairness of the comparison. Pin what the run read — a Kaggle dataset id, a path, a version
tag. If the same factor is measured before and after a re-upload, the two arms differ by the factor
*and* by the file, and the honest reading is that you do not yet know which one you measured. Leaving
it out is not neutral: two arms that both stay silent about their data are treated as equally
unknown rather than as matching, so the table stays quiet instead of inventing a disagreement it
never observed.

There are two honest ways to need more than one change, and both are declared rather than
smuggled:

| What you are doing | How you say so |
|---|---|
| Adding two components **on purpose**, so the interaction is computable | `factorsIntent: "factorial"` |
| Changing nothing, to measure the seed and the run-to-run spread | `factorsIntent: "repeat"` |
| Moving a control **on purpose** (re-evaluating and retraining answer different questions) | `confoundReason: "..."` |

The default answer is still **two nodes off the same parent**, and that is what the branching is
for. A factorial arm is for when the pair itself is the thing you want to measure — not a way to
get past a gate.

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

## A gate may drive; only something else may acquit

Every check in this plugin — the factor arithmetic, the noise floor, `check_plugin.py`, the
`select` budget — decides whether a **write happens**. None of them decides whether a **result is
true**, and the moment you read one as the other you have stopped checking.

The asymmetry is worth stating because it is cheap and it changes what you build:

- **Refusing needs no second opinion.** "This arm differs from its parent by two factors" is
  arithmetic. So is "the claimed artifact is not on disk" and "that number is not the one the
  node recorded". These are free, deterministic, and should be as aggressive as possible.
- **Supporting always needs one.** `settle` marking a run `confirmed` means the delta cleared a
  floor computed from measured noise. That is evidence the effect is real. It is not evidence
  that the effect is the one your hypothesis was about — the same agent chose the factor, ran
  it, and judged it, and a self-reviewer has blind spots by construction.

So when a result is about to be reported, `technical-report` runs the mechanical pass
(`action="audit-report"`) and then hands a reviewer the **paths**, never a summary. Keep the two
apart in anything else you build on top of this skill: a deterministic refusal you can automate,
and a support judgement you must route to something that did not do the work.

---

## Provenance and attribution

Adapted for a competition experiment tree from two MIT-licensed skills in
[K-Dense scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills)
(pinned commit `1dd0fccf46fc3c9855c4a0c313a0c57fe4319883`):

- `experimental-design` (v1.1) — randomization, replication, blocking, and the
  pseudoreplication failure
- `uncertainty-and-units` (v0.1 / upstream) — uncertainty reporting and plausibility discipline

**Their scripts are deliberately not vendored here.** Every one of them imports `pandas`,
`scipy`, `statsmodels` or `pyDOE3`, none of which this plugin requires or may assume. Copying
methodology while leaving out the dependency-bound scripts keeps the knowledge and drops the
dead code. Where you need those computations, `kaggle_sources action="doctor"` shows what is
absent and what installing it would buy, and nothing is installed without the user's agreement.

The boundary moved for plotting and not here. `numpy` and `matplotlib` **are** required, because
figures are drawn with them, and `scientific-plotting` therefore ships the vendored scripts of a
sibling K-Dense skill — those are network-free and need nothing beyond the backend. The test is
whether a script can run once the backend exists, not whether its author used a library.

## Cross-references

- `rsi-experiment-tree` — the tree these rules shape, the tool that enforces the factor
  arithmetic and provenance requirements, and the skill that carries the full `factors` /
  `controls` / `factorsIntent` reference. **Read its "An ablation is arithmetic" section before
  designing a batch**; this skill is the design-time view (what to run, what to hold fixed, what
  would be confounded), that one is the record-and-judgement view.
- `scientific-plotting` — draws the noise band and the frontier these rules make possible.
- `evidence-sources` — where a literature-grounded node's citations are stored.

Once the arms are recorded, `kaggle_experiment_tree action="ablate"` reads the factor sets back
and reports the comparison set: every edge isolating one factor in whichever direction it was
run, the interactions four distinct arms make computable, the effects measured only in
company's shadow, the arms whose controls disagree, and the noise floor implied by any
configuration you ran twice. Designing the batch is here; reading what it actually established
is there.
