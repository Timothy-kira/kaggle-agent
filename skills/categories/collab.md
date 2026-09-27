# collab — 协作与交接

**What this category answers:** how does this work outlive the current session, and when is a
GUI actually the right response?

| Skill | Use it when |
|---|---|
| [`handoff`](../handoff/SKILL.md) | Work spans more than one session or more than one agent, or the user says "handoff" / 接力 / 交接. Covers when to offer one, what it must contain, and keeping it consistent with the experiment tree. |
| [`github-auth`](../github-auth/SKILL.md) | A handoff should reach another machine and this machine cannot authenticate. Browser device flow, PAT fallback, and saying "local only" honestly. |
| [`genui-scenarios`](../genui-scenarios/SKILL.md) | A decision is pending and a widget may carry it — or a state is being reported and a widget would be noise. Decides which. |

## The handoff is a document, not a feature

Its whole value is that an agent which has never seen this plugin can read the file and
continue correctly. That is why the document is **derived from `tree.json`** rather than from
recollection: a handoff that claims a base the tree does not have is worse than no handoff,
because the next agent will build on a fiction.

Order matters, because it is the order a cold reader needs: the rules and link, where we are
and what the base now costs, how it was produced (including an accelerator that was requested
but never granted), what is already refuted, the next experiment, the gotchas, and the paths.

Write one at a checkpoint, not after every run. If nothing about the base, the refuted set or
the next step changed, the old document is still true.

## Remote sync is a bonus, not a prerequisite

The local file already covers a same-machine relay between agents, because the filesystem is
shared. A remote repo covers a cross-machine one, and the transport is chosen at runtime:
`git` if present, the REST API if a token exists, local only otherwise. That last case is not
a failure — report the handoff as **local, not lost**.

Two principles that generalise beyond this plugin: a published plugin must never ship the
author's own OAuth `client_id` (that would make every user authorise against someone else's
registration), and a token only ever lives in an environment variable or a separate
credentials store, never in a config file that gets committed.

## When a widget is the wrong answer

The most common failure is turning a status report into a form. If the user asked *what is the
state*, the state is the answer and it is plain text. A widget is warranted when a real choice
is pending — signing in, switching account, picking an engine, choosing where the handoff goes.
The tell: if every option in the widget leads to the same next action, it is not a decision.

## Cross-references

- `research` and `experiment` both feed this category.
- `identity` owns the account picker; `genui-scenarios` decides whether to offer one.

## Bound edges (rendered from `../relationships.json`)

These rows are **rendered from `skills/relationships.json`**, the single source of truth for
how the skills in this package relate, and for the live state those relationships are gated
on. `tools/check_plugin.py` fails the build if this table drifts from that file, so the graph
cannot rot back into loose prose. To change a relationship, edit `relationships.json` and
re-run the checker.

Edge types: `needs` = prerequisite, `dispatches` = launched at runtime, `produces` = this
skill's output is the other's input, `browses` = must actually open this source, `asks` =
presence-mode decides whether to ask here, `enforces` = a tool validates this, `loops` = a
read-then-decide cycle gated by the tool, `widget`/`gates` = the GenUI binding.

Where two mechanisms legitimately touch the same skill, the split of labour is declared under
`divisionOfLabour` in the same file, and the checker refuses a duplicated claim that has no
such declaration.

| From -> type -> To | When this edge is live |
|---|---|
| `search-engine` -> needs `presence-mode` | its 'ask' action reads the presence state, so the ask-or-auto decision is made by the tool rather than remembered by the agent | <!-- edge:search-engine->presence-mode:needs --> |
| `rsi-experiment-tree` -> produces `handoff` | the base, its cost and the refuted set are read from the tree, never from recollection | <!-- edge:rsi-experiment-tree->handoff:produces --> |
| `handoff` -> needs `rsi-experiment-tree` | the document is derived from the tree so it cannot claim a base the tree lacks | <!-- edge:handoff->rsi-experiment-tree:needs --> |
| `handoff` -> dispatches `github-auth` | the relay should reach another machine, and only then | <!-- edge:handoff->github-auth:dispatches --> |
| `github-auth` -> needs `handoff` | it authenticates the transport for a handoff, and owns no handoff content | <!-- edge:github-auth->handoff:needs --> |
| `genui-scenarios` -> gates `kaggle-account-switch` | decides whether picking an account warrants a picker at all | <!-- edge:genui-scenarios->kaggle-account-switch:gate --> |
| `genui-scenarios` -> gates `log-monitor` | decides whether the interval is a real pending decision | <!-- edge:genui-scenarios->log-monitor:gate --> |
| `genui-scenarios` -> gates `experiment-launch` | engine and accelerator are choices; the local slider has no widget yet, so that one answers in text | <!-- edge:genui-scenarios->experiment-launch:gate --> |
| `genui-scenarios` -> gates `handoff` | no widget row yet, so where the handoff goes is answered in text | <!-- edge:genui-scenarios->handoff:gate --> |
| `presence-mode` -> asks `kaggle-account-switch` | picking which account is a cheap reversible call when the user is away, and a question when they are present | <!-- edge:presence-mode->kaggle-account-switch:asks --> |
| `presence-mode` -> asks `experiment-launch` | engine and time limit are auto-decided when away only within a stated, conservative cap; never past remaining quota | <!-- edge:presence-mode->experiment-launch:asks --> |
| `presence-mode` -> asks `approach-decision` | fork-vs-write defaults to the auditable path when away | <!-- edge:presence-mode->approach-decision:asks --> |
| `presence-mode` -> asks `log-monitor` | a terminal state auto-advances to the next tree node when away, and reports when present | <!-- edge:presence-mode->log-monitor:asks --> |
| `presence-mode` -> asks `handoff` | a handoff may be drafted unattended, but pushing it anywhere is never auto-decided | <!-- edge:presence-mode->handoff:asks --> |
| `presence-mode` -> asks `kaggle-competition-research` | research self-advances when away; the plan is still written and still shown to the user on return | <!-- edge:presence-mode->kaggle-competition-research:asks --> |
| `presence-mode` -> asks `search-engine` | present: ask which engine for a discovery search. away: use the default and record it. Direct navigation to a known URL is exempt either way. | <!-- edge:presence-mode->search-engine:asks --> |
| `presence-mode` -> needs `genui-scenarios` | a question that is asked can be carried by a widget, so the two decide together and neither alone | <!-- edge:presence-mode->genui-scenarios:needs --> |

## Bound edges (rendered from `../../relationships.json`)

These rows are **rendered from `skills/relationships.json`**, the single source of truth for
how the skills in this package relate, and for the live state those relationships are gated
on. `tools/check_plugin.py` fails the build if this table drifts from that file, so the graph
cannot rot back into loose prose. To change a relationship, edit `relationships.json` and
re-run the checker.

Edge types: `needs` = prerequisite, `dispatches` = launched at runtime, `produces` = this
skill's output is the other's input, `browses` = must actually open this source, `asks` =
presence-mode decides whether to ask here, `enforces` = a tool validates this, `loops` = a
read-then-decide cycle gated by the tool, `widget`/`gates` = the GenUI binding.

Where two mechanisms legitimately touch the same skill, the split of labour is declared under
`divisionOfLabour` in the same file, and the checker refuses a duplicated claim that has no
such declaration.

| From -> type -> To | When this edge is live |
|---|---|
| `search-engine` -> needs `presence-mode` | its 'ask' action reads the presence state, so the ask-or-auto decision is made by the tool rather than remembered by the agent | <!-- edge:search-engine->presence-mode:needs --> |
| `rsi-experiment-tree` -> produces `handoff` | the base, its cost and the refuted set are read from the tree, never from recollection | <!-- edge:rsi-experiment-tree->handoff:produces --> |
| `handoff` -> needs `rsi-experiment-tree` | the document is derived from the tree so it cannot claim a base the tree lacks | <!-- edge:handoff->rsi-experiment-tree:needs --> |
| `handoff` -> dispatches `github-auth` | the relay should reach another machine, and only then | <!-- edge:handoff->github-auth:dispatches --> |
| `github-auth` -> needs `handoff` | it authenticates the transport for a handoff, and owns no handoff content | <!-- edge:github-auth->handoff:needs --> |
| `genui-scenarios` -> gates `kaggle-account-switch` | decides whether picking an account warrants a picker at all | <!-- edge:genui-scenarios->kaggle-account-switch:gate --> |
| `genui-scenarios` -> gates `log-monitor` | decides whether the interval is a real pending decision | <!-- edge:genui-scenarios->log-monitor:gate --> |
| `genui-scenarios` -> gates `experiment-launch` | engine and accelerator are choices; the local slider has no widget yet, so that one answers in text | <!-- edge:genui-scenarios->experiment-launch:gate --> |
| `genui-scenarios` -> gates `handoff` | no widget row yet, so where the handoff goes is answered in text | <!-- edge:genui-scenarios->handoff:gate --> |
| `presence-mode` -> asks `kaggle-account-switch` | picking which account is a cheap reversible call when the user is away, and a question when they are present | <!-- edge:presence-mode->kaggle-account-switch:asks --> |
| `presence-mode` -> asks `experiment-launch` | engine and time limit are auto-decided when away only within a stated, conservative cap; never past remaining quota | <!-- edge:presence-mode->experiment-launch:asks --> |
| `presence-mode` -> asks `approach-decision` | fork-vs-write defaults to the auditable path when away | <!-- edge:presence-mode->approach-decision:asks --> |
| `presence-mode` -> asks `log-monitor` | a terminal state auto-advances to the next tree node when away, and reports when present | <!-- edge:presence-mode->log-monitor:asks --> |
| `presence-mode` -> asks `handoff` | a handoff may be drafted unattended, but pushing it anywhere is never auto-decided | <!-- edge:presence-mode->handoff:asks --> |
| `presence-mode` -> asks `kaggle-competition-research` | research self-advances when away; the plan is still written and still shown to the user on return | <!-- edge:presence-mode->kaggle-competition-research:asks --> |
| `presence-mode` -> asks `search-engine` | present: ask which engine for a discovery search. away: use the default and record it. Direct navigation to a known URL is exempt either way. | <!-- edge:presence-mode->search-engine:asks --> |
| `presence-mode` -> needs `genui-scenarios` | a question that is asked can be carried by a widget, so the two decide together and neither alone | <!-- edge:presence-mode->genui-scenarios:needs --> |
| `rsi-experiment-tree` -> needs `evidence-sources` | the tree refuses a concluded node with no provenance, and this is how provenance is supplied - neither half works alone | <!-- edge:rsi-experiment-tree->evidence-sources:needs --> |
| `evidence-sources` -> needs `rsi-experiment-tree` | a source is stored so a node can cite it; a store nobody cites is a reading list, not evidence | <!-- edge:evidence-sources->rsi-experiment-tree:needs --> |
| `kaggle-competition-research` -> produces `evidence-sources` | every paper and repo the sweep opened is stored once, so a later review can re-read it instead of re-searching | <!-- edge:kaggle-competition-research->evidence-sources:produces --> |
| `sources-store` => enforces `experiment-tree` | a node citing a source id that is not in the store is refused, so provenance can never dangle | <!-- edge:sources-store->experiment-tree:enforces --> |
