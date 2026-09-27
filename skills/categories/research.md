# research — 调研与选型

**What this category answers:** what is this competition actually, and what should I build?

Research exists to produce a plan. A plan is only as good as the evidence under it, so both
skills here are organised around *how* the evidence was gathered, not around what it found.

| Skill | Use it when |
|---|---|
| [`kaggle-competition-research`](../kaggle-competition-research/SKILL.md) | Before committing to an approach. Two waves of parallel subagents: four on Kaggle-native sources, then three external ones whose queries the main agent derives from the first wave. Each wave is launched by issuing every subagent task in one assistant response. |
| [`approach-decision`](../approach-decision/SKILL.md) | The write-it-myself vs fork-the-top-notebook choice, and how to make that call audibly. |

## The shape of a research sweep

```
Wave 1 (4 subagents, launched in ONE response, independent)
  ├─ Kaggle Code        what competitors actually run
  ├─ Discussion forum   what participants are told and asking
  ├─ Overview + rules   the macro facts and the constraints
  └─ CPU notebook       what the data actually is
        ↓  main agent synthesises, then DIRECTS wave 2
Wave 2 (3 subagents, launched in ONE response; each opens the real source in the browser)
  ├─ GitHub  ├─ Hugging Face  ├─ arXiv / the web
        ↓
  main agent writes the plan → offers the handoff
```

**A wave is parallel only if every one of its `task` calls is in the same assistant response**,
each with `run_in_background=true`, and no `task_output` read in between. Dispatching one
subagent per message runs the wave serially while still producing a correct-looking report.
There is no `team-plan` or Team Engine entry point to call: the local runtime retired that plan
renderer, and background `task` calls in one response is the verified mechanism.

**Wave 2 is never run in parallel with wave 1.** Its queries only exist once wave 1 has
answered, and searching before you know what to search for returns the same generic prior art
every time. The waits belong between waves, never inside one.

## Read in full, not the title

A notebook title says nothing about its method. A forum row is metadata, not the rule. A
README's first line is a claim, not a fact. The research skill requires pulling notebook
source and reading forum messages, and requires quoting the line that supports each claim —
because a claim without a quote is a rumour, and the plan gets built on it.

## Verified tool behaviour you should not rediscover

The research skill records what was actually tested against a live competition, including the
parts that are *not* reachable: the correct slug must come from `competitions_list`; the forum
is paginated and page 1 is a fraction of it; `authorName` comes back empty; `kernels list` has
no competition filter; `competitions pages` is host-only; and the browser only helps if it
already holds a signed-in Kaggle session, because Kaggle renders client-side. Read that
section in the skill before dispatching subagents — a wrong slug 403s and looks like an auth
failure.

## Reusable subagent

The source forensics are handled by the plugin's own **`competition-browser`** (比赛 browser)
subagent, which opens the real GitHub, Hugging Face and arXiv pages and reports checkable fields
rather than impressions. Its avatar ships in
`assets/agent-avatars/competition-browser.png` and is copied into the agent directory on install.
See [`competition-browser-agent.md`](../competition-browser-agent.md).

The general search is not that agent: it is the host's `deep-research` skill, run in the main
thread over multiple rounds, because that is the session where the in-app browser actually lives.

## Cross-references

- `handoff` takes the research output as its input — the plan, the constraints, the open
  questions.
- `experiment-launch` acts on the plan; `rsi-experiment-tree` records what the plan predicted.

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
| `kaggle-competition-research` -> needs `kaggle-cli` | slug, leaderboard, forum and kernels-list all go through the CLI tools | <!-- edge:kaggle-competition-research->kaggle-cli:needs --> |
| `kaggle-competition-research` -> dispatches `deep-research` | the general browser search, run in the MAIN thread over multiple rounds; it is where the in-app browser and web_search actually exist | <!-- edge:kaggle-competition-research->deep-research:dispatches --> |
| `kaggle-competition-research` -> browses `github` | the research is not complete until GitHub's own pages were opened and parsed, not merely quoted by a search engine | <!-- edge:kaggle-competition-research->github:browses --> |
| `kaggle-competition-research` -> browses `huggingface` | same floor for Hugging Face model and dataset pages | <!-- edge:kaggle-competition-research->huggingface:browses --> |
| `kaggle-competition-research` -> browses `arxiv` | same floor for the arXiv listing and the paper pages themselves | <!-- edge:kaggle-competition-research->arxiv:browses --> |
| `kaggle-competition-research` -> dispatches `competition-browser` | AFTER the general search, for source-specific forensics the main thread should not hand-wave: stars, licence, last commit, model card, architecture | <!-- edge:kaggle-competition-research->competition-browser:dispatches --> |
| `competition-browser` -> browses `github` | the forensic pass is worthless if it never opened the repo | <!-- edge:competition-browser->github:browses --> |
| `competition-browser` -> browses `huggingface` | model and dataset cards are read on the page, not from a snippet | <!-- edge:competition-browser->huggingface:browses --> |
| `competition-browser` -> browses `arxiv` | the paper page itself, for the number and the evaluation setup | <!-- edge:competition-browser->arxiv:browses --> |
| `kaggle-competition-research` -> produces `approach-decision` | the plan's constraints and open questions are what the fork/write call is judged against | <!-- edge:kaggle-competition-research->approach-decision:produces --> |
| `approach-decision` -> needs `kaggle-competition-research` | the decision is only auditable if the evidence under it was gathered | <!-- edge:approach-decision->kaggle-competition-research:needs --> |
| `kaggle-competition-research` -> needs `search-engine` | a discovery search needs an engine, and the engine is a question until the presence state says otherwise | <!-- edge:kaggle-competition-research->search-engine:needs --> |
| `search-engine` -> needs `presence-mode` | its 'ask' action reads the presence state, so the ask-or-auto decision is made by the tool rather than remembered by the agent | <!-- edge:search-engine->presence-mode:needs --> |
| `kaggle-competition-research` -> needs `browser` | search pages and the three required sites are opened with the in-app browser, which only the main session has | <!-- edge:kaggle-competition-research->browser:needs --> |
| `rsi-experiment-tree` -> dispatches `kaggle-competition-research` | a research node re-runs the same research sweep on purpose; re-reading the forum because a result told you to is the tree recording why | <!-- edge:rsi-experiment-tree->kaggle-competition-research:dispatches --> |
| `presence-mode` -> asks `approach-decision` | fork-vs-write defaults to the auditable path when away | <!-- edge:presence-mode->approach-decision:asks --> |
| `presence-mode` -> asks `kaggle-competition-research` | research self-advances when away; the plan is still written and still shown to the user on return | <!-- edge:presence-mode->kaggle-competition-research:asks --> |
| `presence-mode` -> asks `search-engine` | present: ask which engine for a discovery search. away: use the default and record it. Direct navigation to a known URL is exempt either way. | <!-- edge:presence-mode->search-engine:asks --> |

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
| `kaggle-competition-research` -> needs `kaggle-cli` | slug, leaderboard, forum and kernels-list all go through the CLI tools | <!-- edge:kaggle-competition-research->kaggle-cli:needs --> |
| `kaggle-competition-research` -> dispatches `deep-research` | the general browser search, run in the MAIN thread over multiple rounds; it is where the in-app browser and web_search actually exist | <!-- edge:kaggle-competition-research->deep-research:dispatches --> |
| `kaggle-competition-research` -> browses `github` | the research is not complete until GitHub's own pages were opened and parsed, not merely quoted by a search engine | <!-- edge:kaggle-competition-research->github:browses --> |
| `kaggle-competition-research` -> browses `huggingface` | same floor for Hugging Face model and dataset pages | <!-- edge:kaggle-competition-research->huggingface:browses --> |
| `kaggle-competition-research` -> browses `arxiv` | same floor for the arXiv listing and the paper pages themselves | <!-- edge:kaggle-competition-research->arxiv:browses --> |
| `kaggle-competition-research` -> dispatches `competition-browser` | AFTER the general search, for source-specific forensics the main thread should not hand-wave: stars, licence, last commit, model card, architecture | <!-- edge:kaggle-competition-research->competition-browser:dispatches --> |
| `competition-browser` -> browses `github` | the forensic pass is worthless if it never opened the repo | <!-- edge:competition-browser->github:browses --> |
| `competition-browser` -> browses `huggingface` | model and dataset cards are read on the page, not from a snippet | <!-- edge:competition-browser->huggingface:browses --> |
| `competition-browser` -> browses `arxiv` | the paper page itself, for the number and the evaluation setup | <!-- edge:competition-browser->arxiv:browses --> |
| `kaggle-competition-research` -> produces `approach-decision` | the plan's constraints and open questions are what the fork/write call is judged against | <!-- edge:kaggle-competition-research->approach-decision:produces --> |
| `approach-decision` -> needs `kaggle-competition-research` | the decision is only auditable if the evidence under it was gathered | <!-- edge:approach-decision->kaggle-competition-research:needs --> |
| `kaggle-competition-research` -> needs `search-engine` | a discovery search needs an engine, and the engine is a question until the presence state says otherwise | <!-- edge:kaggle-competition-research->search-engine:needs --> |
| `search-engine` -> needs `presence-mode` | its 'ask' action reads the presence state, so the ask-or-auto decision is made by the tool rather than remembered by the agent | <!-- edge:search-engine->presence-mode:needs --> |
| `kaggle-competition-research` -> needs `browser` | search pages and the three required sites are opened with the in-app browser, which only the main session has | <!-- edge:kaggle-competition-research->browser:needs --> |
| `rsi-experiment-tree` -> dispatches `kaggle-competition-research` | a research node re-runs the same research sweep on purpose; re-reading the forum because a result told you to is the tree recording why | <!-- edge:rsi-experiment-tree->kaggle-competition-research:dispatches --> |
| `presence-mode` -> asks `approach-decision` | fork-vs-write defaults to the auditable path when away | <!-- edge:presence-mode->approach-decision:asks --> |
| `presence-mode` -> asks `kaggle-competition-research` | research self-advances when away; the plan is still written and still shown to the user on return | <!-- edge:presence-mode->kaggle-competition-research:asks --> |
| `presence-mode` -> asks `search-engine` | present: ask which engine for a discovery search. away: use the default and record it. Direct navigation to a known URL is exempt either way. | <!-- edge:presence-mode->search-engine:asks --> |
| `kaggle-competition-research` -> produces `evidence-sources` | every paper and repo the sweep opened is stored once, so a later review can re-read it instead of re-searching | <!-- edge:kaggle-competition-research->evidence-sources:produces --> |
