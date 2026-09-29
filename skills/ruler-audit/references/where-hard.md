# Where is it hard, and is the ruler the reason?

The adversarial-sampling argument is **quoted verbatim** from Anthropic's blog *Automating eval
design and hillclimbing with Claude*, Lance Martin, 2026-09-28
(`https://claude.dev/blog/automating-eval-design-and-hillclimbing/`, §"Adversarial sampling"). The
input-source ordering is quoted from `build-eval.md` Step 1 (Apache-2.0), readable at
`upstream/build-eval.md:41-54`.

## Adversarial sampling, verbatim

> Model capability is jagged. If you pick cases because today's model fails them, you are sampling
> the valleys of one model's capability surface (Figure 2). The evaluation can end up measuring
> that model's failure fingerprint rather than what is intrinsically hard or valuable for your
> application to do.
>
> Pick hard cases because a human judged them hard: a useful test is to be able to say why a task
> is hard before you include it. Include cases that are specific failures in your application
> derived from production traffic, bug reports, or tickets. However, don't blindly trust user
> traffic: users sometimes try what they expect to work, so a task distribution drawn strictly
> from user traffic may skew easy.

Note what that last sentence does: it rules out the obvious source, not just the obvious selection
rule. Both halves are needed — "pick hard cases" without "don't trust the traffic" leaves the
method with a bias in the other direction.

## The same trap, in a competition

Selecting a direction because the current approach is weak there is the same error with different
words: the search optimises the weakness instead of the objective.

**The leaderboard makes it worse.** A public leaderboard is one specific measuring instrument, and
thousands of people have already been measured against it. A direction chosen because "the public
LB drops there" is chosen from *that instrument's* failure fingerprint.

The test, in the form you have to finish before spending a quota slot:

> This direction is worth the next twelve hours because **____________**.

Valid completions name the task: *the target is genuinely rare and the features that predict it are
absent*; *this slice is 8% of the test distribution and the current approach has no signal on it*;
*two domain experts would agree this is hard*.

Completions that name the ruler are the failure mode: *the public LB punishes it*; *the top
solutions all do this*; *it is where we lose the most points*.

**"The top solutions do this" is the strongest available evidence that a direction is fitted to the
board rather than to the task.** It is a reason to be suspicious, not a reason to copy. Whatever
makes a top solution work is very likely something about how the board is computed, and it may be
something that does not hold on the private split.

So the sentence to finish before spending a quota slot is not "the public LB drops there" but
"this is hard because ____" — and the blank has to be fillable with a fact about the data. If the
only available answer names the leaderboard, that is the ruler's failure fingerprint, not the
task's difficulty.

## Where the real difficulty tends to live

Upstream's input-source ordering, quoted from `build-eval.md:47-52`. The source numbers and the
warning attached to each are the argument; the ranking is by fidelity:

> **Either way,** ask where realistic inputs could come from. Work down this list and use the first
> source that's available and that the user is comfortable using:
>
> 1. **Production transcripts or logs.** The highest-fidelity source. Ask where they live (Datadog,
>    a database, S3, a logging endpoint) and whether you can pull a sample. Before you pull
>    anything, confirm the source is **usable in practice**, not just available right now: *Is there
>    a retention policy that will force you to delete this data? Does it contain PII that can't sit
>    in a repo?* An eval built on data the user can't keep is an eval they can't re-run next
>    quarter - that's worse than a synthetic one they can. If either answer is yes, three options:
>    store only the **identifiers** in the repo and have the runner fetch the real inputs at eval
>    time (nothing sensitive ever lands on disk); have the user pull and anonymize a sample
>    themselves; or rewrite each real input into a synthetic one that preserves the shape and
>    difficulty but replaces the identifying content (show the user the rewrites before using them).
> 2. **Bug reports, support tickets, or "this went wrong" examples.** Often the most valuable
>    inputs are the ones someone complained about. Ask if there's a channel or tracker where these
>    collect.
> 3. **Hand-written by the user.** Ask them for five to ten examples off the top of their head.
>    These are usually skewed toward what's salient to them rather than what's frequent, so treat
>    them as a seed, not the whole set.
> 4. **Synthesized by you from the codebase.** Read the system prompt, the tool descriptions, and
>    any docs or tests, and generate candidate inputs that exercise the flow. This is the
>    lowest-fidelity option - make that clear to the user, and don't do it cold: first get three to
>    five real examples from them (source 3) plus a sentence on what makes a case *hard* in this
>    domain, then synthesize variations of those rather than inventing from the prompt alone.

Note that upstream's order is not "most useful first" — it is **most faithful first**, and the two
are not the same. A complaint is often the most valuable input and ranks second.

The first entry's warning is the part a competition has no direct use for and the part worth
keeping anyway: **a validation set built on data you cannot keep is a validation set you cannot
re-run**, and a measurement that cannot be repeated stops being evidence. The three fallbacks it
offers — store identifiers and fetch at run time, have the owner anonymise, or synthesise while
preserving shape and difficulty — all apply to a competition dataset with a retention limit or a
licence that forbids redistribution.

Translated into what a competition has instead of an application:

1. **The slices where the data distribution is genuinely different**, not merely unmodelled.
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
