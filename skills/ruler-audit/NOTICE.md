# NOTICE

## ruler-audit

This skill is adapted from Anthropic's public `claude-api` eval guides.

**Upstream work**

- **Copyright** 2026 Anthropic, PBC
- **Source** `https://github.com/anthropics/skills` — `skills/claude-api/shared/evals/`
- **Licence** Apache License, Version 2.0 (`https://www.apache.org/licenses/LICENSE-2.0`),
  as published in the upstream repository at `skills/claude-api/LICENSE.txt`
- **Files adapted**

  | Upstream | Here |
  |---|---|
  | `eval-audit.md` | `references/checklist.md` |
  | `eval-hillclimb.md` (Step 0.5, Step 4.5) | `references/resolution.md`, `references/triage.md` |
  | `build-eval.md` (Step 1) | `references/where-hard.md` |
  | `cost-hillclimb.md` (adoption gates) | `references/adoption.md` |
  | *Automating eval design and hillclimbing with Claude*, Lance Martin, 2026-09-28, `https://claude.dev/blog/automating-eval-design-and-hillclimbing/` | `references/where-hard.md`, `references/triage.md` |

**The four guides are also shipped whole, in `references/upstream/`.** Those copies are
byte-for-byte: not adapted, not abridged, not re-worded. The passages quoted in the mapping files
are quoted from them, and `tools/verify_upstream_quotes.py` asserts that mechanically — every
quoted block must be findable in an upstream source, character for character, and every shipped
copy must match the byte count recorded in `references/upstream/README.md`. A paraphrase under
quotation marks fails the build.

**Modifications.** This is a derivative work and has been modified. The changes:

- **Domain-bound material restated; domain-neutral material quoted.** The noise-floor arithmetic,
  the build-variance finding, the five stall buckets, the three adoption gates and the
  adversarial-sampling argument are quoted verbatim, because they are statements about measurement
  and hold whatever is being measured. What is restated is the vocabulary they are applied in:
  `results.jsonl`, `traces/`, `runner-scaffold.mjs`, `AskUserQuestion`, transcripts and memory
  stores belong to an application evaluated by an LLM, and none of them exist in this package, so
  the competition equivalents — the tree, `ask_user`, the fold structure, the feature cache — are
  named in their place. Every check that named a Claude-specific mechanism (`stop_reason`, tool-call
  traces, context windows) is re-expressed as its competition equivalent, and the Claude-specific
  mechanism itself is removed.
- **The tree is the single ledger.** Upstream's parallel file layout — `results.jsonl`, `traces/`,
  `_state.json`, `summary.json`, `vN/` directories, the `report/` builder and its HTML output — is
  not carried over. Judgements are recorded through `kaggle_experiment_tree` (`action="calibrate"`,
  `action="declare"`, `action="settle"`, `action="regrade"`, `action="anchor"`, `action="ablate"`)
  rather than in files beside it.
- **Two variance layers stated separately.** Upstream's build-variance measurement is quoted, then
  restated as seed variance and rebuild variance, with the competition-specific list of what a
  rebuild covers (feature caches, preprocessing folds, resampled data, the previous round's
  prediction file). The two layers are stored under separate keys because they call for opposite
  remedies.
- **Four stall buckets re-homed.** Upstream's artifact-gap, harness, structural and variance
  buckets already exist in this plugin under other names — `failureLayer`, the `parent: null` swap,
  `partial` verdicts and `ablate`'s repeats. The table points at those existing mechanisms rather
  than restating them, and only the judge-disagreement bucket is new. `failureLayer` and the ruler
  bucket are kept explicitly unmerged: one asks which runtime layer broke, the other whether the
  fault is the measurement.
- **Sign-off points mapped to `ask_user`.** Upstream's `AskUserQuestion` pauses are the same
  gesture; they are expressed as text questionnaires in this plugin's idiom, following the
  `genui-scenarios` rule that a mid-task prompt is a sentence to answer rather than a panel.
- **Arithmetic delegated to the tree.** The three-number resolution check, the verdict floor and
  the re-grading comparison are enforced in `mcp/experiment_tree.py` rather than restated as prose,
  so the numbers in a refusal and the numbers in a verdict cannot disagree.
- **Cost margin generalised.** "Cost margin" becomes a margin on whichever quantity the change was
  meant to move (quota, wall-clock, memory), since a competition run usually optimises the score
  and only sometimes trades it for something else. The other two gates are unchanged.

**The blog is quoted, not copied.** `references/upstream/blog-automating-eval-design-and-hillclimbing.md`
records the sections that are cited. The Apache-2.0 licence covering the four guides does not
extend to the published article, so its text appears as attributed quotation in the mapping files
rather than as a redistributed file.

**No upstream code, scripts, or tooling are vendored.** Upstream's `runner-scaffold.mjs`,
`build-report-lite.mjs` and `report/SCHEMA.md` are not reproduced, and no upstream dependency is
added to this plugin.

**Trademarks.** Anthropic, Claude and claude are trademarks of Anthropic, PBC. This plugin is not
affiliated with or endorsed by Anthropic, PBC. The trademarks are used here only to attribute the
upstream work.

## Other adapted works in this package

Listed for completeness; each carries its own attribution in its own file.

| Work | Where | Licence |
|---|---|---|
| K-Dense Scientific Agent Skills — `experimental-design`, `uncertainty-and-units` | `skills/ablation-design/` | MIT (scripts not vendored) |
| K-Dense Scientific Agent Skills — `scientific-visualization` | `skills/scientific-plotting/` | MIT (scripts vendored) |
| K-Dense Scientific Agent Skills — `hypothesis-generation`, `scientific-critical-thinking` | `skills/ruler-audit/references/kdense/` | MIT (shipped whole) |
| K-Dense Scientific Agent Skills — `seaborn` | `skills/scientific-plotting/references/kdense/` | BSD-3-Clause as declared upstream (shipped whole) |
| K-Dense Scientific Agent Skills — `scientific-writing`, `scientific-slides` | `skills/technical-report/references/kdense/` | MIT (shipped whole) |
| K-Dense Scientific Agent Skills — `scientific-brainstorming` | `skills/kaggle-competition-research/references/kdense/` | MIT (shipped whole) |
| Frontis-MA1 / OpenMLE, arXiv 2607.28568 | `mcp/experiment_tree.py` (parent selection) | cited in `skills/relationships.json` |
| The Last AI Built by Humans, arXiv 2609.11873 | `skills/rsi-experiment-tree/` | cited in `skills/relationships.json` |
| Dream-RSI, arXiv 2609.14858 | `skills/rsi-experiment-tree/` | cited in `skills/relationships.json` |
| Scaling Laws for Agent Harnesses, arXiv 2605.29682 | `skills/rsi-experiment-tree/` | cited in `skills/relationships.json` |
| ModularRSI, arXiv 2609.14857 | `skills/relationships.json` (`divisionOfLabour`) | cited in `skills/relationships.json` |

### The K-Dense bodies under `references/kdense/`

Seven skills from the same project, pinned at commit
`065b734670d7d990627dbc06a05b5a99be33f1f1`, copied byte-for-byte with no edit. The upstream
`LICENSE.md` (MIT, Copyright (c) 2025 K-Dense Inc.) travels with each host's copy. `seaborn`
declares BSD-3-Clause in its own frontmatter, which is recorded as declared rather than resolved;
the repository-level MIT covers the tree either way, and both are permissive.

`experimental-design` was vendored and then removed. It is a laboratory protocol — its worked
examples are mice, plate edges and reagent ageing — and the only part that transfers to a
competition, blocking and what counts as a true independent replicate, is already carried by
`ablation-design` and `ruler-audit`. Shipping it would have added a `pyDOE3` dependency for a
fractional-factorial matrix this package can already enumerate. Its adaptation in
`ablation-design/` predates that and stays.

Two conventions were **not** carried into the host skills, and each is refused by name in the host
that refuses it:

- Every upstream body ends by instructing the agent to fetch an arXiv page and add a K-Dense
  citation to the user's output. That is a courtesy of their distribution, not a step of ours.
- Three of them install packages unconditionally (`experimental-design` pulls `pyDOE3`; `seaborn`
  and `scientific-visualization` pin versions through `uv`). This package asks first, via
  `kaggle_sources action="doctor"` and `presence-mode`.

The bodies are reproduced unedited on purpose. Each host skill states what it takes, what it
declines and why, so the judgement lives in prose somebody can argue with rather than in a diff
nobody reads. Re-fetch with `python tools/fetch_kdense_bodies.py <commit>`.
