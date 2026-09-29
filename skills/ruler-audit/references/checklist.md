# Audit checklist: five groups

Adapted from Anthropic's `eval-audit.md` (Apache-2.0). The five groups are unchanged; the case
vocabulary is competition vocabulary, and every check that named a Claude-specific mechanism has
been re-expressed as the competition equivalent.

Run this once before the first declaration of a run and again when the measurement surface
changes. The tree enforces arithmetic (see `SKILL.md`); this is the part where a wrong answer
looks like a right one.

**Before auditing, read the tree, not the notebook's prose.** A surprising share of metric-quality
arguments turn out to be about a pipeline that no longer runs. `action="read"` plus the
`ruler` block is the starting state; `action="board"` shows which configurations were actually run
twice, which is where a real repeat set may already exist.

## 1. Task design

Are the cases right? In a competition the "case" is a data distribution, a validation split, or a
slice of the public leaderboard — and the same questions apply with different nouns.

- **Unambiguous success criteria.** Does the metric name one quantity, computed one way? "Better
  CV" is a wish; `5-fold stratified CV, macro-F1, seeds fixed` is a metric. Note the dual failure:
  under-specified (fold count, seed handling, preprocessing scope all unstated) and over-specified
  (a validation protocol so pinned it measures the pinning).

- **A reference exists and passes.** Does every configuration recorded in the tree have a result
  that could plausibly have been produced by a working pipeline? A run at exactly the base value
  across every fold is more often a broken fold or a leaked prediction file than a flat baseline.

- **Ground truth is correct, and you know where it came from.** Record the provenance: leaderboard
  scores, a public notebook, a local fit, or a self-derived proxy. **A proxy derived from a public
  notebook's output makes imitating that notebook the optimal strategy**, which is exactly the
  wrong thing to reward when the goal is a genuinely better solution. Tag the provenance on the
  node.

- **No annotation artifacts.** Could a trivial baseline score well from surface patterns — row
  count, column presence, submission formatting — without solving the task? A constant prediction
  scoring well on a metric with a large majority class means the metric, not the model, is doing
  the work. Report the majority-class baseline next to every model score.

- **Leakage in what the pipeline can see.** Does the answer reach the model through a path you did
  not close — a cached prediction file from a previous round, a feature store keyed on the target,
  target statistics computed over the full set before the fold split, a `fit` on all folds followed
  by a per-fold score? This is the competition form of the upstream "answer appears in the
  prompt" check, and it is the most common cause of a CV that disagrees with the leaderboard.

- **Answerable from the data alone.** If the target is derived from a column present at training
  time by any route the pipeline can reach, the score measures that route. Ask what the weakest
  conceivable pipeline would get.

- **Difficulty comes from the problem.** Are the hard cases hard because the prediction is hard, or
  because a preprocessing quirk punishes one implementation? When a direction wins only on a
  specific metric variant, that is a signal about the metric.

- **Symptom, not investigation.** When analysing why a node lost, how much of the cause is handed
  over? If the diagnosis already names the offending feature, the next round has learned nothing.
  Give the analysis the symptom and let it fetch the rest.

- **Realistic distribution and interaction shape.** Does the validation split match the test
  distribution in the ways that matter — group structure, temporal ordering, label frequency, the
  gap between feature availability at train and test time? A single random split across a
  time-ordered or group-structured dataset is the standard way to produce a CV that does not
  predict the leaderboard. Name any obvious divergence before reading the first delta.

- **Difficulty headroom.** Look at the spread of the recorded results. If the base is already
  within a couple of points of the top of the board, the remaining variance is format and luck
  rather than capability, and a hillclimb there mostly buys nothing. If everything sits near zero,
  suspect the pipeline before suspecting the task.

- **Saturated metrics and what they end up measuring.** Near the ceiling, remaining variance is
  dominated by tie-breaking and luck. The last fraction of a percent may no longer measure what
  the run set out to measure.

- **Class balance.** For any AUC/PR or thresholded metric, report the class distribution and the
  majority-class baseline. When positives and negatives both exist, prefer precision / recall /
  specificity over accuracy alone.

- **Both directions.** A validation set that only contains rows where the answer is easy to get
  wrong, or only rows where the base fails, is a set the tree will overfit to. Check the base's
  per-slice results for a slice where it is already near-perfect — that slice contributes no
  signal and dilutes the rest.

- **One factor per case when diagnosis matters.** A node that changes features, preprocessing and
  loss at once shows a delta whenever any one breaks. Fine for a headline, useless for asking
  which one. `ablation-design` is the mechanism for keeping these apart.

- **Inverted results as a smoke test.** Where a strictly worse configuration outscores a strictly
  better one, look closely. Far more often it is a split artefact or a cached artifact than a real
  inversion.

- **Staleness.** If the metric depends on external state — a leaderboard that has been updated
  since the run, a public dataset version, a library default that changed — when was it last
  verified? A currently-correct result marked wrong against a stale key is a silent zero.

- **For generated features: fix the generator.** When features come from a pipeline, a problem in
  the output is a symptom of something upstream. Patching individual features leaves siblings
  carrying the same defect.

### Auditing at scale

The code you can read end to end; the case set may be thousands of rows you cannot. Three tiers:

**Tier 1 — programmatic over the full set.** One pass reporting: duplicate rate, label balance,
feature-length distributions, NaN and inf rates, train/test distribution drift, per-fold result
spread. Cheap, exhaustive, catches skew and drift regardless of set size.

**Tier 2 — stratified sample for a close read.** Draw twenty to fifty rows, stratified across the
group or class label, and apply the checks above. Recommend the user read a handful themselves;
a second pair of eyes on raw rows catches what no checklist does.

**Tier 3 — per-row audit.** For large sets, a sampled audit is usually enough. If a full sweep is
wanted, say what it costs first — roughly N cheap calls — and let the user decide.

## 2. Harness design

Is the scaffolding right? The central failure is **conflation**: any time a non-model artefact — a
crashed cell, an OOM, a truncated write, a stale cache read — lands in the same column as a genuine
result, the tree attributes to the approach something that belongs to the plumbing.

- **Infra failures distinguished from real failures.** How does the run treat a timeout, an OOM, a
  cell that died, a truncated CSV, a corrupted cache? If those land as a score of 0 and mix in
  with real results, the headline is contaminated. **A crash is not a bad score**, and this is
  where the tree's `failureLayer` earns its place: `tool-recovery`, `output-contract` and
  `state-continuity` classify the run; the variance bucket does not.

- **"No answer" is not "wrong answer."** Does the pipeline distinguish a model asserting a
  negative from the pipeline failing to produce a file? If a missing submission and a wrong
  submission both score 0, a run that errors on every input scores identically to one that
  carefully got everything wrong.

- **Clean state per run.** Does each run start from a fresh environment — no cached features, no
  previous round's predictions, no leftover git state, no warm data loader carrying over? Shared
  state lets one round read hints from another and makes results order-dependent. This is the
  upstream "leftover state hands the model the answer" check, and in a competition it is the
  single most common source of a CV that improves for no reason.

- **Environment complete and functional.** Are the accelerators, the paths, the dependencies
  actually there? A node that fails for every arm because a path was wrong measures the
  environment.

- **Deterministic setup.** Unseeded randomness, unordered iteration that reaches the output,
  timestamp-dependent paths, non-deterministic GPU reductions — all add run-to-run variance
  unrelated to the system under test. Pin seeds; sort anything whose order matters.

- **Scaffold limits versus approach limits.** A tight time budget, an early-give-up retry policy,
  or a truncated context all look like capability gaps from outside. Where practical, vary the
  scaffold holding the approach fixed.

- **Limits will not clip any run.** Compare the longest input and the longest plausible output
  against the limits. Truncation is easy to misread as the run choosing to stop.

- **Transient errors retried with jittered backoff, and retries recorded.** Unretried rate limits
  show up as spurious failures and can make one arm or one hour look worse; a zero-delay retry
  loop is worse, because it burns quota invisibly. Back off with jitter, cap attempts, record the
  count.

- **A hard wall-clock ceiling per run, independent of stream liveness.** Only a ceiling on total
  time reclaims a slot. On Kaggle the equivalent is a timeout that produces a failed run rather
  than a partial one scored as a loss.

- **The run that produced the result is the run that was declared.** Confirm the recorded result
  belongs to the declared arm — a stale kernel, a re-pushed folder that resumed an old run, a
  notebook still reading last round's output file. **A result from the wrong run measures
  nothing**, and silent substitution surfaces nowhere else.

- **Eval config matches the submission config.** Diff the inference-time configuration against
  what actually ships: fold count, ensembling, TTA, threshold, preprocessing scope. A CV computed
  with a different configuration than the submission is a different metric.

- **Full per-node provenance saved.** Every node's code, config, log and output, so a surprising
  result can be traced without re-running. The tree stores the verdict and its evidence; the
  artifacts belong beside it.

- **Multiple trials with variance reported.** A single run is a point estimate with no error bar.
  Differences smaller than the run-to-run spread are not meaningful. This is what
  `factorsIntent: "repeat"` is for.

- **Reproducible over time.** Versions pinned, fold definition and metric definition recorded
  together. **Scores from before and after a metric change are not comparable** — which is what
  `action="regrade"` exists to make visible.

- **Harness tested on known-good and known-bad.** Before the first full pass, run (a) an oracle —
  the known-best configuration, which should reproduce its score — and (b) a null baseline — a
  constant prediction, the majority class, or a deliberately broken pipeline — through the whole
  path. If the oracle does not reproduce, the pipeline is broken; if the null does not fail, the
  metric is too lenient. Two runs, minutes, and it catches most wiring bugs before they cost a
  full pass.

## 3. Metrics hygiene

Does the metric reflect the solution rather than the rig around it?

- **Accounted from what was actually run, not estimated.** Quota, runtime and memory from the
  run's own record. String-length or wall-clock estimates are off by enough to reverse a cost
  comparison.

- **Cost derived from recorded usage and the arm's actual accelerator.** Never a flat assumed
  rate, and a grader's own cost recorded separately so it neither hides nor damps differences
  between arms.

- **Cache state comparable across arms.** If one arm runs with a warm feature cache and another
  cold, cost and latency differences are partly an artefact of run order. Flag comparisons where
  the cache-read share differs materially.

- **Latency measured against the right boundaries.** Time the scored run; keep retries, queueing
  and upload out of the model-latency column and record total wall-clock separately. Otherwise
  whichever arm hit more transient errors looks slower.

- **Per-call breakdown for agentic pipelines.** Record time and quota per cell and per step, not
  just per run, or a slow step is indistinguishable from a slow approach.

- **Performance reported alongside quality**, per arm, as absolute numbers first, so
  quality-vs-cost and quality-vs-runtime trade-offs are visible rather than implied.

## 4. Grader design

Is the scoring right? Here "grader" is the metric plus the validation protocol, which is code.

- **Metric and objective agree.** Does the metric reward what the objective asks for? The
  competition form of the upstream drift: the objective is ranking quality, the metric is
  log-loss; the objective is balanced accuracy, the metric is raw accuracy on an imbalanced set.
  This penalises approaches that do the right thing.

- **Grades outcomes, not paths.** Does the metric reward the right answer or a particular route?
  An AUC that ignores calibration and a log-loss that does not will rank the same submission
  differently, and the tree will optimise whichever one was declared.

- **Not overly rigid.** Normalise before comparing: column order, dtypes, index column, float
  formatting, `4` vs `4.0`, thousands separators, NaN handling. Most leaderboard-plausible
  disagreements are formatting, and a fold difference of 0.0004 is usually a dtypes problem.

- **Not too lenient.** Write a deliberately wrong-but-plausible submission and confirm the metric
  punishes it. A metric that a constant prediction scores well on is not measuring the task.

- **Cheat-resistant.** How could an approach satisfy the metric without solving the task — hard-
  coding the expected output, reading a leaked file, special-casing on test identifiers, a
  degenerate policy that technically optimises the metric? Approaches under selection pressure
  find these. The competition form: a pipeline that reads the public LB distribution and optimises
  against it directly.

- **Ground truth not reachable by the pipeline.** Not in a file in the working directory, not in a
  checked-out repo, not in leftover state, not derived from target statistics computed before the
  split. **This has to be structural; "don't use it" in a comment is not a defence.** The tree
  already refuses nodes that score on the declared `anchor` — that refusal protects the held-out
  set, and it does not protect against leakage that happens *before* the split.

- **Spot-check the losses.** Read a handful of the cases the metric scored worst. If more than
  roughly one in ten look like metric errors, fix the metric before any full pass — otherwise you
  are partly measuring which approach matches the metric's blind spots. This is the competition
  form of the upstream finding that a re-grading shrank a claimed +9 to +3.

- **Deterministic, or with measured variance.** Run the metric twice on the same submission. If
  the result changes, there is grader variance on top of run variance; measure it and report it.

- **Atomic checks over one blended score.** Record independent properties separately — overall
  score, per-class score, calibration, fold spread — rather than one number. More reproducible,
  easier to diagnose, and it is what `ablate`'s per-factor table reads.

- **Aggregation matches the question.** A mean is right for typical-case quality; worst-case or
  fail-on-any reflects what matters better for a metric where one fold collapsing ruins the
  submission.

- **Partial credit and penalties do not make a degenerate policy optimal.** If abandoning a fold
  or dropping hard rows beats attempting them, "do nothing" wins and the tree will find it.

- **Handles the largest plausible output.** The metric must not truncate, time out, or crash on
  the biggest submission a run will produce. A crash is a pipeline failure, not a score.

## 5. Can it detect the change you are after?

A metric can be correct on every item above and still be useless for the decision at hand because
it lacks the resolution to see the effect. Check this **before** the first full pass and again
before the next round of experiments — discovering it after several paid runs is the expensive way.

- **Noise floor versus headroom versus the smallest change worth acting on.** This is stage 2 of
  `SKILL.md`, and `action="calibrate"` is where the first number comes from. See
  `references/resolution.md` for the two variance layers, which change which lever applies.

- **Search and held-out are the same population.** The split must be drawn at random or
  stratified, **never by baseline score**. A search slice hand-picked from the worst-scoring rows
  guarantees two things: the analysis only ever sees pathological rows, and selecting on low
  baseline scores buys regression to the mean — those rows "improve" on re-run by chance alone.
  The symptom is a healthy search gain with a flat held-out set. `calibrate`'s `sideCheck`
  reports the gap between the two sides at calibration time; a gap larger than the noise floor
  means the split is selecting for extremes. **Re-draw before the next run rather than after
  several have been read as wins.**

- **The mechanism is wired.** Whatever the score is supposed to depend on — a feature group, a
  loss term, a post-processing step — disable it and confirm the score drops; enable it and confirm
  it engages. If the score barely moves either way, the metric is not measuring the lever you
  plan to pull. This is the competition form of the upstream "turn it off and see the score
  fall" check, and it is the cheapest of the five.

- **The headline recomputes from raw rows.** Recompute the number you will report from the
  per-fold or per-run values yourself; do not trust an aggregate field. Mean-versus-sum and
  per-rep-versus-per-run mixups produce phantom breakthroughs. The tree enforces the same rule
  for samples (`result` must equal `samples.mean`), and `calibrate` applies it to the noise floor
  it is handed.

## 6. Reporting findings

The checks above are directives; the report is not one. Whoever built the pipeline has context
you lack — a deadline, a deliberate trade-off — and the purpose is to surface what is worth a
second look, not to grade the work.

- **Frame findings as observations and suggestions.** "Something worth looking at is…", "one thing
  that can cause trouble here is…" over "this is wrong." State what was observed, why it might
  matter, and one concrete change, then let the user decide.

- **Distinguish severity.** Lead with what makes the numbers actively misleading — crashes scored
  as failures, leakage, a split selected by score, a noise floor larger than the effect sought,
  results from the wrong run — then what adds noise without flipping conclusions.

- **Be specific and cite evidence.** The node id, the fold, the file, the run. "Node e7's score
  came from a run whose submission file was written by the previous cell" is actionable; "some
  runs may be stale" is not.

- **Say when things are fine.** An audit that finds nothing wrong is a valid result. Do not
  manufacture concerns.

- **Do not be exhaustive.** Report the handful that matters for the decision at hand; listing
  every deviation from an ideal buries them.

- **Offer to fix, not just flag.** Where a finding is a small change — pin a seed, renormalise
  before comparing, re-draw the split, recalibrate — make it or offer to.

Two practices worth suggesting regardless: treat the validation set as living (new failure modes
from the leaderboard become cases, saturated items are hardened, the metric is recalibrated when
it drifts), and periodically ask a strong model to read the metric definition and a few graded
submissions and say where a reasonable person would disagree.
