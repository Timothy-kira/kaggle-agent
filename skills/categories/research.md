# research — 调研与选型

**What this category answers:** what is this competition actually, and what should I build?

Research exists to produce a plan. A plan is only as good as the evidence under it, so the
skills here are organised around *how* the evidence was gathered, not around what it found.

| Skill | Use it when |
|---|---|
| [`kaggle-competition-research`](../kaggle-competition-research/SKILL.md) | Before committing to an approach. Two waves of parallel subagents: four on Kaggle-native sources, then three external ones whose queries the main agent derives from the first wave. Each wave is launched by issuing every subagent task in one assistant response. |
| [`approach-decision`](../approach-decision/SKILL.md) | Which angle to attack before the first node exists — each candidate costed, given a killer, and scored against how converged the public cluster already is — and then the write-it-myself vs fork-the-top-notebook call. |

| [`kdense-methods`](../kdense-methods/SKILL.md) | Before an experiment is declared, while the tree has
  stalled, or when a branch opens on different data — whether published experimental method
  already answers it. Scrapes the upstream catalogue, searches it with the words this
  competition produced, and fetches what is worth reading through four gates. |

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

## No reusable subagent

Both halves of wave 2 happen in the main thread. The general search is the host's
`deep-research` skill, run over multiple rounds, and the source forensics that follow it are done
in that same thread rather than dispatched — which is what lets the forensics use the in-app
browser, something a detached subagent does not have.

The three `browses` edges below are held by the research skill itself for exactly that reason:
if the forensics ever went back to a subagent, the coverage floor would have no owner.

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
consulted for a decision, `enforces` = the gate is attached to that tool, `widget` = rendered
by that visualizer, `gate` = decides whether a widget is warranted, `loops` = feeds back.

| Edge | Why it holds | Marker |
|---|---|---|
| `kaggle-competition-research` -> needs `kaggle-cli` | slug and leaderboard in preflight, then forum and kernels-list inside survey's four subagents - every one of those goes through the CLI tools, which is why the account is checked before the wave and not during it | <!-- edge:kaggle-competition-research->kaggle-cli:needs --> |
| `kaggle-competition-research` -> dispatches `deep-research` | the general search runs in the MAIN thread over multiple rounds with no subagent dispatched anywhere, and the source forensics that follow it are that same thread rather than a second one | <!-- edge:kaggle-competition-research->deep-research:dispatches --> |
| `kaggle-competition-research` -> browses `github` | the research is not complete until GitHub's own pages were opened and parsed, not merely quoted by a search engine | <!-- edge:kaggle-competition-research->github:browses --> |
| `kaggle-competition-research` -> browses `huggingface` | same floor for Hugging Face model and dataset pages | <!-- edge:kaggle-competition-research->huggingface:browses --> |
| `kaggle-competition-research` -> browses `arxiv` | same floor for the arXiv listing and the paper pages themselves | <!-- edge:kaggle-competition-research->arxiv:browses --> |
| `kaggle-competition-research` -> produces `approach-decision` | the plan's constraints and open questions are what the fork/write call is judged against | <!-- edge:kaggle-competition-research->approach-decision:produces --> |
| `approach-decision` -> needs `kaggle-competition-research` | the decision is only auditable if the evidence under it was gathered | <!-- edge:approach-decision->kaggle-competition-research:needs --> |
| `kaggle-competition-research` -> needs `search-engine` | a discovery search needs an engine, and the engine is a question until the presence state says otherwise | <!-- edge:kaggle-competition-research->search-engine:needs --> |
| `search-engine` -> needs `presence-mode` | its 'ask' action reads the presence state, so the ask-or-auto decision is made by the tool rather than remembered by the agent | <!-- edge:search-engine->presence-mode:needs --> |
| `kaggle-competition-research` -> needs `browser` | search pages at field, and the three required sites at forensics, are opened with the in-app browser, which only the main session has - a detached subagent would be left with plain fetches | <!-- edge:kaggle-competition-research->browser:needs --> |
| `rsi-experiment-tree` -> dispatches `kaggle-competition-research` | a research node re-runs the same research sweep on purpose; re-reading the forum because a result told you to is the tree recording why | <!-- edge:rsi-experiment-tree->kaggle-competition-research:dispatches --> |
| `presence-mode` -> asks `approach-decision` | fork-vs-write defaults to the auditable path when away | <!-- edge:presence-mode->approach-decision:asks --> |
| `presence-mode` -> asks `kaggle-competition-research` | the two research agenda questions and the plan review are asked in either mode, so research stops and waits for them; between them research self-advances, and research decides what the questions ask | <!-- edge:presence-mode->kaggle-competition-research:asks --> |
| `presence-mode` -> asks `search-engine` | present: ask which engine for a discovery search. away: use the default and record it. Direct navigation to a known URL is exempt either way. | <!-- edge:presence-mode->search-engine:asks --> |
| `kaggle-competition-research` -> produces `evidence-sources` | every paper and repo the sweep opened is stored once - from method's fetches and from field and forensics - so a later review can re-read it instead of re-searching, and a source nobody stored reads as a source that does not exist | <!-- edge:kaggle-competition-research->evidence-sources:produces --> |
| `kaggle-competition-research` -> needs `ruler-audit` | the held-out set is declared at plan time, so the calibration that decides whether the search split is comparable is written then too, not on the day of the first declare | <!-- edge:kaggle-competition-research->ruler-audit:needs --> |
| `kaggle-competition-research` -> needs `kdense-methods` | between wave 1 and wave 2, in the main thread, the index is refreshed and searched with the words wave 1 just produced; it is the same act as directing the general search, one step earlier | <!-- edge:kaggle-competition-research->kdense-methods:needs --> |
| `rsi-experiment-tree` -> needs `kdense-methods` | consider returns published method on every declaration, and re-reads it when the tree stalls or a branch opens on data the old findings do not cover | <!-- edge:rsi-experiment-tree->kdense-methods:needs --> |
| `ruler-audit` -> needs `kdense-methods` | stage 2 and stage 4 each hand off to it, and only after the ruler has said the metric can resolve the change - a method cannot rescue a measurement that cannot see the result | <!-- edge:ruler-audit->kdense-methods:needs --> |
| `kdense-methods` -> produces `evidence-sources` | a skill that was actually downloaded and read is stored with its name and the upstream commit, on the same relations as a paper, so a later review can re-read it instead of re-searching | <!-- edge:kdense-methods->evidence-sources:produces --> |
| `kdense-methods` -> needs `github-auth` | the scrape and the download are authenticated, and a refused request is not evidence that anything is missing - the two have to stay distinguishable or the index is retired for a reason nobody can see | <!-- edge:kdense-methods->github-auth:needs --> |
| `presence-mode` -> asks `kdense-methods` | a download that fails stops and asks, because a method that is silently missing reads as a method that does not exist; away, the decision to continue on cache is recorded and marked for the user's return | <!-- edge:presence-mode->kdense-methods:asks --> |
| `genui-scenarios` -> gates `kdense-methods` | whether asking about a failed download is a sentence or a panel - never whether to ask, which is presence-mode's call | <!-- edge:genui-scenarios->kdense-methods:gate --> |
