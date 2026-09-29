# Resolution: the three numbers, and the two layers

Adapted from Anthropic's `eval-hillclimb.md` Step 0.5 and `eval-audit.md` §5 (Apache-2.0). The
arithmetic is restated in competition terms and the second variance layer — which Kaggle adds and
upstream does not — is stated separately, because it changes which lever applies.

## The three numbers

Before the first run, and again before each round, put three numbers next to each other:

| | What it is | Where it lives |
|---|---|---|
| **noise floor** | how far this metric moves when you re-roll it with nothing changed | `ruler.noise` |
| **headroom** | ceiling minus current best — how much is reachable at all | `ruler.headroom` |
| **smallest actionable delta** | the smallest gain worth shipping, decided by you, not by the tree | `ruler.smallestActionable` |

**If the floor exceeds either of the other two, the experiment cannot be read.** No amount of
good work clears it. Say so before starting, with the numbers, not after five rounds.

Upstream's order-of-magnitude shortcut for a pass rate is `1/sqrt(n·R)`: 25 cases × 2 reps ≈ ±14
points, 100 × 2 ≈ ±7. A competition CV is a mean over folds rather than a pass rate, so the
shortcut does not transfer directly — **measure the fold-to-fold and run-to-run spread of a
configuration you have already run** instead of estimating it. `action="ablate"` reports the
noise floor implied by any configuration that appears twice in the tree, and that is the honest
input to `calibrate`.

## Why the arm's own repeats are not enough

`settle` has always judged a delta against what that arm's own repeats spread over. That measures
one thing: whether the metric is stable **for this configuration on these folds**.

It cannot measure whether the metric is stable at all. Repeating one configuration ten more times
on the same five folds does not make the folds finer, does not change the fold assignment, and does
not shrink the gap between the fifth-best and the sixth-best submission. **Resolution is a property
of the measurement surface, not of the arm.**

This is why the floor is the **largest of three terms** — what you declared (`atLeast`), what the
arm's own repeats spread over (`samples.std`), and the calibrated floor (`ruler.noise`) — and why
`floorFrom` names the terms that tied for it.

The distinction decides the next action, and the two remedies are opposites:

| `floorFrom` | Meaning | Remedy |
|---|---|---|
| `declared` | you promised more than the metric can see | the prediction was miscalibrated; re-declare against the floor |
| `arm` | this arm's repeats were wide | more repetitions on the same folds will sharpen it |
| `ruler` | the metric's own resolution binds | **more repetitions will not help** — change the measurement surface |
| combinations | both | they need different remedies, so they are named separately |

`floorFrom` lists only what **tied** for the floor. A well-calibrated ruler sitting below a noisy
arm's spread is not named, because it did not cause this downgrade — and naming it would send you
off to rebuild the metric when the arm was the noisy thing.

## The two variance layers

Upstream's hardest-won measurement is build variance, and it is worth being precise about because
it is the case that makes "add more repetitions" the wrong advice.

Some flows put a stochastic step between the lever and the score. The prompt you iterate on
**builds** something — a memory store, a retrieval index, a synthesised corpus — and the eval then
scores *reads* against the built thing. When the build runs once per variant and every repeat
reads the same build, the repeats and their confidence intervals measure only the noise of scoring
a fixed build. **The build's own run-to-run variance is sampled once per variant, invisible to
every gate, and often the larger term.**

Upstream's measured case: three builds of one unchanged prompt spanned about 7 points of train-mean
score, against rescore noise near ±1.4. Every edit had been compared against a single baseline
build, and the loop could not tell any of them from the default.

**The fix is not more repetitions.** It is to rebuild the baseline two or three times with nothing
changed, score each the same way, and take the spread across those no-change rebuilds as the floor
any single edit has to clear. If the build spread exceeds a plausible one-edit effect, build K
times per variant and compare build-pooled means, or move the lever closer to the score.

### In a competition

The build step is the pipeline. Rebuilt between repeats:

- feature caches and precomputed embeddings
- preprocessing folds and fold assignments
- augmented or resampled training data
- the previous round's prediction file, if a node scores against one

Held fixed by a repeat that only changes the seed: everything else.

The two layers are recorded separately, and `calibrate` **refuses** a calibration that gives only
one of them:

```json
{"ruler": {"seedSpread": 0.012, "rebuildSpread": 0.031, "noise": 0.031}}
```

The refusal is not pedantry. The two call for opposite responses:

| Wide layer | What it means | Remedy |
|---|---|---|
| `seedSpread` | the estimate is noisy, the method is fine | add repetitions — the estimate sharpens |
| `rebuildSpread` | the measured artifact is not stable | **take the rebuild out of the measured path** — more repetitions re-measure the same rebuild and add nothing |

Adding repetitions to a `rebuildSpread` problem measures the same rebuild again and produces a
number that looks sharper and is not.

`controls.rebuild` is the per-node control that separates them. It is a **separate key from
`seed`**, not a value of it: a repeat arm that rebuilt its feature cache is not measuring seed
noise, and recording it as such is how a rebuild figure gets mistaken for a seed figure.

## Getting the numbers

1. **Find arms that were already run twice.** `action="ablate"` reports configurations that appear
   twice and the noise floor implied by them. Calibrating from existing arms beats running a fresh
   sweep, because the runs already cost their quota.
2. **If no configuration appears twice, say so.** A tree with no repeats has no measured floor.
   `calibrate` requires `samples.n >= 3` with values that reproduce the stated mean precisely
   because two readings do not describe a spread, and a mean that disagrees with its own values is
   a transcription error rather than a measurement.
3. **Separate the layers before writing the figure.** If a `repeat` node varied the seed, that is
   `seedSpread`. To get `rebuildSpread` you need repeats that changed the pipeline, not the seed —
   which is what `factorsIntent: "repeat"` with `controls.rebuild: true` records.
4. **Write it into the tree.** `action="calibrate"`, not the conversation. The next session's first
   `declare` must read the same figure.
5. **Recalibrate when the surface moves, then regrade.** A new CV scheme invalidates the old floor.
   `calibrate` is meant to be overwritten — a stale floor starts refusing correct experiments,
   which is worse than having none — and `action="regrade"` then shows what the new floor did to
   the verdicts already recorded.

## Checking the split at the same time

`calibrate` returns a `sideCheck`, and it is worth reading. The anchor stops a node from scoring on
the held-out set; the side check asks whether the two sides are even comparable.

A split selected to look good buys regression to the mean: the rows were chosen for being extreme,
so they drift back toward the population on a re-run and the gain was never there. The symptom is a
healthy search gain with a flat held-out set.

`sideCheck` compares the two sides' means against the floor. Inside the floor: the split is not
selecting for extremes, carry on. Outside it: re-draw **before** the next run rather than after
several have been read as wins.

## Levers, in cost order

When the floor exceeds what you need:

1. **More repetitions** — cheapest, and correct only when `seedSpread` dominates.
2. **More folds or a different CV scheme** — correct when variance lives in the split. This
   invalidates the old calibration: recalibrate, then regrade.
3. **A finer-grained metric** — a saturated scalar cannot resolve anything near its ceiling.
4. **Iterate on a discriminative subset, confirm on the full set at the end** — buys iteration
   speed at the cost of a final check that must not be skipped.

Cases and repeats are two knobs on the same dial. Budget them together when the set is sized, not
after.
