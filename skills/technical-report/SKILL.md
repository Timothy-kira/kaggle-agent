---
name: "technical-report"
description: "Use when a competition run is over and the user is satisfied with the result and confirms finishing, OR when the user proactively asks for a technical report, a writeup, a paper, a summary of the work, or a release. Turns the RSI experiment tree into a paper-shaped document: the kept chain and what it cost, what was refuted and at which layer, the literature the tree already cites with the sentence that supports each claim, and the figures the data actually supports. Also assembles the release - code, weights, artifacts - and publishes it to GitHub or, when the bundle is too large for a git host, to a Kaggle dataset, with both links in the report."
---

# The technical report

A report is the one document whose failure mode is a confident sentence nobody can check. The
whole discipline here is that **every sentence traces to a row the tree already holds**, and the
things that cannot be traced are enumerated before anyone writes a sentence rather than quietly
left out.

## When to write one

- The user confirms they are finished **and** is satisfied with the result. Both, not either:
  "stop here" while the last run is still failing is not a report, it is a post-mortem, and the
  user should be told which one they are asking for.
- Or the user asks for it directly — "write this up", "技术报告", "paper", "release". That is a
  request; do it.

## Step 1: get the ledger

```
kaggle_experiment_tree action="report" competition="<slug>"
```

This assembles the record the report is written FROM. It is not the report, and it is assembled
the same way every time so two reports of one run cannot disagree:

| Field | What it is |
|---|---|
| `kept` | the chain that produced the result, in order, with each delta and the reason it was kept |
| `refuted` + `failuresByLayer` | what was tried and did not work, and **which layer broke** |
| `bibliography` | the sources this tree already cites, each with the sentence that supports the claim |
| `figures` / `couldNotDraw` | the figures the data supports, and the ones it does not |
| `artifacts` | the files, code and weights a release would carry |
| `quotaHours` | what the kept chain cost |
| `anchor` | the held-out set, and whether it was declared |
| `mayNotClaim` | **the sentences this report must not contain** |

**If the tree has structural problems, the report is refused.** Fix the tree first. A report about
a tree the validator distrusts is a report about a fiction.

## Step 2: the figures must be complete, or the gap must be stated

`analyze` draws what the data supports and **reports what it could not draw** — a node with no
recorded samples cannot get a noise band, a node with no recorded cost cannot go on the frontier.

Run it, then use every figure it produced. For each one it could not draw, either fix the
underlying gap (record the samples, record the cost) or **say so in the report**. A figure silently
missing reads as "nothing there to see", which is the specific failure this plugin exists to
prevent.

## Step 3: write it like a paper

```
Title            what the method achieves, not what the project was
Abstract         problem, approach, result, cost. One paragraph, numbers included.
1. Introduction  the problem, and why the obvious approach does not work
2. Method        the kept chain, as a method - what each kept node contributes
3. Experiments   setup, held-out anchor, metric, repetitions; the noise band, not a bare delta
4. Results       figures, with the kept chain's numbers
5. Ablations     the refuted list, BY FAILURE LAYER - this section is the contribution
6. Limitations   mayNotClaim, verbatim, plus every figure analyze could not draw
7. Conclusion    what it does, what it does not, what to try next
References       the bibliography, with the sentence each source carries
```

**Section 5 is why the tree exists.** A report that only lists wins is marketing. "60% of failures
were output-contract violations, not a weak model" is a finding, and the failure layers are
recorded precisely so it can be stated.

## Step 4: the release

Two links, both in the report, and a report without them is a description rather than a release.

**Code → GitHub.** `github_auth action="status"` reports the transport without a network call; the
`github-auth` skill covers the device flow when this machine cannot authenticate. Push the code
repository and put its URL in the report.

**Weights / data / artifacts → Kaggle dataset.** A release bundle is routinely too large for a git
host, and then "commit it to GitHub" is the wrong answer rather than the only one:

```
kaggle_datasets_publish folder="<dir>" title="<title>" slug="<owner>/<name>" private=true
```

It reports the file count, the total size and the largest files **before** anything is written, and
returns `https://www.kaggle.com/datasets/<slug>`. Publish privately first, make it public when
the user says so — this is an outward, hard-to-reverse action, so it waits for a yes.

## Step 5: state the limits in the report itself

`mayNotClaim` is not a note to yourself. Every entry goes into section 6, in the author's own
words. That includes:

- a node with no source and no `evidence="local-only"` — nobody can check it
- a source link with no sentence behind it
- a run that was declared and never settled — there is no result to report
- a held-out set that was never declared — nothing here was checked against untuned-on data
- a figure `analyze` could not draw

A report that admits what it cannot support is worth more than one that does not, and this is the
only place in the plugin where saying "we do not know" is the deliverable.

## Step 6: audit the finished prose — and be precise about which half ran

```
kaggle_experiment_tree action="audit-report" competition="<slug>" path="<report>.md"
```

This has two halves, and they are different in kind. Knowing which one you are looking at is
the whole point.

**The mechanical pass refuses, and a refusal needs no second opinion.** It catches three things
that are not judgement calls: a sentence from `mayNotClaim` copied into the prose verbatim, an
artifact the tree claims that is not on disk, and a number the report pins to a node that the
node does not hold. Route those to a model and you would only be turning a certain answer into
an uncertain one.

**The mechanical pass does not acquit.** A number that matches the tree proves the evidence
**exists**. It does not prove the sentence around it is *supported*. `n3: 0.61` matching n3's
recorded result is a fact about the tree; whether n3's run supports the claim written next to it
is a judgement, and the only version of that judgement worth having comes from something that
did not write the report.

So when the answer says a reviewer is needed, give the reviewer the two **paths** — the report
and `tree.json` — and nothing else. Dispatch a `verifier` subagent with those paths and the
question "does the evidence in the tree support each claim in this report, and does any
sentence overstate what the tree records?" Do not paste the audit's own summary in place of
them. A reviewer handed a summary is reviewing the summary, and `reviewerPacket` exists so you
never have to.

This is the same rule the rest of the plugin is built on: a deterministic gate may **drive** a
decision and it may refuse, but it may never **acquit** a claim. The check decides whether a
write happens; the reviewer decides whether the result is true.

## Two vendored bodies, and what each is actually for

Both are shipped whole under `references/kdense/`, with the upstream licence beside them.

**`scientific-writing/` — the claim/evidence id scheme.** Steps 3 to 5 above are prose discipline:
"every sentence traces to a row". Upstream makes that mechanical by numbering every claim
(`C001`) and every piece of evidence (`E001`), and a sentence that cites neither is a sentence the
linter flags. It is worth taking for a report this size, and it costs two columns. Two of its rules
are stricter than anything above, and both are adopted verbatim:

- **A source may not be marked verified until a human has opened it.** The same rule
  `evidence-sources` enforces with an extracted quote, arriving from the other direction.
- **Only an accountable human sets `submission_ready`.** In this package's idiom that is
  `ask_user`, at the same tier-3 point `genui-scenarios` puts every other mid-task prompt.

Its eight offline linters are vendored. They are not wired to a step here, because this package's
own `action="audit-report"` already refuses the three failures that matter mechanically and a
second gate that covers the same ground reads as two gates rather than as one.

**`scientific-slides/` — a talk, which is a different document.** Upstream's *default* path renders
each slide as one picture and hands it to a paid image model, then asks the model to look at the
previous slide so the next one matches its style. Take the structure and take the other two paths
upstream also ships; the picture path is the one to leave.

- **Do not render a slide as an image.** The slide's text becomes pixels: unselectable, unsearchable,
  and wrong whenever the image model mis-renders a symbol or a number. In a talk the text *is* the
  argument, and a research talk is exactly where a garbled confidence interval hurts most.
  Upstream's own PowerPoint route says as much — generated visuals, *separate text*.
- **Build the deck as a document.** Two real formats ship with the vendored body and neither needs
  an API: the Beamer templates in its `assets/` (conference, seminar, defence) with
  `references/beamer_guide.md`, or a PPTX through the `pptx` skill. Style consistency is then
  structural rather than something a model is asked to copy from its own previous output.
- **The figures go in as themselves.** `scientific-plotting` already produced them as real files at
  the export resolution it inspected. A slide embeds those files; it does not ask anything to
  redraw them.
- **An image model still has a place — on the schematic, not on the slide.** A method diagram or
  an illustrative graphic is what text-to-image is actually good at. The deliverable of that step
  is a **prompt, handed to the user**, not a call this package makes: one prompt per slide that
  needs art, written so it can be pasted straight into whatever tool the user already pays for —
  GPT, Gemini, or the upstream generator. They run it, the image comes back, and the deck is
  assembled around it.
- **Keep the numbers out of the prompt, every time.** Say "no text, no numerals, no labels" in
  the prompt itself. A rendered citation or a rendered confidence interval is a wrong number on a
  slide, and it is wrong in a way nobody catches until the talk. The text, the figures and the
  numbers are typeset by this package, from the tree, after the picture is in.
- **Set the author.** Upstream defaults it to "K-Dense" unless told otherwise, which would stamp
  somebody else's name on your talk. Set it, and check it before you present.
- **Timing survives all of it.** Results get 40-50% of the time, and three to five timed rehearsals
  come before finalising. `assets/timing_guidelines.md` and `references/talk_types_guide.md` cover
  the per-talk-type splits.
- **Its accessibility line conflicts with this package's.** Upstream treats "high contrast 7:1" as
  a presentation preference. `scientific-plotting` forbids claiming that a palette makes a figure
  accessible at all, and the stricter rule wins where they meet.

## Cross-references

- `rsi-experiment-tree` — the tree these numbers come from, and the read-gate behind them
- `scientific-plotting` — the six chart types and when each answers a question a table cannot
- `evidence-sources` — how a source gets its quote, and why a link without one is not a citation
- `handoff` — the other document derived from the same tree, for continuity rather than publication
- `github-auth` — getting code onto a repository from a machine that cannot authenticate
