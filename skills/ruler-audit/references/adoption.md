# Adoption gates

The three gates are **quoted verbatim** from Anthropic's `cost-hillclimb.md` §"Adoption gates"
(Apache-2.0), reproduced in full below and readable at `upstream/cost-hillclimb.md:328-346`.
Upstream registers them before the first round so that a candidate is accepted against criteria
fixed in advance, rather than against whatever the round happened to show.

## The three gates, verbatim

> A candidate change (prompt edit, effort cut, model swap) is adopted only if ALL three
> pre-registered gates pass:
>
> 1. **Quality band** - held-out score within a named band of the incumbent (state the
>    band before running).
> 2. **Cost margin** - strictly cheaper beyond a registered margin, measured at the
>    stated pricing basis.
> 3. **Mechanism** - the *predicted* mechanism appears in the measurements (e.g. "this
>    edit removes duplicate lookups" must show up as fewer tool calls, not just a lower
>    bill). A cost tie with the right mechanism and a cost win with the wrong mechanism
>    are both rejections: the first is an edit that didn't bite, the second is an
>    unexplained confound that will not survive contact with production.
>
> The final joint confirm (Step 5 of the search order) reports against these same gates;
> three sequential selections, each made on the data that chose it, overstate the
> combined win, so the confirm's number - not the per-round selection scores - is the
> headline.

Two of those carry over without change. The **quality band** is the tree's `anchor` — a held-out set
the search may not score on, which upstream takes as given and which `kaggle_experiment_tree`
enforces. The paragraph on **three sequential selections** is a statement about statistics, not
about LLMs, and it is the reason a run of kept nodes is reported as the final configuration's
measurement rather than as the sum of individual deltas.

## What changes for a competition

**Cost margin** is the one gate that needs restating: a competition run optimises a leaderboard
score, so the quantity being bought is usually quota hours, wall-clock, or memory — whichever the
change was actually meant to move. The gate keeps its shape: strictly better beyond a margin
registered *before* the run, measured on a stated basis.

**Mechanism** transfers intact, and it is the sharpest of the three because the asymmetry is the
whole point — a tie with the right mechanism and a win with the wrong one are both rejections, and
the second is the dangerous one.

## The mechanism gate, in competition terms

"Cheaper" is an outcome. "Why it is cheaper" is a prediction, and the prediction is falsifiable
while the outcome is not.

| Declared mechanism | Must appear as | Not sufficient |
|---|---|---|
| the change removes a redundant fit | fewer training runs in the log | a lower final score |
| the change caches the embedding | a cache hit on the second pass | a faster wall-clock |
| the change subsamples the negatives | fewer rows read | a lower quota bill |
| the change shortens the context | fewer input tokens billed | a faster run |
| the change early-stops the augmentation | the loop terminating before the cap | an unchanged time |

**A margin with the right mechanism and a tie with the wrong one are both rejections**, and the
second is the dangerous one. A win with no mechanism is a confound. It will not survive the next
change that touches the same region, and shipping it means shipping a number whose cause you do
not know.

The cheapest test: name the mechanism **in the `declare`**, before the run. Then check the log for
it. A mechanism named afterwards is a story fitted to whatever the run showed.

## When the goal is not the score

Most of a competition run optimises the score. Three situations do not, and all three are where
these gates earn their keep:

- **A saturated metric.** When the public score is near the ceiling, the score cannot discriminate
  and the real question becomes quota, runtime, or robustness. Quality becomes a **constraint**, not
  the objective.
- **A quota-budgeted run.** With limited accelerator hours left, the question is what to spend them
  on, and a change that is 20% cheaper per run is worth more than a change that scores 0.1 higher.
- **A submission-shaped change.** Ensembling, threshold tuning, TTA — changes whose value is not
  fully visible in CV and must be justified against the stated band.

**An optimiser only optimises what was registered.** Upstream measured a pure-quality hillclimb
that raised cost per delivered unit by 75% — nobody asked it to, and nothing stopped it. If the
goal is "cheaper at equal quality", the three gates **are** the goal, and they belong in the
declared plan, not in a retrospective.

## The confirm reports against the same gates

Upstream's final joint confirmation reports against the same three gates, and the reason is a
statistical one worth keeping: **three sequential selections, each made on the data that chose it,
overstate the combined win.** The confirmation's number is the headline, not the per-round
selection scores.

The tree's analogue: a node kept because its own delta cleared the floor is a selection made on
that node's data. Three such selections in a row are three chances to be lucky, and the apparent
cumulative gain is larger than any of them. When a run of nodes is reported as a result, the
honest statement is what the **final** configuration measures against the baseline — not the sum of
the individual deltas.

## Registering a gate

There is no dedicated gate field in the tree, and inventing one would be the wrong move: the
`anchor` already exists for the one decision that must be mechanically enforced (which set may not
be scored on), and a second enforcement point would blur that.

Register a gate in the places the tree already treats as declarations:

- **The band and the margin go in the `hypothesis` and `reason` of the `declare`.** A declaration
  states what is expected before the run; a threshold fixed afterwards is not a gate.
- **The mechanism goes in the `declare` too**, in the same place, phrased as a prediction about the
  log rather than about the score.
- **The held-out side is enforced by the `anchor`.** A quality band is measured against it, and the
  tree already refuses nodes that score on the anchor directly.

So a gated `declare` looks like:

> (this plugin's own syntax, not a quotation)
> change: replace the full-sample fit with a 60% subsample
> hypothesis: score within 0.002 of the parent on the held-out slice, at 0.6× the training cost,
> with the mechanism showing as roughly half the fitted rows in the log
> reason: the anchor slice is already within the noise floor of the leaderboard, so quota bought
> elsewhere is worth more than a further 0.002

Every clause is checkable after the run, and all three were written before it.

## Reporting

State the three verdicts, not a single judgement: quality band (pass/fail, with the number), margin
(pass/fail, with the number), mechanism (seen/not seen, pointing at the log line).

A candidate that fails only the mechanism gate is **not** a failed candidate — it is an edit that
did not bite, and the useful next move is to find out why rather than to discard it. A candidate
that fails only the quality band has made a real trade, and that trade is sometimes exactly what
was wanted; say so rather than reporting it as a loss.
