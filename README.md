# Kaggle Agent

**The environment for human–agent competition research on Kaggle** — 人机协作在 Kaggle 取得竞赛研究成果的环境.

English · [中文](README.zh-CN.md)

**30 tools · 19 skills · 2 optional dependencies (plotting only).**

---

## What you get

| | |
|---|---|
| **Tools** | 29 — quotas, kernels, competitions, accounts, the RSI experiment tree, the evidence store, plotting, presence |
| **Skills** | 18 — grouped as identity, research, experiment, collab |
| **Subagents** | no persona is shipped. The research sweep dispatches four at the one rung whose members are independent, and every other rung runs in the main thread |
| **Runtime dependencies** | none. The server is Python standard library only. `numpy` and `matplotlib` are needed for figures and nothing else, and the plotting skill checks before it draws and offers to install on your word. The Kaggle CLI itself is probed once at startup and, if absent, is offered through the same install path — never installed without you saying yes. |

---

## Architecture

### How a call reaches the tools

![How a call reaches the tools](docs/architecture-launch-path.svg)

The host starts one stdio process and speaks JSON-RPC on its stdout. It does not expand
`${PLUGIN_ROOT}` and does not promise a working directory, so the manifest carries a one-line
bootstrap that locates the package at runtime — and two install shapes have to look the same
locally and in the Marketplace cache.

| | |
|---|---|
| `mcp/agent_server.py` | the self-locating entry. A local install is `<root>/kaggle-agent`; a Marketplace install is cached under a content hash, further down. Neither is matched on a directory name, because a hash directory is not a name. |
| `mcp/kaggle_server.py` | protocol and dispatch for 30 tools. Every Kaggle tool resolves credentials, then asks for the CLI command — a value cached per process, so it is probed once rather than once per call. |
| `mcp/credentials.py` | multi-account store. `token_for()` reads without writing, which is what makes a per-call `account=` safe. |
| `mcp/experiment_tree.py` | the RSI tree. |
| `mcp/deps.py` | what this machine can do, and the one route that may install something. |

`mcp/call_tool.py` | drives that same server as an ordinary subprocess, for a subagent that is not
given its tools. Then the other ten modules cover evidence, handoff, GitHub sync, log monitoring,
plots, presence, search selection and structured input.

### The four categories

![Seventeen skills, four layers](docs/architecture-skill-layers.svg)

A category is a grouping you read; an edge is a dependency the checker enforces. The full graph
lives in [`skills/relationships.json`](skills/relationships.json) and is rendered into
[`skills/README.md`](skills/README.md).

| Category | The question it answers | Skills |
|---|---|---|
| **identity** | Who am I acting as, and which quota pays? | `kaggle-cli`, `kaggle-account-switch`, `account-rename-visualizer` |
| **research** | What is this competition, and what do I build? | `kaggle-competition-research`, `approach-decision` |
| **experiment** | How do I run it, watch it, and learn from it? | `experiment-launch`, `log-monitor`, `log-monitor-visualizer`, `rsi-experiment-tree`, `scientific-plotting`, `ablation-design` |
| **collab** | How does the work outlive this session, and when should I ask? | `handoff`, `github-auth`, `presence-mode`, `evidence-sources`, `genui-scenarios`, `technical-report` |

### Try asking

- *"Research the ARC Prize 2026 competition."*
- *"How much GPU quota do I have left?"*
- *"Should I write this myself or fork the top public notebook?"*
- *"Track my ablation experiments in a tree."*
- *"Watch this run's log and tell me when it errors."*
- *"Write a handoff so another agent can pick this up."*

---

## Install

**From the Marketplace** — search for *Kaggle Agent* in the plugin panel and install it. From the
CLI: `mcode plugin add kaggle-agent@official`.

**From a repository** — the plugin panel can also add a plugin straight from a GitHub repository;
point it at `https://github.com/Timothy-kira/kaggle-agent`.

Either route registers the package; neither builds it.

### Before the first call

| Requirement | Why |
|---|---|
| Python reachable **as `python`** | `servers.mcp.json` launches the server as `python`, so that exact name has to resolve. `python --version` is the check. |
| The Kaggle CLI | The tools shell out to it. If it is missing, `kaggle_sources action="doctor"` says so and offers to install it once you agree. |

**On macOS and Linux**, a machine that ships `python3` only has no `python`, and the symptom is a
plugin that installs cleanly and then exposes **no tools at all**. An empty tool list means the
interpreter was not found, not that the plugin failed to register. One line fixes it:

```bash
mkdir -p ~/.local/bin && ln -sf "$(command -v python3)" ~/.local/bin/python
```

Edit the installed manifest instead and the next update overwrites it. If the server still does not
come up, `python3 -B mcp/agent_server.py` starts it by hand and shows the error.

**A local copy that vanished from the plugin panel.** MiniMax Code 3.1.0 refuses a local plugin
directory that contains any hardlink, `.git` included, and logs nothing when it does. `git clone`
from a local path hardlinks `.git/objects` by default, so clone a working copy into
`~/.minimax/plugins` with `--no-hardlinks`, or from the GitHub URL.
`python tools/check_plugin.py` reports any hardlink it finds.

Both entry points under `bin/` are Python files with a shebang and no executable bit, invoked
through the interpreter — `python3 bin/kaggle-cli.sh`, `python3 bin/run-mcp.sh` — so one command
works on every platform and a packaged file mode never has to survive a checkout.

### Credentials

No token, key or account name is committed here, and the repository is scanned for them on every
validation run. Credentials resolve from your environment or from `~/.kaggle-agent/accounts.json`,
and a token you paste into the conversation is saved to your own store — never written into a
skill, a manifest, a log, a chart, or a handoff. A store at the previous location
(`~/.kaggle-cli/accounts.json`) is copied across on first read and left in place.

---

## RSI for Science

![RSI for Science: the experiment tree](docs/architecture-rsi-tree.svg)

`kaggle_experiment_tree` is the core of the package: a read-gated, validated DAG **and** a replay
simulator. *RSI* here is recursive self-improvement for science — the loop is the point, and it only
becomes a loop if the memory survives the session.

- **The gate is real.** `action="read"` returns a revision; `record` refuses a stale or missing one.
  Prose that says "check the current state first" gets skipped. A revision number does not.
- **A node must declare its failure layer** when it is reverted, so "this didn't work" becomes a
  claim about *where* it broke.
- **Replay scores your own history** under a candidate exploration policy, and `compare` always
  includes the policy you are running — so a recommendation can never be worse than the status quo.
  A replay is an estimate, not a measurement; it says what to try, not what will win.
- **Parent selection is non-greedy** (`score + progress + novelty`, with visit cooling) so a locally
  promising branch cannot starve the search.
- **Per-criterion effective cost.** A gain on one criterion that quietly costs you two others is
  visible as such, because the four cost flags are kept per criterion rather than flattened into
  one boolean.
- **Provenance is mandatory.** A node needs a linked source or an explicit `evidence:"local-only"`.
  A claim with no evidence behind it has to say so out loud.
- **`action="audit-report"` checks the finished prose against the ledger.** It refuses mechanically
  when a `mayNotClaim` sentence has been copied in, or when the tree claims an artifact that is not
  on disk. It **drives and never acquits**: a figure the tree holds proves evidence exists, never
  that it supports the sentence.

---

## Published method, before the experiment

Before committing to a direction, the agent looks for whether published experimental method
already answers it. `kaggle_methods` scrapes an index of scientific-agent skills from the page
upstream keeps for that list, ranks it against what you are about to do, and fetches what is
relevant. The index is not bundled here: a snapshot of someone else's repository is stale the
day after it is taken, and a stale answer to "is there published method for this?" reads as a
search that found nothing.

Four moments trigger it, and which one you are in decides whether it reaches the network:

| | When | Network |
|---|---|---|
| **Research** | at the `method` rung, between `survey` and `field` | yes — scrape, search, fetch |
| **Every declaration** | before each `declare` | no — local index only |
| **Stall** | after two or three flat rounds | no — local index only |
| **New branch** | when a branch opens on different data | yes — scrape, search, fetch |

The two that stay offline are the two that sit inside the experiment loop. `consider` returns the
same shortlist on every call, ranked by how rare the matching words are across the index, with the
words that matched attached so you can judge the hit rather than take it.

**The ruler goes first.** `ruler-audit` stage 2 asks whether the metric can resolve the change at
all; stage 4 asks what to do when it has stopped moving. Both run before any skill is consulted, in
both directions — a method cannot rescue a measurement that cannot see the result, and hunting for
a method before checking the ruler wastes the search.

**A new branch is where the previous answer stops applying.** Different data invalidates the old
refutations, so `consider` no longer carries a `revert` across a branch boundary or a changed
`controls.data`: the node stays in the match list, `consider` names the branch the refutation came
from, and the call is yours.

A download passes four gates in order — allowlist, pinned commit, cache first, scanner — and lands
outside the package. When one fails, the tool reports the HTTP status, the transport's own error
and an offline capability probe, and stops. Which of those is yours to fix, which is a stale pin,
and which is a rate limit are three different questions, and it declines to guess which one it hit.

---

## The tools

**Account and quota** — `kaggle_accounts` (add / switch / rename / list) · `kaggle_auth_status` ·
`kaggle_config_view` · `kaggle_quota` · `kaggle_accelerators`

**Kernels** — `kaggle_kernel_launch` (capped at 12h) · `kaggle_kernel_verify` · `kaggle_kernel_push` ·
`kaggle_kernels_list` · `kaggle_kernels_status` · `kaggle_kernels_logs` · `kaggle_kernels_output` ·
`kaggle_kernel_pull` · `kaggle_kernel_retire`

**Competitions** — `kaggle_competitions_list` · `kaggle_competitions_forums` ·
`kaggle_competitions_leaderboard`

**Science loop** — `kaggle_experiment_tree` · `kaggle_methods` · `kaggle_sources` ·
`kaggle_log_monitor`

**Collaboration and judgement** — `handoff_read` · `handoff_write` · `handoff_status` ·
`handoff_sync` · `github_auth` · `kaggle_presence` · `kaggle_search_engine`

**What the code refuses, and what it leaves to the host.** Three calls act outside this machine
or change it, and each one does nothing unless the call says the user agreed:
`kaggle_kernel_retire` deletes only with `confirm` equal to `ref`, `kaggle_sources action="install"`
installs only with `confirm=true`, and `handoff_sync` pushes only with `confirm=true`. Without it
each reports what it would have done. `kaggle_local_launch` starts a local command once an
experiment node has been declared for it, and that declaration is something the agent can make
itself, so it is not a safety boundary: whether a local command may run is the host's tool
approval, and that is where it should be decided.

---

## Validation

```bash
python tools/check_plugin.py
```

Around 1500 assertions covering the manifest, the skill graph and its rendered index, the tree
mechanics, the evidence chain, the plotting engine's backend, the secrets scan, and a live drive of
the MCP server over the wire. It is the reason the claims in the skills are claims rather than
intentions: a relationship described only in prose can rot, and one declared in the graph and
checked cannot.

Every file under a skill's `assets/`, `references/` or `scripts/` came from someone else, and all of
them are scanned for prompt-injection patterns on every run — with no allowlist, so a vendored file
added later is covered without anyone remembering. A clean scan is a floor rather than a verdict, and
the report says so rather than implying the content was cleared.

Seven probes drive the real thing rather than reading it: the transport, the ablation cycle, the
plot cycle, the claim audit, the monitor cycle, account scope, and the Marketplace layout — which
starts this package's own bootstrap against a Marketplace-shaped directory tree and asks the server
to answer `initialize`. Each probe is proved able to fail: five different breaks turn the layout
probe red, and the transport probe fails when a case gets no reply at all.

The checks include the failures this package actually had. `check_publishable` asserts that no
manifest path is excluded by `.gitignore`, that every gitignore directory rule is anchored, and
that no publishable file contains a credential — because a file once sat on disk, declared in the
manifest and linked from the graph, while the repository was short of it.

---

## Repository layout

```
.minimax-plugin/plugin.json   manifest: 19 skills, one MCP server, no apps
servers.mcp.json              stdio server, inlined bootstrap, cwd-independent
mcp/                          the server and its eighteen modules
skills/                       19 skills, flat as the manifest requires
  categories/                 the layering, as readable pages
  _shared/                    GenUI foundation, forked once
  relationships.json          the single source of truth for how skills connect
docs/                         the architecture figures, generated by tools/draw_architecture.py
tools/                        the enforcement layer
```

Redraw them with `python tools/draw_architecture.py`.

---

## Attribution

Eight bodies from the MIT-licensed
[K-Dense-AI/scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills)
project (formerly `K-Dense-AI/claude-scientific-skills`) were taken at commit
`065b734670d7d990627dbc06a05b5a99be33f1f1`; seven are vendored whole, each under the host skill
that reads it and beside a copy of the upstream licence:

| Vendored body | Read by |
|---|---|
| `hypothesis-generation`, `scientific-critical-thinking` | `ruler-audit` — declaration and verdict review |
| `seaborn`, `scientific-visualization` | `scientific-plotting` — the figure stage |
| `scientific-writing`, `scientific-slides` | `technical-report` — the report and the talk |
| `scientific-brainstorming` | `kaggle-competition-research` — after the sweep |

`experimental-design` was taken and then dropped: it is a laboratory protocol, and the two ideas
in it that transfer — blocking, and what counts as a true independent replicate — are already
carried by `ablation-design` and `ruler-audit`. Shipping its scripts would have meant a `pyDOE3`
dependency for a matrix this package already enumerates.

`ablation-design` is separately adapted from the experimental-design and uncertainty-and-units
material. The method was taken; the dependency-heavy scripts were not. Original MIT licence and
upstream commit are recorded in the skill.

The bodies are shipped unedited, and that is deliberate: each host skill says in its own text what
it takes from them, what it declines, and why — including three places where this package's own
rules are stricter than upstream's. The vendoring is reproducible with
`python tools/fetch_kdense_bodies.py <commit>`.

The replay and non-greedy-selection design follows *Dream-RSI* (arXiv 2609.14858), adapted from a
linear child chain to a multi-child DAG; the per-criterion cost accounting follows *Effective
Feedback Compute* (arXiv 2605.29682). Both are cited in the skills and in `mcp/experiment_tree.py`,
with the places where the adaptation is not a literal port called out in the comments.

## Licence

MIT.
