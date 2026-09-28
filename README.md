# Kaggle Agent

A MiniMax Code plugin that turns Kaggle into **composable tools and workflows** instead of a
place you paste commands into: competition research that actually opens the sources, runs that
report the accelerator they really got, and an experiment tree that remembers what was already
refuted so the next iteration does not pay for it twice.

**29 tools · 17 skills · 2 optional dependencies (plotting only).**

Every tool runs without them. Figures are the one thing that needs a library, and the plotting
skill checks for it before it draws anything and offers to install on your word — never silently.

---

## Install

**From the Marketplace** — search for *Kaggle Agent* in the plugin panel and install it. From
the CLI that is `mcode plugin add kaggle-agent@official`.

**From a repository** — the plugin panel can also add a plugin straight from a GitHub
repository; point it at `https://github.com/Timothy-kira/kaggle-agent`.

Either route registers the package; neither builds it. The MCP server is Python standard
library only, so there is nothing to `pip install` to make the server start.

| Requirement | Why |
|---|---|
| `python` on PATH | `servers.mcp.json` launches the server as `python`, so that exact name has to resolve. `python --version` is the check. |
| `pip install kaggle` | The tools that shell out to the Kaggle CLI need it. |

On macOS and Linux a machine that ships `python3` only has no `python`, and the symptom is a
plugin that installs cleanly and then exposes **no tools at all**. An empty tool list means
the interpreter was not found, not that the plugin failed to register; the fix is a `python`
on PATH, because an edit to the installed manifest is overwritten by the next update.

Figures are the one thing that draws on a library: `numpy` and `matplotlib`, which the plotting
skill checks for before it draws anything and offers to install on your word — never silently.
`kaggle_sources action="doctor"` says whether a figure can be produced on this machine right now.

### Credentials

No token, key, or account name is committed here, and the repository is scanned for them on
every validation run. The plugin reads credentials from your environment or from
`~/.kaggle-agent/accounts.json`, and a token you paste into the conversation is saved to your own
store — never written into a skill, a manifest, a log, a chart, or a handoff. A store found at
the previous location (`~/.kaggle-cli/accounts.json`) is copied across the first time it is
read, and left in place.

---

## What you get

| | |
|---|---|
| **Tools** | 29 — quotas, kernels, competitions, accounts, the experiment tree, the evidence store, plotting, presence |
| **Skills** | 17 — grouped as identity, research, experiment, collab |
| **Subagents** | none — both halves of a research sweep run in the main thread |
| **Dependencies** | none at runtime beyond optional plotting (`numpy`, `matplotlib`); `git` and `kaggle-cli` are optional and detected, not required |

### The four categories

| Category | The question it answers | Skills |
|---|---|---|
| **identity** | Who am I acting as, and which quota pays? | `kaggle-cli`, `kaggle-account-switch`, `account-rename-visualizer` |
| **research** | What is this competition, and what do I build? | `kaggle-competition-research`, `approach-decision` |
| **experiment** | How do I run it, watch it, and learn from it? | `experiment-launch`, `log-monitor`, `log-monitor-visualizer`, `rsi-experiment-tree`, `scientific-plotting`, `ablation-design` |
| **collab** | How does the work outlive this session, and when should I ask? | `handoff`, `github-auth`, `presence-mode`, `evidence-sources`, `genui-scenarios` |

The full index — which skill binds to which, and the graph that enforces it — is
[`skills/README.md`](skills/README.md).

### Try asking

- *"Research the ARC Prize 2026 competition."*
- *"How much GPU quota do I have left?"*
- *"Should I write this myself or fork the top public notebook?"*
- *"Track my ablation experiments in a tree."*
- *"Watch this run's log and tell me when it errors."*
- *"Write a handoff so another agent can pick this up."*

---

## The tools

**Account and quota** — `kaggle_accounts` (add / switch / rename / list) · `kaggle_auth_status` ·
`kaggle_config_view` · `kaggle_quota` · `kaggle_accelerators`

**Kernels** — `kaggle_kernel_launch` (capped at 12h) · `kaggle_kernel_verify` · `kaggle_kernel_push` ·
`kaggle_kernels_list` · `kaggle_kernels_status` · `kaggle_kernels_logs` · `kaggle_kernels_output` ·
`kaggle_kernel_pull` · `kaggle_kernel_retire`

**Competitions** — `kaggle_competitions_list` · `kaggle_competitions_forums` ·
`kaggle_competitions_leaderboard`

**Science loop** — `kaggle_experiment_tree` · `kaggle_sources` · `kaggle_log_monitor`

**Collaboration and judgement** — `handoff_read` · `handoff_write` · `handoff_status` ·
`handoff_sync` · `github_auth` · `kaggle_presence` · `kaggle_search_engine`

---

## What the experiment tree actually does

This is the part that is not a template. `kaggle_experiment_tree` is a read-gated, validated
DAG **and** a replay simulator.

- **The gate is real.** `action="read"` returns a revision; `record` refuses a stale or missing
  one. Prose that says "check the current state first" gets skipped. A revision number does not.
- **A node must declare its failure layer** when it is reverted, so "this didn't work" becomes a
  claim about *where* it broke.
- **Replay scores your own history** under a candidate exploration policy, and `compare` always
  includes the policy you are running — so the recommendation can never be worse than the status
  quo. A replay is an estimate, not a measurement; it says what to try, not what will win.
- **Parent selection is non-greedy** (`score + progress + novelty`, with visit cooling) so a
  locally promising branch cannot starve the search.
- **Per-criterion effective cost.** A gain on one criterion that quietly costs you two others is
  visible as such, because the four cost flags are kept per criterion rather than flattened into
  one boolean.
- **Provenance is mandatory.** A node needs a linked source or an explicit `evidence:"local-only"`.
  A claim with no evidence behind it has to say so out loud.
- **`action="audit-report"` checks the finished prose against the ledger.** It refuses mechanically
  when a `mayNotClaim` sentence has been copied in, or when the tree claims an artifact that is not
  on disk — a refusal is arithmetic, so no model is asked. It never *acquits*: a figure in the
  report that the tree does hold proves the evidence exists, never that it supports the sentence,
  so the reviewer packet carries file paths and an instruction not to accept any summary.

`scientific-plotting` closes the loop: six chart types drawn with numpy + matplotlib, against a
palette that was measured rather than chosen — the bundled auditor shipped in the repo is how the
five colours were cleared for contrast and greyscale, and a delta without an interval is not a
result.

---

## Repository layout

```
.minimax-plugin/plugin.json   manifest: 17 skills, one MCP server, no apps
servers.mcp.json              stdio server, inlined bootstrap, cwd-independent
mcp/                          the server and its fifteen modules
skills/                       17 skills, flat as the manifest requires
  categories/                 the layering, as readable pages
  _shared/                    GenUI foundation, forked once
  relationships.json          the single source of truth for how skills connect
tools/check_plugin.py         the enforcement layer
```

Layering lives in `relationships.json` and `skills/categories/`, not in directory nesting — a
category is a grouping you read, and an edge is a dependency the checker enforces.

---

## Validation

```bash
python tools/check_plugin.py
```

Around 1400 assertions covering the manifest, the skill graph and its rendered index, the tree
mechanics, the evidence chain, the plotting engine's backend, the secrets scan, and a live drive of
the MCP server over the wire. It is the reason the claims in the skills are claims rather than
intentions: a relationship described only in prose can rot, and one declared in the graph and
checked cannot.

Every file under a skill's `assets/`, `references/` or `scripts/` came from someone else, and all
of them are scanned for prompt-injection patterns on every run — with no allowlist, so a vendored
file added later is covered without anyone remembering. A clean scan is a floor rather than a
verdict, and the report says so rather than implying the content was cleared.

The checks include the failure this package actually had. Every check reads the working tree,
so a file can sit on disk, be declared in the manifest, be linked from the graph — and still be
missing from the repository, because `.gitignore` matched it. The unanchored rule that did it has
been rooted, and `check_publishable` now asserts that no manifest path is excluded, that every
gitignore directory rule is anchored, and that no publishable file contains a credential.

---

## Attribution

`ablation-design` adapts the experimental-design and uncertainty-and-units material from the
MIT-licensed [K-Dense-AI/claude-scientific-skills](https://github.com/K-Dense-AI/claude-scientific-skills)
project. The method was taken; the dependency-heavy scripts were not. Original MIT licence and
upstream commit are recorded in the skill.

The replay and non-greedy-selection design follows *Dream-RSI* (arXiv 2609.14858), adapted from a
linear child chain to a multi-child DAG; the per-criterion cost accounting follows *Effective
Feedback Compute* (arXiv 2605.29682). Both are cited in the skills and in `mcp/experiment_tree.py`,
with the places where the adaptation is not a literal port called out in the comments.

## Licence

MIT.
