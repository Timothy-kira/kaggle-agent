---
name: approach-decision
description: Use when facing the choice between writing a solution from scratch and adapting the strongest public notebook, repository or paper for a competition or task. Covers when adapting wins, when it loses, how to adapt safely, and how to keep the decision auditable in the experiment tree.
---

# Write it yourself, or start from the strongest public one

This is a recurring fork in a competition run, and it is worth deciding explicitly rather than
drifting. The two paths have different costs and different failure modes, and the evidence from
ARC-AGI-3 is instructive: the leading teams are *not* all writing from scratch, and the ones who
are winning publicly are the ones whose harness everyone else forked.

## The short version

Start from the strongest public solution when one exists **and you can read what it does**. Write
your own when the public material is a black box, when the task's rules are still unclear, or
when the public solution is so converged that a small change is indistinguishable from a copy.

Convergence is the real risk. When ten public notebooks are near-copies of one another, forking
one puts you in a cluster where only luck separates you. Diversity is a score.

## When adapting wins

- A proven solution exists and is **open and readable**. You can see the mechanism, not just the
  output.
- Your goal is a working baseline this week, not a winning method.
- The public solution scores well on the metric you are scored on, verified on the same split.
- You can name the specific change you will make. "Add my agent" is not a change.

## When writing wins

- The top public solutions are **not open**. For ARC-AGI-3's current leader, the winning harness
  is deliberately unpublished, so forking is not an option at all.
- The public material is a leaderboard score with no mechanism behind it. A number you cannot
  explain is a number you cannot improve on, and you cannot debug.
- You need to change something structural, and the fork's architecture fights you.
- The public cluster is saturated: when the leaderboard is dense and everyone's notebooks share a
  base, a fresh approach is worth more than another variant.
- The competition scores efficiency, not just completion, and the public solution is slow.

## How to adapt safely

1. **Pull it, do not retype it.** `kaggle_kernel_pull` on a public notebook, or clone the repo.
   A hand-copied solution drifts from the original and the comparison becomes meaningless.
2. **Claim it in your own name.** Editing the pulled `kernel-metadata.json` is mandatory: set `id`
   to your own `owner/slug` and change the title. Pushing unchanged would write to the original
   author's notebook.
3. **Record the provenance in the experiment tree.** The node's `change` says "forked
   `owner/slug`, changed X". An unlabelled fork is indistinguishable from your own work later.
4. **Establish the fork's own baseline before changing anything.** Run it unmodified, record the
   number. Without that, every later delta is measured against nothing.
5. **Make one change, then measure.** The tree's one-variable rule applies to forks exactly as it
   does to your own code.
6. **Check the licence.** A public notebook is not automatically licensed for reuse. Several
   strong ARC-AGI-3 repositories carry no licence file, which is a real constraint on derivative
   work and worth surfacing to the user rather than ignoring.

## The decision, recorded

### When the user is not watching

This decision follows `presence-mode` (an `asks` edge in `../relationships.json`). Read
`kaggle_presence action="get"` before putting the fork-vs-write question to the user.

**Present** — ask. Fork and write lead to genuinely different plans, which is a tier-3 shape of
question even though neither is destructive.

**Away** — default to the **auditable** path: fork-and-adapt a public solution, and record the
node, because it is reversible, it is the one you can defend on their return, and writing your
own blind is the harder thing to undo. Record it:

```
kaggle_presence action="record"
  decision="defaulted to fork-and-adapt the top readable public solution"
  rationale="reversible and auditable; the tree records the node so the call can be revisited"
```

Whichever way it goes, the node goes on the experiment tree either way, so the user inherits a
reasoned decision rather than a silent one. If `action="record"` returns `stopped: true`, stop
and wait rather than committing to an approach.

Write the decision into the experiment tree as a node, even though it is not an experiment:

```json
{
  "change": "fork jeroencottaar/taaf-duck-harness instead of writing a harness",
  "hypothesis": "a proven harness gives a usable baseline faster than a from-scratch build",
  "verdict": "keep",
  "reason": "readable, 336 votes, milestone-1 winner; the current leader's harness is unpublished so there is nothing better to copy"
}
```

Later, when the fork stops paying off, the tree shows exactly when that stopped being true. Without
the node, the next session re-litigates the same decision from scratch.

## The check that settles it

If you cannot state, in one sentence, **what you will change in the public solution**, you are not
adapting it, you are copying it. That sentence is the whole justification. If there is no such
sentence, write your own.
