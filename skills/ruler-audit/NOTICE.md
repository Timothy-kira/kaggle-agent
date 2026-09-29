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

**Modifications.** This is a derivative work and has been modified. The changes:

- **Competition vocabulary throughout.** LLM-application cases, prompts, agents and graders are
  restated as competition data, validation splits, leaderboards, metrics and pipelines. Every check
  that named a Claude-specific mechanism — `stop_reason`, tool-call traces, context windows,
  `AskUserQuestion` — is re-expressed as its competition equivalent, and the Claude-specific
  mechanism itself is removed.
- **The tree is the single ledger.** Upstream's parallel file layout — `results.jsonl`, `traces/`,
  `_state.json`, `summary.json`, `vN/` directories, the `report/` builder and its HTML output — is
  not carried over. Judgements are recorded through `kaggle_experiment_tree` (`action="calibrate"`,
  `action="declare"`, `action="settle"`, `action="regrade"`, `action="anchor"`, `action="ablate"`)
  rather than in files beside it.
- **Two variance layers stated separately.** Upstream's build-variance measurement is restated as
  seed variance and rebuild variance, with the competition-specific list of what a rebuild covers
  (feature caches, preprocessing folds, resampled data, the previous round's prediction file). The
  two layers are stored under separate keys because they call for opposite remedies.
- **Four stall buckets re-homed.** Upstream's artifact-gap, harness, structural and variance
  buckets already exist in this plugin under other names — `failureLayer`, the `parent: null` swap,
  `partial` verdicts and `ablate`'s repeats. The table points at those existing mechanisms rather
  than restating them, and only the judge-disagreement bucket is new. Upstream's
  `failureLayer`-equivalent and the ruler bucket are kept explicitly unmerged.
- **Sign-off points mapped to `ask_user`.** Upstream's `AskUserQuestion` pauses are the same
  gesture; they are expressed as text questionnaires in this plugin's idiom, following the
  `genui-scenarios` rule that a mid-task prompt is a sentence to answer rather than a panel.
- **Arithmetic delegated to the tree.** The three-number resolution check, the verdict floor and
  the re-grading comparison are enforced in `mcp/experiment_tree.py` rather than restated as prose,
  so the numbers in a refusal and the numbers in a verdict cannot disagree.
- **Adoption gates renamed.** "Cost margin" is generalised to a margin on whichever quantity the
  change was meant to move (quota, wall-clock, memory), since a competition run usually optimises
  the score and only sometimes trades it for something else.

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
| Frontis-MA1 / OpenMLE, arXiv 2607.28568 | `mcp/experiment_tree.py` (parent selection) | cited in `skills/relationships.json` |
| The Last AI Built by Humans, arXiv 2609.11873 | `skills/rsi-experiment-tree/` | cited in `skills/relationships.json` |
| Dream-RSI, arXiv 2609.14858 | `skills/rsi-experiment-tree/` | cited in `skills/relationships.json` |
| Scaling Laws for Agent Harnesses, arXiv 2605.29682 | `skills/rsi-experiment-tree/` | cited in `skills/relationships.json` |
| ModularRSI, arXiv 2609.14857 | `skills/relationships.json` (`divisionOfLabour`) | cited in `skills/relationships.json` |
