# Blog: Automating eval design and hillclimbing with Claude

**Author** Lance Martin · **Published** 2026-09-28 · **Section** Playbooks
**URL** `https://claude.dev/blog/automating-eval-design-and-hillclimbing/`

This is the principles layer the four `.md` files in this directory implement in detail. It is
quoted from throughout `where-hard.md`; the sections below are reproduced as fetched.

**Copyright** 2026 Anthropic, PBC. The blog is a published article, not a licensed source file, so
it is quoted rather than shipped: the passages that `where-hard.md` relies on are short and are
given there with attribution, and this file is the record of what was read on 2026-09-29. The
Apache-2.0 licence covering the four guides beside this one does not extend to the blog text.

---

## The four elements of a good eval

> Well designed evaluations have a few common elements (Figure 1):
>
> 1. **Eval tasks mirror production.** Sample tasks that you care about in "production," or the
>    setting in which the capability or application you are testing will be used. Sometimes tasks
>    are picked because they are easy to generate or they are easy to grade. But it's important to
>    ensure that the task distribution represents what you *actually* care about.
> 2. **Performance improves with stronger models and more thinking**. More capable models and
>    higher effort levels typically should perform better on an evaluation. If they don't,
>    ambiguous tasks or a miscalibrated grader often are hobbling performance.
> 3. **There is "passable" headroom at the frontier**. The most capable model at the highest effort
>    should be well below 100% on the evaluation, otherwise you can't reliably judge how changes
>    impact performance. Importantly, the gap should not be explained by impossible or ambiguous
>    tasks: a common tell is that a task fails every evaluation run, regardless of the number of
>    replicates. A good task is one where two domain experts would reach the same verdict and
>    everything the grader checks is stated in the task.
> 4. **Low run-to-run variance**. High variance is often due to poorly designed, ambiguous tasks
>    or a grader that produces different verdicts on identical output. Variance can also hide in
>    the configuration. For example, effort may not be applied consistently. Also, the environment
>    can affect the results of the evaluation: leftover state from an earlier trial (a file, a git
>    history) can hand the agent the answer.

## Adversarial sampling

> Model capability is jagged. If you pick cases because today's model fails them, you are sampling
> the valleys of one model's capability surface (Figure 2). The evaluation can end up measuring
> that model's failure fingerprint rather than what is intrinsically hard or valuable for your
> application to do.
> Pick hard cases because a human judged them hard: a useful test is to be able to say why a task
> is hard before you include it. Include cases that are specific failures in your application
> derived from production traffic, bug reports, or tickets. However, don't blindly trust user
> traffic: users sometimes try what they expect to work, so a task distribution drawn strictly
> from user traffic may skew easy.

## Designing examples — the input-source order

> 1. Production transcripts, after asking about retention and sensitive data.
> 2. Bug reports and support tickets.
> 3. Five to ten cases you write by hand.
> 4. Cases synthesized from your codebase.

## Diagnostic checks

> During the baseline runs mentioned above, Claude checks a number of things:
>
> - **Grader**: Claude runs the grader twice on the same output, and reports whether the verdict
>   changed.
> - **Plumbing**: Claude checks for timeouts, API errors, and cut-off answers to ensure
>   infrastructure noise doesn't pass as model variance.
> - **Headroom**: if the baseline already scores about 95% or higher, the skill warns the user and
>   alerts that the hillclimb should aim to explore cost or latency rather than quality.

## Where to hillclimb

> - **Cheap iteration** - It should be inexpensive (in terms of time, cost, and effort) to modify
>   whatever surface you are focused on for hillclimbing. Many internal efforts and customers have
>   focused hillclimbing on text, such as prompts and skills. These are easy to change and revert.
>   In contrast, open-ended modifications to an agent harness during hillclimbing may involve
>   extensive code changes.
> - **Attributable** - Changes in the score on your evaluation should be attributable to the
>   surface you are modifying during hillclimbing. For example, several successful applications of
>   hillclimbing have focused on skill triggering. The evaluation metric (the trigger rate for the
>   skill) is directly coupled to the skill description that is being modified.
> - **Well-scoped objective** - One common failure mode is an open-ended request to improve
>   performance without careful consideration of the headroom available in the evaluation; an
>   evaluation that's near saturation or a poorly scoped surface (e.g., an open-ended request to
>   update the harness) is more likely to stall. One generally strong objective across various
>   efforts is cost: even if an evaluation is saturated, you can ask Claude to find ways to reduce
>   cost while keeping performance at parity.

## Harness overfitting

> Even a well-designed evaluation rarely matches the exact task distribution you care about in
> production. As a result, "overfitting" to an evaluation is a common problem and results in a
> system that performs better on an evaluation than on production traffic.
>
> There are many ways an evaluation can "leak" into your harness (the code around the model,
> including prompts, tools, and loop that calls Claude). For example, consider an evaluation task
> that benefits from OCR, but OCR is rarely beneficial in your production tasks. The evaluation
> harness might add an OCR tool to your application, which improves on the benchmark without any
> impact on production. More broadly, hillclimbing may add features to that harness that address
> edge cases in the particular evaluation examples you've chosen. These harness additions improve
> your evaluation score, but don't translate to improvements in production (Figure 5).

Three things can help address this:

> - **Split the cases**. Use a train set that the hillclimber may read and a test set that is never
>   seen. If the train set scores improve while the test set scores stay flat, then that is a
>   common overfitting warning sign.
> - **Never paste failures into the prompt**. If the hillclimber reads the failing transcripts, it
>   should never paste the failure content into the prompt.
> - **Keep the answers structurally out of the model's reach**. Models can sometimes "reward
>   hack" by directly finding answers to evaluations.

## The loop

> Before the first round, Claude checks that the eval's noise (how far the score can move by chance
> alone) is smaller than the smallest improvement you'd act on; if it isn't, it says so and
> suggests more repetitions or cases.
>
> Each round, Claude reads the previous round's train transcripts and proposes one change as a
> patch. It aims each round at a change whose effect can show above the eval's noise: it fixes the
> failing behavior at its root (e.g., rewrites the section that causes it or adds a missing rule)
> rather than rewording a line. It then runs evaluation with the patched change. At this point,
> Claude applies a check: if the `train` set improves but the `test` set is flat, Claude suspects
> overfitting and reverts the patch. If there is a regression, Claude reverts. If train and test
> sets improve, it keeps the patch (Figure 6).
>
> When the score stalls for two or three rounds, Claude reads each remaining train failure and
> sorts it by cause. It does the same early if no single fix could gain more than the eval's noise,
> and suggests more repetitions or cases, rather than spending rounds on changes too small to
> measure. This step can catch ambiguous evaluation cases, harness errors, or run-to-run variance.
>
> Only legitimate failures are included in more hillclimbing rounds.
>
> When hillclimbing completes, Claude leaves your code at the version that did best on the test set
> for your goal. It reports the test result against the baseline with confidence intervals (Figure
> 7). If the gain is within noise, it says so and recommends against merging.

## The two examples

**Cost reduction** — an internal customer support benchmark, 44 tickets, 30 for search and 14 held
out. Started on Opus 4.8 at default (high) effort at **74.4%** decision accuracy and 4.6 cents per
ticket. Auditing the prompt cleared 87.8% on Opus 5.5 at low effort for 1.9 cents; Sonnet 5 at low
effort scored 88.9% at 1 cent; routing rules and a refund-cap cross-reference brought it to 98.9%
at about the same cost. On the 14 held-out tickets the final configuration scored **90.5%** against
the original setup's 78.6%, at about one fifth of the cost.

> Note: secondary sources report the baseline as 98.9%. The article says 74.4%, and 98.9% is the
> *final* configuration's training accuracy. The baseline is 74.4%.

**Performance improvement** — the `claude-api` skill against an eval derived from Anthropic's own
documentation. Started at 66%; adding sections for eight missing features reached 74%; fixing
errors in the C# and Java type tables reached 77%.

> After the score stalled for two rounds, Claude analyzed the remaining failures and bucketed them
> by root-cause. A normal round makes one edit for the most common failure. This step makes no
> edit; it only sorts every remaining failure by cause.
>
> - Reflecting across a collection of failures, the hillclimber found that the skill content was
>   present but Claude was simply writing older API shapes (e.g., from its trained priors). To
>   address, the hillclimber added a table near the top of the skill that guided Claude from the
>   forms it remembered to the current ones... This improved performance to 80%.
> - Tasks that never improved in performance despite addressing obvious content gaps are tells
>   that the example or grader is flawed. One task asked for code that catches one error type,
>   while its grader wanted a chain of at least three. Claude reworded the task. Another grader's
>   instructions contradicted our docs, and testing the real API showed the docs were right.
>   Addressing these, along with more skill edits, brought performance to ~88%.

The second bullet is the origin of the judge-disagreement bucket in `../triage.md`: a stalled
dimension with content already present is a statement about the measurement, and the two bugs found
were both in the eval rather than in the artifact.
