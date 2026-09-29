# Kaggle Agent — skill index

Seventeen skills, twenty-nine tools, no reusable subagent, and one graph that binds them. This
page is the index: what exists, which category it belongs to, and what it is bound to.

Every entry below links to its own `SKILL.md`, which remains the single source of truth for its
own procedure. This page tells you which file to open and how the pieces connect.

> **Layout note.** The skills are flat on disk at `skills/<name>/SKILL.md` because that is the form
> the plugin manifest requires. The **layering lives here and in `relationships.json`**, not in
> directories — a category is a grouping you read, and an edge is a dependency the checker
> enforces. A physical folder was never the binding.

## The index

| # | Skill | Category | Use it when |
|---|---|---|---|
| 1 | [`kaggle-cli`](kaggle-cli/SKILL.md) | identity | Any Kaggle call from this machine: sign in, list, push, pull, status, logs. The tool reference, and the rule that a token pasted in chat is still saved. |
| 2 | [`kaggle-account-switch`](kaggle-account-switch/SKILL.md) | identity | More than one account exists and you need to know which is active, or the active one is wrong. The two-name model: username is identity, stored name is the handle. |
| 3 | [`account-rename-visualizer`](account-rename-visualizer/SKILL.md) | identity | An account needs a different stored name. Renders the rename panel — **load and emit it, do not describe it in prose**. |
| 4 | [`kaggle-competition-research`](kaggle-competition-research/SKILL.md) | research | Before committing to an approach. Wave 1 launches four Kaggle-native subagents in one response; wave 2 then runs entirely in the main thread — the general browser search, then the forensics on the pages it named — opens GitHub / Hugging Face / arXiv for real, asks what the sweep is for before and after wave 1, puts the finished plan up for review, and declares the held-out anchor. |
| 5 | [`approach-decision`](approach-decision/SKILL.md) | research | Write it yourself or fork the top public solution, and how to make that call audibly. |
| 6 | [`experiment-launch`](experiment-launch/SKILL.md) | experiment | About to start a real run: which engine, which accelerator, how long, whose quota. Includes the verify-after-launch step that is not optional. |
| 7 | [`log-monitor`](log-monitor/SKILL.md) | experiment | A run is producing logs and you want a subagent to watch it. Defines the only three conditions that justify interrupting the main agent. |
| 8 | [`log-monitor-visualizer`](log-monitor-visualizer/SKILL.md) | experiment | The interval control, with manual entry beside the slider. **Load and emit it — do not describe it in prose.** |
| 9 | [`rsi-experiment-tree`](rsi-experiment-tree/SKILL.md) | experiment | Repeated iteration: which change gained what, which side effects it caused, what to branch next, when to stop. The tree is a **read-gated, validated DAG** and a **replay simulator**. |
| 10 | [`scientific-plotting`](scientific-plotting/SKILL.md) | experiment | After a run produced a result. Six chart types from a bundled pure-stdlib engine, because a number alone does not say whether the delta is real. |
| 11 | [`ablation-design`](ablation-design/SKILL.md) | experiment | Choosing the next batch, or when a fast-growing tree has become hard to read. One variable per node, honest replication, recorded cost. Adapted from MIT-licensed upstream work. |
| 12 | [`ruler-audit`](ruler-audit/SKILL.md) | experiment | Before declaring the next experiment, or when the tree has gone flat. Whether the metric can resolve the change at hand, the two variance layers kept apart because they call for opposite remedies, a hard task told from one this leaderboard happens to punish, and whether a stall belongs to the approach or to the ruler. Adapted from Apache-2.0 upstream work. |
| 13 | [`handoff`](handoff/SKILL.md) | collab | Work spans more than one session or more than one agent. Derived from the tree so it cannot claim a base the tree does not have. |
| 14 | [`github-auth`](github-auth/SKILL.md) | collab | A handoff should reach another machine and this machine cannot authenticate. Device flow, PAT fallback, saying "local only" honestly. |
| 15 | [`presence-mode`](presence-mode/SKILL.md) | collab | Whether to ask the user or auto-decide. Present (the default) asks early and often; away takes recorded, reversible defaults and stops on a budget. |
| 16 | [`evidence-sources`](evidence-sources/SKILL.md) | collab | A conclusion rests on something that was read. Store the paper or repo once, capture the sentence that carries the claim, link it to the node with an explicit relation. |
| 17 | [`genui-scenarios`](genui-scenarios/SKILL.md) | collab | A decision is pending and a widget may carry it — or a state is being reported and a widget would be noise. Decides which. |

| 18 | [`technical-report`](technical-report/SKILL.md) | collab | A finished run the user is satisfied with, or an explicit request for a writeup. Writes the report from the tree's own ledger: the kept chain and its cost, the refuted list by failure layer, the literature already cited with its supporting sentence, and every figure the data supports. Publishes the release to GitHub or a Kaggle dataset and puts both links in. |

### By category

| Category | What it answers | Skills |
|---|---|---|
| [identity](categories/identity.md) | Who am I acting as, and which quota pays? | `kaggle-cli`, `kaggle-account-switch`, `account-rename-visualizer` |
| [research](categories/research.md) | What is this competition, and what do I build? | `kaggle-competition-research`, `approach-decision` |
| [experiment](categories/experiment.md) | How do I run it, watch it, and learn from it? | `experiment-launch`, `log-monitor`, `log-monitor-visualizer`, `rsi-experiment-tree`, `scientific-plotting`, `ablation-design`, `ruler-audit` |
| [collab](categories/collab.md) | How does the work outlive this session, and when should I ask? | `handoff`, `github-auth`, `presence-mode`, `evidence-sources`, `genui-scenarios` |

## No reusable subagent

A research sweep's forensics used to be a dispatched subagent. It is now done in the main
thread, immediately after the general search, for one reason worth keeping: the in-app browser
is bound to the session that owns it, so a detached child could only ever fetch. Doing it here
means a page that needs JavaScript or a signed-in session can actually be opened.

That is also why `kaggle-competition-research` holds the `browses` edges to GitHub, Hugging Face
and arXiv itself. If the forensics ever went back to being delegated, the coverage floor would
have an owner that could not meet it.

## The graph is the binding

[`relationships.json`](relationships.json) is the source of truth for how everything connects, and
`tools/check_plugin.py` fails the build when any index drifts from it — including the table above.
A relationship that is only described in prose can rot; one that is declared in the graph and
checked cannot.

```text
identity ──▶ research ──▶ experiment ──▶ collab
   │            │             │            │
   │ quota      │ plan        │ tree       │ relay
   │ pays for   │ becomes     │ becomes    │ becomes
   ▼            ▼ the run     ▼ the doc    ▼
experiment   experiment-    handoff      github-auth
 (whose      launch         (derived
  quota?)    runs it)        from tree)

presence-mode ──asks──▶ every decision point in the plugin
                 └─needs─▶ genui-scenarios   (who is there to click a widget?)

kaggle-competition-research ──browses──▶ github · huggingface · arxiv
                                 (one thread; the forensics are not delegated)

experiment-tree (kaggle_experiment_tree) ──enforces──▶ rsi-experiment-tree
                                     ├─loops──────▶ rsi-experiment-tree
                                     └─enforces──▶ rsi-experiment-tree
sources-store  ──enforces──▶ experiment-tree   (provenance can never dangle)
plot-engine    ──enforces──▶ rsi-experiment-tree
```

### Edge types

| Type | Meaning |
|---|---|
| `needs` | This skill cannot work without the other. A prerequisite, not a suggestion. |
| `dispatches` | Launches the other at runtime — a subagent, a follow-on skill, or an external skill. |
| `produces` | This skill's output is the other's input. The hand-off edge. |
| `browses` | **The coverage floor.** The source's own pages must be opened and parsed. A snippet or a result listing does not satisfy it. |
| `asks` | `presence-mode` decides whether the agent may auto-decide there or must stop and ask. |
| `enforces` | **A tool, not a convention.** The named discipline is validated in code. Prose can be skipped; validation cannot. |
| `loops` | A read-then-decide cycle gated by the tool: a write is refused unless the reader saw the current state. |
| `widget` / `gates` | The GenUI binding: which business decision a visualizer renders, and which skill decides whether a widget is warranted at all. |

### Where two mechanisms touch one decision point

`genui-scenarios` and `presence-mode` both reach four skills, and that overlap is real rather
than accidental. It is recorded under `divisionOfLabour` in the same file, and the checker refuses
a duplicated claim with no such declaration — because two gates that each think they decide the
same thing is the shape that cost ModularRSI eight points. The split is always the same: **presence
decides whether to ask, genui decides what form the settled answer takes.**

### State the graph declares

`relationships.json` also declares the **live state** those edges are gated on, so a decision point
can find it without already knowing the skill that holds it:

| State | Values | Read with |
|---|---|---|
| `presence` | `present` · `away` | `kaggle_presence action="get"` |
| `search-engine` | `google` · `bing` | `kaggle_search_engine action="ask"` |

`kaggle_presence action="get"` returns the state **and** the ask-or-auto verdict in one read, and
fails toward *asking* if the state cannot be read — the cost of a wrong ask is a pause, the cost
of a wrong auto is an unattended irreversible action.

## Shared authoring material

[`_shared/`](_shared/README.md) holds the GenUI foundation, forked **once** and read by every
visualizer through a relative path, plus `COMPONENTS.md` — the component shapes these widgets
already share, so a new one is assembled rather than rewritten. Neither the foundation nor the
components are registered capabilities; they are authoring reference only.

`tools/check_plugin.py` is the enforcement layer: it validates the graph, the manifests, the skill
frontmatter, the tree mechanics, the evidence chain, and drives the MCP server over the wire.

## Reading order that is usually right

Research before you run, run before you conclude, conclude before you hand off:

```
identity (who/quota) → research (what to build) → experiment (run / watch / learn)
                                                        ↓
                                             collab (handoff + ask discipline)
```

The one thing that is not optional: **open the linked `SKILL.md`, do not act from this table.**
The index tells you which file to read and how it binds; the skill tells you what to do.
