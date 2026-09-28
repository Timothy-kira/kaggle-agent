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

## Cross-references

- `rsi-experiment-tree` — the tree these numbers come from, and the read-gate behind them
- `scientific-plotting` — the six chart types and when each answers a question a table cannot
- `evidence-sources` — how a source gets its quote, and why a link without one is not a citation
- `handoff` — the other document derived from the same tree, for continuity rather than publication
- `github-auth` — getting code onto a repository from a machine that cannot authenticate
