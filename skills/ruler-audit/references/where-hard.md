# Where is it hard, and is the ruler the reason?

Adapted from the adversarial-sampling section of Anthropic's blog *Automating eval design and
hillclimbing* (2026-09-28) and `build-eval.md` Step 1 (Apache-2.0). The question is upstream's;
the competition form of it is sharper than the original, because a public leaderboard is a ruler
that thousands of other people have already been measured against.

## The jagged capability surface

Model capability is **jagged**. A given model is excellent at some task shapes and poor at others,
with no smooth gradient between them.

This creates a specific failure when choosing what to work on: **if you select cases because the
current model fails them, you are sampling the bottom of this model's capability surface.** The
eval then measures that model's *failure fingerprint* rather than what is intrinsically hard or
valuable for the problem.

The same trap in a different costume: select a direction because it is where the current approach
is weak, and the tree will optimise the weakness instead of the objective.

> The useful test: **before committing to a direction, can you say why it is hard?**

Not "the score is low there." Why is it low — is the signal genuinely absent, is the label
ambiguous, is the metric punishing a valid solution, or has this exact pattern been found already?

## The leaderboard is a fitted ruler

A public leaderboard is a specific measuring instrument, and it has been measured against many
times. Everyone who has entered this competition has looked at where it deducts points.

So a direction chosen because "the public LB drops there" is a direction chosen from **that
ruler's failure fingerprint**, not from the task's difficulty. It optimises agreement with a
measurement that has itself been optimised against.

The test, stated as a sentence you must be able to finish:

> This direction is worth the next twelve hours because **____________**.

Valid completions name the task: *the target is genuinely rare and the features that predict it are
absent*; *this slice is 8% of the test distribution and the current approach has no signal on it*;
*two domain experts would agree this is hard*.

Invalid completions name the ruler: *the public LB punishes it*; *the top solutions all do this*;
*it is where we lose the most points*.

**"The top solutions do this" is the strongest possible evidence that the direction is fitted to
the board and not to the task.** It is a reason to be suspicious, not a reason to copy. Whatever
makes a top solution work is very likely something about how the board is computed — and it may be
something that stops working on the private split.

## Where the real difficulty tends to live

Upstream's answer, in competition terms — these are the sources that survive the "why is it hard"
test:

1. **Production or real-traffic analogues.** The failure modes people actually complain about. In a
   competition: the slices where the data distribution is genuinely different, not merely
   unmodelled.
2. **Known failure reports and forum threads.** Someone already paid to discover it.
3. **What the user finds salient.** Ask what they think is hard, treat it as a seed — it is
   systematically skewed toward what they have already hit rather than what is frequent.
4. **Synthesised from the domain.** Lowest fidelity. Do not do it cold: get three to five real
   examples plus a sentence on what makes a case hard in this domain, then synthesise variations
   of those. Cases synthesised with nothing real to anchor on come out simplistic, and steering
   them afterwards costs more than writing them would have.

Note what is **not** on the list: "where the current model scores lowest."

## Do not trust the traffic blindly

Upstream's warning carries directly: users mostly try what they expect to work, so a distribution
sampled strictly from observed traffic skews easy.

The competition form: the rows in the training set that look most like the test set are not
uniformly the rows that are hard, and the samples that dominate a local proxy may be the ones whose
difficulty comes from a quirk of how that proxy was built.

## Sizing the set against the change you hope to detect

From `build-eval.md`: aim for somewhere between fifteen and a hundred inputs for a first eval.
Fewer than fifteen and a single flaky case swings the score; well past a hundred and nobody
actually reviews them all, which defeats the point of the sign-off.

The competition form is the fold count and the repeat count rather than a case count, and the same
arithmetic applies: **size the validation set against the change you hope to detect**, not only
against reviewability. Fifty folds with a held-out slice, or ten folds with more repeats, are two
routes to the same resolution.

`references/resolution.md` has the numbers.

## The two sign-off points, and why they are pauses

Upstream stops twice for explicit approval, and the stops are not ceremony — the expensive failure
is a metric that is agreed late, after several paid rounds have been read against it.

Mapped to this plugin, both are `ask_user` **text**, never a widget, following the same rule
`kaggle-competition-research` uses: these are sentences to answer, not forms to fill in.

1. **The input set is right.** Show the actual thing, not a summary. In a competition: the fold
   definition, the split, the validation rows, the stratification. Any observation offered about it
   is quantitative — counts, named slices, measured spread — not "looks reasonable." Ask: *are these
   representative of what the test set actually contains, and are there obvious slices missing?*
2. **The grading is right.** The metric, the direction, and what a good solution scores. Upstream
   states the reasoning plainly: the sign-off is the thing that makes the number trustworthy later.
   Skipping it produces a setup that is technically runnable and practically ignored.

**If the answer is "mostly, but…"** — fix the "but" and show it again. If the numbers changed
during the discussion, restate the final version in one message and get an explicit yes. Do not
carry an unconfirmed figure into the tree.

## Writing it down

The calibration, the split, and the metric definition go into the tree — `action="calibrate"`,
the `anchor` declaration, and the node's own fields — not into the conversation. The next session's
first `declare` has to read the same figures, or agreeing on them was worth nothing.
