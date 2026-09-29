---
name: "ruler-audit"
description: "Use before declaring the next experiment, or when the tree has gone flat - establishes whether the metric can resolve the change you are about to make, keeps seed noise and rebuild noise apart because they call for opposite remedies, tells a hard task from one this leaderboard happens to punish, and decides whether a stall belongs to the approach or to the ruler. Adapted from Anthropic's public claude-api eval guides (Apache-2.0); the experiment tree is the ledger here, so none of the upstream file layout is carried over."
license: "Apache-2.0. Adapted from Anthropic claude-api shared/evals (build-eval.md, eval-audit.md, eval-hillclimb.md, cost-hillclimb.md) and the blog post 'Automating eval design and hillclimbing with Claude' (2026-09-28), Copyright 2026 Anthropic, PBC. The four guides are shipped byte-for-byte under references/upstream/ and the domain-neutral reasoning is quoted from them; what is adapted here is the vocabulary - competition data, validation splits, metrics and the tree as the single ledger - the two variance layers Kaggle adds, and the four stall buckets this plugin already implements under other names. Every quoted block is asserted verbatim by tools/verify_upstream_quotes.py. See NOTICE.md."
---

# Ruler audit: is the metric able to see what you are about to do?

A competition tree fails in one way that no amount of good methodology catches, and it fails
quietly. The change is real, the implementation is correct, the run is reproducible — and the
delta is smaller than the distance the metric moves when you re-roll it. The node reads as a small
win, gets kept, and the next five nodes inherit a base that was never better.

This skill is about that question, and only that question. It is not about picking the next
experiment (`approach-decision` chooses the angle, `ablation-design` shapes the batch) and not
about recording the outcome (`rsi-experiment-tree` owns the ledger).

## What this skill can and cannot do

**In code**, on the tree:

- `action="calibrate"` records how finely this metric resolves a difference, and refuses a
  calibration that is two readings wide or a mean that disagrees with its own values.
- `action="declare"` then **refuses** a declaration whose `atLeast` sits inside that floor, with
  the floor, the headroom and the smallest actionable delta in the refusal text.
- `action="settle"` judges against the **largest** of three floors — what you declared, what this
  arm's own repeats spread over, and the calibrated floor — and names in `floorFrom` the terms
  that tied for it.
- `action="regrade"` re-judges every stored node under the current calibration and reports whether
  the ranking flipped, without writing anything.
- `action="status"` reports whether the tree is calibrated, and `calibrate`'s `sideCheck` compares
  the search and held-out sides once both carry settled nodes.

**In prose only** — the part no tool can enforce, and the reason this skill exists: deciding that
a delta inside the floor means *stop adding repetitions and change the metric*, rather than
running the same arm again; deciding that a flat tree is a broken judge rather than a wrong
approach; deciding whether a direction is hard because the task is hard or because this
leaderboard happens to punish it.

The tree enforces arithmetic. This skill supplies the judgement the arithmetic points at.

## Stage 1 · Calibrate, before the first declaration

Do not invent a noise floor. Derive it from arms that were already run, using
`action="ablate"` to find configurations that appear twice, and from
`factorsIntent: "repeat"` nodes, which change nothing on purpose and therefore measure the
machine rather than the idea.

**Keep the two variance layers apart.** They are the same number in most trees and the opposite
number in meaning:

| Layer | What varies between repeats | Stored as | Wide means |
|---|---|---|---|
| Seed | the seed, the data order | `seedSpread` | add repetitions — the estimate sharpens |
| Rebuild | the feature cache, the preprocessing fold, the previous round's prediction file | `rebuildSpread` | take the rebuild out of the measured path — more repetitions measure the same rebuild again |

A `repeat` arm that rebuilt its feature cache is not measuring seed noise. It is measuring both,
lumped, and that lumped figure is then used to justify adding repetitions to a metric whose
resolution does not depend on them. The `controls.rebuild` key is what separates the two — set it
to state whether the measured artifact was rebuilt, independently of whether the seed moved.

This is not a distinction imported from elsewhere. It is forced by the numbers: Anthropic measured
three rebuilds of one prompt at about 7 points of spread while re-scoring the same artifact moved
only ±1.4. Read as one figure, that is a blur; read as two, the large one is the one that decides
how many times you need to build.

```json
{"ruler": {
  "noise": 0.031, "noiseFrom": "cv-splits, 5 repeats",
  "samples": {"n": 5, "values": [0.671, 0.684, 0.702, 0.679, 0.686], "mean": 0.6844, "std": 0.0121},
  "headroom": 0.28, "smallestActionable": 0.02,
  "seedSpread": 0.0121, "rebuildSpread": null
}}
```

`calibrate` requires `n >= 3` with values that reproduce the stated mean, a noise figure above
zero, and at least one of the two spreads. It returns `incomplete` naming `headroom` and
`smallestActionable` when you left them out, and the refusal message quotes whichever are
missing — so fill them in now, not when the first refusal arrives.

**Write the calibration into the tree, not into the conversation.** The next session's first
`declare` has to read the same figure, or calibrating was worth nothing.

**Interview, once, before the first run — not per experiment.** Two sign-off points, the shape
upstream uses, because the expensive failure is a metric that is agreed late:

1. **What is the metric, and what is it measuring at?** Ask with `ask_user`, as text, following
   the same rule `kaggle-competition-research` uses: this is a sentence to answer, not a form to
   fill in. Options are whole measurement surfaces — a leaderboard score, a CV score, a
   per-fold aggregate, a local proxy — and each states what it costs.
2. **Is the calibration above acceptable as the floor for the whole run?** Restate the final
   numbers in one message and get an explicit yes. If the numbers changed over the discussion,
   restate once more; do not carry an unconfirmed figure into the tree.

Do not re-ask either one before every `declare`. The per-declaration check is stage 2, and it is
arithmetic against the recorded floor.

## Stage 2 · Resolution check, before every declaration

Three numbers side by side, every time:

- **the noise floor** — `ruler.noise`
- **the headroom** — how far the current best is from the ceiling worth reaching
- **the smallest delta worth acting on** — `ruler.smallestActionable`

If the floor exceeds either of the other two, the experiment as declared cannot be read. Levers in
cost order:

1. **More repetitions.** Cheapest, and only correct when `seedSpread` is the dominant layer.
2. **More folds / a different CV scheme.** Correct when the variance is in the split, and it
   invalidates the old calibration — recalibrate, then regrade.
3. **A finer-grained metric.** A scalar that saturates cannot resolve anything near its ceiling.
4. **Iterate on a discriminative subset, confirm on the full set at the end.** Buys iteration speed
   at the cost of a final check that must not be skipped.

Put the answer in the `hypothesis` and `reason` of the `declare` itself, not in a separate note.
A node that says "0.004 expected" with the floor quoted beside it is a different record from one
that says "0.004 expected, below the 0.031 floor, so this arm exists to measure the rebuild
spread rather than the feature".

**Headroom near zero is its own decision.** When the public score is already high, grinding
features against it has falling returns; the moves left are the model, ensembling, or a different
objective. Say so before spending the next twelve hours on a lever whose ceiling is in reach.

**Then, and only then, look for published method.** The order is not a preference. A change the
metric cannot resolve produces a finding that is real, legible and unreadable, so hunting for
a better method to make it is twelve hours spent on a number that will mean nothing. Once the
ruler is known to see the result, `consider` carries a `skills` block: `kaggle_methods
action="search"` against the same change and hypothesis, read from the local index with no
network call. If it returns a candidate that is already on disk, read it before you declare. If
it returns one that is not, it comes back marked `not downloaded` — fetching is a separate,
explicit call, and never a thing that happens by itself between one declaration and the next.

**Then write the hypothesis properly, because the declaration is where it gets written.**
`references/kdense/hypothesis-generation.md` is vendored for this. Three of its moves earn their
place, and they are moves about *shape* rather than about biology:

- **A rival, not a preference.** "Warm-starting helps" cannot lose. "Warm-starting helps *because*
  the encoder is under-trained at this budget" can, because at twice the budget it stops. The rival
  goes in the `declare`'s own `hypothesis` field, so the tree carries it and the next reader sees
  what would have counted against it.
- **One observation that would refute it, under the assumptions you are actually making.** A
  change with no refuter is a preference wearing a delta, and it survives its own disconfirmation
  because nothing was ever going to disconfirm it.
- **Keep the labels apart.** A mechanistic story is not a prediction, and a prediction is not
  evidence. The `metric` you declare against is evidence about the base, not about your idea, and
  the delta is a prediction's worth of one.

**One upstream rule is not carried, and the disagreement is informative.** Upstream forbids the
tool scoring, ranking or rejecting a hypothesis. This plugin does exactly that: `consider`
returns a verdict and `settle` records one. The two are answering different questions — upstream's
is a scientific claim, this one's is an experimental action — and the useful reading of upstream's
rule is "the judge is not the author of the claim", which is the same reason a `verifier` reviews
the report in `technical-report` and does not write it.

## Stage 3 · Verdict review, after every settle

`settle` already refuses to call a node `confirmed` when the delta is inside
`max(atLeast, arm std, ruler noise)`, and `partial` exists precisely so that "the direction was
right" does not read as a success.

What the tool cannot answer is the question you should ask next: **is the difference between this
delta and the floor an effect, or the resolution limit?** Read `floorFrom`.

- `declared` — you promised a bigger move than the metric can see. The prediction was miscalibrated.
- `arm` — this arm's own repeats were wide. More repetitions on the same splits will sharpen it.
- `ruler` — the metric's own resolution is the binding constraint. **Adding repetitions to this
  arm will not help**: repeating one configuration on the same splits does not make the splits
  finer. Change the measurement surface.
- a combination — both, and they need different remedies, so they are named separately.

`floorFrom` names only the terms that **tied** for the floor. A ruler well below the arm's spread
is not named, because it did not cause this downgrade and putting it in the explanation sends you
off to fix the metric when the arm was the noisy thing.

**Then grade it, because a downgrade is not one kind of event.**
`references/kdense/scientific-critical-thinking.md` is vendored for this. Its contribution is
severity grading: "the finding is a bit soft" hides which remedy applies, and the two remedies
here are opposites.

| Severity | What it looks like on this tree | What it costs |
|---|---|---|
| **Critical** | the measurement cannot support the verdict as recorded — a split that leaked, `parent` and `result` scored by different metrics, a node carrying no `controls` at all | re-run, do not re-read |
| **Important** | the verdict stands and its stated reason is wrong — a `floorFrom` naming the wrong term, a `reason` citing a bottleneck the log does not show | correct the node's prose; the number stands |
| **Minor** | presentational — a missing unit, an unstated `n`, a figure with no noise band | noted, fixed in the next settle |

Two of upstream's rules are load-bearing and are taken as written: **judge the methodology, not
the result** — a stage-3 review that recommends a bigger delta because the current one was small is
reviewing the result, and will recommend a bigger one forever — and **name the specific bias, not
the category**. "The calibration was taken on the same folds every arm is scored on" tells you what
to do; "selection bias" does not.

Not carried: upstream's optional GRADE and Cochrane Risk-of-Bias figures, which are drawn by a
separate skill through a paid external image API this package does not have. The grading survives
without them, which is the only reason to take it.

## Stage 4 · Stall triage, after two or three flat rounds

Before running more rounds, read the remaining failures and classify them. Do this earlier — the
moment no single change can plausibly exceed the floor — not only when patience runs out.

| Bucket | What it looks like | Where this plugin already handles it | Do |
|---|---|---|---|
| Artifact gap | the model is missing a fact it needs | the tree's home case | keep going |
| Harness / infrastructure | it broke before producing a scoreable output | `failureLayer`: `tool-recovery`, `output-contract`, `state-continuity` | fix the harness; exclude the errored rows from the denominator |
| Structural | the content is there and the run never reaches it, or one finding recurs across rounds | `parent: null` swap | restructure — do not add more unreachable content |
| Variance | the same code flips between pass and fail more than the between-round difference | `partial` verdicts and `ablate`'s repeats | report the best arm, then raise repetitions or change the target |
| **Judge / ruler disagreement** | a correct submission scores badly, or the metric says two different things about the same file | **new: a research node with `targets: ["ruler"]`** | re-judge in place under the corrected rule, then regrade |

The first four are not new mechanisms. They are existing ones collected into one table, and the
table is the point: it turns "should I change the code or the yardstick" into a lookup. The fifth
is the one bucket with no home, and it is the one worth stopping for.

**A zero caused by a crashed run is not a bad score.** Retries exhausted, slot ceilings, empty
outputs — those transcripts belong to the harness bucket, not the variance one, and counting them
as failures makes a working approach look broken.

**`failureLayer` and the ruler bucket must not be merged.** One asks which runtime layer broke; the
other asks whether the fault is the measurement. Two orthogonal spaces sharing one enum would make
"the tool call failed and scored zero" and "the judge marked a correct answer wrong" the same
value, and those call for opposite responses.

Full classification, with the upstream cases behind each row, in `references/triage.md`.

**A flat line is also a reason to look outside the tree.** Every row above is a bucket this
plugin already has a mechanism for, and that is exactly why a stall can persist through all of
them: an approach can be correctly diagnosed as a variance problem, repaired, re-run, and still
be the wrong approach. After the triage, ask `kaggle_methods action="search"` what published
experimental method exists for the thing being attempted, using the same change and hypothesis
you would declare. Local index, no network, no download unless you ask for one by name. A stall
is the moment where re-deriving something that was published is most likely, because the
attempts that would have found it have all been spent.

## Stage 5 · Re-judge after the ruler changes

Run `action="regrade"` when the measuring surface moved underneath the tree: a new CV scheme, a
corrected scoring rule, or the discovery that the public leaderboard had already been fitted to.

It writes nothing. The tree is append-only and ids are never reused, which is the whole reason it
can be cited as evidence; re-judging is running the same stored results past a different floor and
keeping both answers.

Read two fields together, never one alone:

- **`priorBestStillBest`** and **`collapsesIntoNoise`** are different outcomes with opposite
  responses. Still best with the lead intact: the earlier rounds hold. Still best with the lead
  collapsed into the floor: the gains were probably real and the old calibration could not resolve
  them — keep the node, and size the next experiment against the floor rather than the last delta.
  **Ranking flipped:** those rounds were optimising something the metric could not distinguish;
  go back to the baseline rather than building on the old best.

- **`verdictsFlipped`** counts nodes whose `confirmed` / `partial` / `refuted` changed, which is
  often larger than the ranking change and is the honest measure of how much of the tree was
  resting on a resolution the ruler did not have.

A rank flip after a regrading round is the single most informative result this skill can produce,
and it is the reason upstream's own case study has an audit step that **re-judges in place** and
compares before and after, rather than editing the stored scores.

## What is not carried over

Upstream's guides are built around a parallel file layout — `results.jsonl`, `traces/`, `_state.json`,
`summary.json`, `vN/` directories, and a report builder. None of it comes here.

The tree is the ledger. It is append-only, ids are never reused, and every verdict carries the
evidence it was judged against. A second set of files beside it is two histories for one
competition, which is exactly what `action="alias"` exists to prevent.

What was taken is the judgement: the five buckets, the three-number resolution check, the two
variance layers, the sign-off points, and the re-judging discipline. The sign-off points survive
as `ask_user` interviews, because upstream's `AskUserQuestion` is the same gesture.

## References

The upstream guides are shipped whole, byte-for-byte, in `references/upstream/`. The passages
quoted in the files below are quoted from them, and `tools/verify_upstream_quotes.py` fails the
build if a quoted block cannot be found in an upstream source character for character.

| File | What it holds |
|---|---|
| `references/upstream/` | Anthropic's four guides, unmodified, plus the cited blog sections |
| `references/checklist.md` | The five audit groups, mapped to competition semantics |
| `references/resolution.md` | The three numbers, and the two variance layers in detail |
| `references/triage.md` | The five stall buckets, quoted, and which four are already implemented here |
| `references/where-hard.md` | Adversarial sampling, and the leaderboard as a fitted ruler |
| `references/adoption.md` | Three registration gates, quoted, for a tree that is also buying something |

What is quoted and what is restated: the reasoning in these guides is about measurement and holds
whatever is being measured, so the noise-floor arithmetic, the build-variance finding, the five
buckets, the three gates and the adversarial-sampling argument are quoted intact. What is restated
is the vocabulary they are applied in — `results.jsonl`, `traces/`, `runner-scaffold.mjs`,
`AskUserQuestion`, transcripts and memory stores belong to an application evaluated by an LLM, and
none of them exist in this package.

`NOTICE.md` carries the Apache-2.0 attribution.
