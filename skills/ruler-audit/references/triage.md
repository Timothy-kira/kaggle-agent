# Stall triage: five buckets

Adapted from Anthropic's `eval-hillclimb.md` Step 4.5 (Apache-2.0). The five buckets are
upstream's. Four of them are **not new mechanisms here** — this plugin already implements them
under different names, and the table's value is that it puts them side by side so "should I change
the code or the yardstick" becomes a lookup. The fifth is the one with no home, and it is the one
worth stopping for.

## When to do this

The tell is **two or three consecutive nodes whose delta did not clear the floor**, despite changes
that should have helped.

Do it earlier than patience demands: the moment no single change can plausibly exceed
`ruler.noise`, not only once the tree has gone flat. Rounds spent grinding past that point are
rounds spent learning nothing.

Read every remaining failure and bucket it by root cause. Then dispatch **per bucket** rather than
running another round against all of them.

## The table

| Bucket | Tell | Where this plugin handles it | Do |
|---|---|---|---|
| **Artifact gap** | the approach never had the fact it needed; the log shows it guessing or reaching for a default | the tree's home case | keep going — this is the loop working |
| **Harness / infra** | it broke before producing a scoreable output: quota, OOM, timeout, a cell that died. Some harnesses *score* the failure instead of erroring it — a zero whose log carries infra markers (retries exhausted, slot ceilings, empty output) belongs here too | `failureLayer`: `tool-recovery`, `output-contract`, `state-continuity` | fix the harness; exclude errored rows from the denominator until then, and decide the handling rule for scored-in zeros before comparing scores |
| **Structural** | the content exists in the artifact but the run never reaches it; or the same finding recurs across rounds; or one dimension underperforms regardless of which feature you target | `parent: null` swap | reorganise — consolidate, split, fix the route — rather than adding more unreached content |
| **Variance** | the same code flips between pass and fail by as much as the round-over-round delta | `partial` verdicts, `ablate`'s repeats | you are at the noise floor on this lever. Report the best arm; raise repetitions or change the target |
| **Judge / ruler disagreement** | a correct submission scores badly; or the metric and the objective ask for different things | **new: a research node with `targets: ["ruler"]`** | fix the metric, re-judge every node in place, then regrade |

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
