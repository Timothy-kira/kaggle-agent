# Stall triage: five buckets

The five buckets below are **quoted verbatim** from Anthropic's `eval-hillclimb.md` Step 4.5
(Apache-2.0), with the original wording intact. Read them at `upstream/eval-hillclimb.md:285-293`
against the full step, and `upstream/README.md` for the licence and the rest of the sources.

Four of the five are **already implemented in this plugin under other names** — they predate the
upstream material and were not adapted from it. The table's value is that it puts upstream's
reasoning and this package's existing mechanisms side by side, so "should I change the code or the
yardstick" becomes a lookup. The fifth bucket is the only one with no home, and it is the one worth
stopping for.

## When to do this

The tell is **two or three consecutive nodes whose delta did not clear the floor**, despite changes
that should have helped.

Do it earlier than patience demands: the moment no single change can plausibly exceed
`ruler.noise`, not only once the tree has gone flat. Rounds spent grinding past that point are
rounds spent learning nothing.

Read every remaining failure and bucket it by root cause. Then dispatch **per bucket** rather than
running another round against all of them.

## The upstream table, verbatim

> | Bucket | Tell | What to do instead of another content round |
> |---|---|---|
> | **Artifact gap** | Model never had the fact it needed; transcript shows it guessing or searching | This is the loop's home turf - keep going |
> | **Grader disagreement** | Model's output looks correct to you but the grader marks it wrong; or the prompt and the rubric ask for different things | Fix the grader, then re-grade *every* variant in place from stored outputs. Before overwriting, compare old vs new grades - how many cases moved, and did the variant ranking change? If the previous best is still the best and its lead over baseline held, keep going. If the ranking flipped or the lead collapsed to noise, the prior rounds were tuned to the wrong signal: show the before/after table and propose restarting the loop from baseline. |
> | **Harness / infra** | Case errored before the model produced a scorable output - auth failure, timeout, rate-limit, env setup. Some harnesses *score* the failure instead of erroring it: zero-scored cases whose transcripts carry infra markers (retries exhausted, stall ceilings, empty outputs) belong here too | Fix the harness; exclude errored cases from the denominator until then. For scored-in zeros, decide the handling rule before comparing scores |
> | **Structural** | The content exists in the artifact but the model didn't reach it; or the same review finding recurs across rounds; or one dimension (a language, a provider) underperforms regardless of which feature you target | Reorganize - consolidate duplicated facts into one table, split a monolith file, fix the routing - rather than adding more of the unreached content |
> | **Variance** | Pass<->fail flips between identical-code runs are as large as the round-over-round delta | You're at the noise floor on this lever. Report best-so-far; offer to raise reps or change target |
>
> A failure that fits none of these is itself a signal: the artifact you're tuning may not be the
> bottleneck for that slice - offer to change target rather than forcing it into a bucket.

## The same table, mapped to what this plugin already has

The third column is the only part that is not upstream's. Each row names a mechanism that already
exists in `mcp/experiment_tree.py` — none of these four were introduced by this skill.

| Upstream bucket | Already here as | The node or field that carries it |
|---|---|---|
| **Artifact gap** | the tree's home case | an experiment node whose `change` is real and whose `hypothesis` predicted a move |
| **Harness / infra** | `failureLayer` on a reverted node | `output-contract`, `tool-recovery`, `artifact-persistence`, `state-continuity` — required whenever `verdict: "revert"` |
| **Structural** | the `parent: null` swap | a node with an explicit null parent opens a new direction instead of extending a spent one |
| **Variance** | `partial` verdicts, and `ablate`'s repeats | `factorsIntent: "repeat"` changes nothing on purpose; `ablate` derives the floor from configurations that appear twice |
| **Grader disagreement** | **new — no existing node carries it** | a research node with `targets: ["ruler"]` |

**Grader disagreement is the only bucket this skill adds.** It has no home in the four mechanisms
above, which is why `RESEARCH_TARGETS` gained `"ruler"`: a research node that goes back to the
measurement rather than to a forum, a paper or a repository. It is the one worth stopping for,
because concluding that the yardstick is wrong invalidates the work already recorded — and that is
the most expensive conclusion in a competition search, so the one most likely to be avoided.
Having a named bucket is what makes it cheap to say out loud.

## The new bucket, in this package's terms

The upstream action for this bucket is quoted above and applies unchanged: fix the grader, re-grade
every variant in place from stored outputs, and **compare before overwriting** — how many moved, and
did the ranking change. Three details map onto what exists here:

- **Re-grading in place** is `action="regrade"`. The stored results are evidence and do not need
  re-running; the metric does. Nothing is written.
- **Comparing before and after** is `priorBestStillBest` read together with
  `collapsesIntoNoise`, and `verdictsFlipped` for the size of the move.
- **Restarting the loop from baseline** on a rank flip is `verdict: "revert"` on the chain, not a
  deletion — the tree is append-only and ids are never reused.

**`failureLayer` has a `"metric"` entry and is still not this bucket.** One asks which runtime
layer broke; the other asks whether the fault is the measurement itself. Sharing one enum would
make "the run crashed and scored zero" and "the metric marked a correct answer wrong" the same
value, and those call for opposite responses.

A failure that fits none of these is itself a signal: the approach may not be the bottleneck for
that slice. Offer to change target rather than forcing it into a bucket.

Tell the user how many of the remaining failures are **not** artifact gaps, and what each cluster
needs — then dispatch per bucket.

## The fifth bucket in detail

It is the only one this skill introduces, and it is the most under-served.

**The tell.** A correct submission scores badly, and reading the run confirms it was correct. Or
the metric and the stated objective ask for different things: the objective is ranking quality
while the metric is log-loss; the objective is balanced accuracy while the metric is raw accuracy
on an imbalanced set; the fold definition changed midway and the earlier verdicts were never
re-examined.

**A special form: something that will not move no matter what you add.** A feature, a loss term, a
post-processing step that is genuinely present and genuinely wired, and the score does not budge.
That is the signature of a metric that is not measuring the lever — check the mechanism before
concluding the approach is wrong, because the cheapest test in the whole checklist is to disable
the mechanism and confirm the score drops.

**Why it is worth its own bucket.** The other four are the tree's ordinary work. This one requires
admitting that the yardstick is wrong, which is the most expensive conclusion in a competition
search because it invalidates the work already done — and therefore the one most likely to be
avoided. Having a named bucket is what makes it cheap to say out loud.

**The action, in order:**

1. **A research node with `targets: ["ruler"]`.** The question is not "what else could I try" —
   it is whether the metric is measuring what the objective says. Nothing in the existing target
   set asks that question.
2. **Re-judge in place.** Re-run the metric over the stored outputs. The stored results are
   evidence and they do not need re-running; the metric does.
3. **Compare before and after.** How many nodes moved, and did the ranking change? This is
   `action="regrade"`.
4. **Read two outcomes, not one** — see below.

## Reading a regrade

`priorBestStillBest` and `collapsesIntoNoise` are different outcomes with **opposite** responses,
and reading either alone gives the wrong answer.

| Outcome | Reading | Do |
|---|---|---|
| Still best, lead intact | the earlier rounds hold | carry on |
| **Still best, lead collapsed into the floor** | the gains were probably real and the old calibration could not resolve them | keep the node, and size the next experiment against the floor rather than the last delta |
| **Ranking flipped** | those rounds were optimising something the metric could not distinguish | show the before/after table, and go back to the baseline rather than building on the old best |

The middle row is the one that gets misread. "The lead shrank" reads like the earlier work was
worthless; in fact it means the measurement improved and the work was fine.

The bottom row is the single most informative result this skill can produce, and it is why
upstream's own case study has an audit step that re-judges **in place** and compares, rather than
editing the stored scores.

`verdictsFlipped` counts nodes whose `confirmed` / `partial` / `refuted` changed. It is often
larger than the ranking change, and it is the honest measure of how much of the tree was resting on
a resolution the ruler did not have.

## Two patterns this surfaces

Both come from classifying, and neither is visible to a per-round analysis.

**The long tail.** "The single change that most often costs the score" is worst-bucket-first and
never reaches a tail of many small buckets, each worth one or two nodes. If classification shows a
dozen dimensions each contributing ≤2 failures with none of them covered, **one breadth pass**
covers more ground in a single round than the serial loop will in ten. This deliberately breaks
one-change-per-node: the dimensions are independent, the question is coverage rather than
attribution, and no single one would move the score enough to be measured on its own. Record them
as separate nodes so each is still attributable afterwards.

**Mid-run metric drift.** A metric definition that is subtly wrong for one feature, or a fold
definition that drifted, does not show up as an implausible jump. It shows up as a dimension that
will not move no matter what you add. **When one bucket has resisted three rounds of changes that
look correct, re-read the metric before writing the fourth** — and if you do change it, regrade
everything, quantify the shift, and decide with the user whether the existing rounds still stand.

## `failureLayer` and this bucket are not the same thing

They must not be merged. `failureLayer` asks **which runtime layer broke** — the tool call, the
output contract, the state between runs. The ruler bucket asks **whether the fault is the
measurement**.

Two orthogonal question spaces in one enum would make "the tool call failed and scored zero" and
"the metric marked a correct answer wrong" the same value, and those call for opposite responses:
one is a pipeline repair, the other invalidates the search.

## Reporting

State the bucket counts before the actions: *N of the remaining M failures are not artifact gaps.*
Then what each cluster needs. Do not present this as a verdict on the approach — the person who
built the pipeline has context you lack, and the purpose is to surface what is worth a second
look.

An audit that finds nothing wrong is a valid result. Do not manufacture a stall to classify.
