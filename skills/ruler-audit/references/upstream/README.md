# Upstream sources

Two kinds of source, and the difference matters for what this directory can claim.

## The four guides — byte-for-byte copies

These are **not adapted, not abridged, and not re-worded**. Nothing in them has been changed.

| File | Bytes | Upstream path |
|---|---|---|
| `build-eval.md` | 54,819 | `skills/claude-api/shared/evals/build-eval.md` |
| `eval-hillclimb.md` | 68,773 | `skills/claude-api/shared/evals/eval-hillclimb.md` |
| `eval-audit.md` | 27,892 | `skills/claude-api/shared/evals/eval-audit.md` |
| `cost-hillclimb.md` | 31,574 | `skills/claude-api/shared/evals/cost-hillclimb.md` |

- **Source** `https://github.com/anthropics/skills` — `skills/claude-api/shared/evals/`
- **Licence** Apache License 2.0, as published at `skills/claude-api/LICENSE.txt` in that
  repository. Section 4(d) requires that recipients receive a copy of the licence; it is at
  `https://www.apache.org/licenses/LICENSE-2.0` and the attribution for this package is in
  `../../NOTICE.md`.
- **Copyright** 2026 Anthropic, PBC
- **Retrieved** 2026-09-29, from `main`

## The blog — quoted sections, not a copy

`blog-automating-eval-design-and-hillclimbing.md` holds the passages this package relies on from
Lance Martin's article of 2026-09-28, reproduced in full where they are quoted. It is **not** a
byte-for-byte copy: it is a record of the sections that were read and are cited, and the rest of
the article is not reproduced.

The Apache-2.0 licence above covers the four guides. It does not extend to the published article,
so the blog text is quoted with attribution rather than redistributed as a file — which is why
that file names its own status in its header.

## Why the originals are here at all

The reasoning in these guides does not depend on the thing being evaluated. The noise-floor
arithmetic, the build-variance finding, the five stall buckets, the three adoption gates and the
jagged-capability argument are all about measurement, and they transfer to a competition leaderboard
unchanged.

What does not transfer is the vocabulary: `results.jsonl`, `traces/`, `runner-scaffold.mjs`,
`AskUserQuestion`, transcripts, memory stores and retrieval indexes belong to an application being
evaluated by an LLM. None of those exist in this package.

So the original stays readable on its own terms, and the mapping into this package's vocabulary
lives beside it in `../`:

| Reading | For |
|---|---|
| `../resolution.md` | which of the upstream mechanisms are already enforced by the tree, and which are judgement |
| `../triage.md` | the five buckets quoted verbatim, and which four are already implemented here under other names |
| `../checklist.md` | the five audit groups, with the domain-bound checks removed and the rest stated as-is |
| `../where-hard.md` | adversarial sampling and the leaderboard as a fitted ruler |
| `../adoption.md` | the three gates, quoted, with the mechanism gate intact |

## One bucket is ours

Of the five stall buckets, four already exist in this plugin under different names — they are not
adapted from upstream, they predate it:

| Upstream bucket | Already here as | Where |
|---|---|---|
| Harness / infra | `failureLayer` | `output-contract`, `tool-recovery`, `artifact-persistence`, `state-continuity` |
| Structural | the `parent: null` swap | a new direction replaces an exhausted one |
| Variance | `partial` verdicts + `ablate`'s repeats | the floor is a measured spread, not a hope |
| Artifact gap | the tree's own home case | — |

**Judge disagreement is the only new one.** It has no home in the vocabulary above, which is why
`RESEARCH_TARGETS` gained `"ruler"` — a research node that goes back to the measurement rather than
to a forum or a paper. `failureLayer` already has a `"metric"` entry, and the two are deliberately
**not** merged: `failureLayer` asks which runtime layer broke, while the judge bucket asks whether
the fault is the measurement itself. Merging them would make "the run crashed and scored zero" and
"the metric marked a correct answer wrong" the same value, and those call for opposite responses.

See `../triage.md` for the full table.
