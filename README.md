# Kaggle Agent

A MiniMax Code plugin that turns Kaggle into **composable tools and workflows** instead of a
place you paste commands into: competition research that actually opens the sources, runs that
report the accelerator they really got, and an experiment tree that remembers what was already
refuted so the next iteration does not pay for it twice.

**27 tools · 16 skills · 1 reusable subagent · 0 runtime dependencies.**

---

## Install

This repository is the package. Import it into MiniMax Code:

1. Open the plugin panel and add a plugin from a GitHub repository.
2. Point it at `https://github.com/Timothy-kira/kaggle-agent`.
3. Sign in when the first Kaggle call asks — credentials are yours, entered in the conversation
   and stored in your home directory, never in this repository.

Nothing to install, no `pip install`, no virtualenv. The MCP server is Python standard library
only, and the plotting engine writes SVG by hand. If the tool list is empty after import, the
plugin has not been registered — installing it *is* registering it.

### Credentials

No token, key, or account name is committed here, and the repository is scanned for them on
every validation run. The plugin reads credentials from your environment or from
`~/.kaggle-cli/kaggle.json`, and a token you paste into the conversation is saved to your own
store — never written into a skill, a manifest, a log, a chart, or a handoff.

---

## What you get

| | |
|---|---|
| **Tools** | 27 — quotas, kernels, competitions, accounts, the experiment tree, the evidence store, plotting, presence |
| **Skills** | 16 — grouped as identity, research, experiment, collab |
| **Subagents** | 1 reusable — `competition-browser`, for source forensics on real pages |
| **Dependencies** | none at runtime; `git` and `kaggle-cli` are optional and detected, not required |

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

`scientific-plotting` closes the loop: six chart types from a bundled standard-library SVG engine,
because a delta without an interval is not a result.

---

## Repository layout

```
.minimax-plugin/plugin.json   manifest: 16 skills, one MCP server, no apps
servers.mcp.json              stdio server, inlined bootstrap, cwd-independent
mcp/                          the server and its nine modules
skills/                       16 skills, flat as the manifest requires
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

Around 850 assertions covering the manifest, the skill graph and its rendered index, the tree
mechanics, the evidence chain, the plotting engine's self-containment, the secrets scan, and a
live drive of the MCP server over the wire. It is the reason the claims in the skills are claims
rather than intentions: a relationship described only in prose can rot, and one declared in the
graph and checked cannot.

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
