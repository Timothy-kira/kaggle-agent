# `competition-browser` — the plugin's source-forensics subagent

One agent, reused for every competition. Its brief is written fresh each time; the agent itself
is created once.

- **Name:** `competition-browser`  ·  **Display name:** 比赛 browser
- **Reference:** `agent:competition-browser` (custom name, so it needs the `agent:` prefix)
- **Role:** read-only source forensics. Opens the real pages, extracts checkable fields.
- **Avatar:** `assets/agent-avatars/competition-browser.png`
- **Formerly:** `kaggle-search` / "Kaggle 搜索". Renamed because the agent is not the general
  searcher — the general search is `deep-research`, run in the main thread. This agent is the
  thing that opens one specific source and pulls the hard fields out of it.

## Why it exists

`deep-research` establishes *that* a repo, a model or a paper exists and roughly what it claims.
This agent establishes the things a plan is actually built on and that a general search is not
supposed to assert:

| Field | Source |
|---|---|
| stars, **licence**, last commit, what it forks | GitHub |
| downloads, licence, base model, claimed score, what the card was evaluated on | Hugging Face |
| the paper's own number, its evaluation setup, any saturation warning | arXiv |

Its persona enforces the failure modes that matter at this stage:

| Failure it prevents | How |
|---|---|
| Quoting a search snippet instead of opening the page | "a snippet is a claim about a source, not the source" |
| Falling back to a generic search engine | Forbidden as the method |
| Padding an empty result with loosely related items | "An empty result is a result" |
| Reporting a README's claim as a fact | "Say what it actually says, not what its title implies" |
| Missing a licence because it looked like a detail | "A missing licence is a finding" |
| Comparing a paper's benchmark to a leaderboard score | Explicitly forbidden in its brief |
| Re-doing the general search or wave 1 | "Execute the brief's scope, not your own" |
| Writing files it is not meant to write | Returns `ROLE_MISMATCH` instead |

## How it opens a source

The in-app browser is the primary method: load `browser-use:control-in-app-browser`, call
`mcp_browser`, and read the repo page, the model card, the paper page.

**The runtime constraint you must know.** The in-app browser is bound to the session that owns
it, so a **detached background subagent does not have the browser tool in its tool list** — this
was verified: a background subagent sees neither `mcp_browser` nor `web_search`, only
`web_fetch`. Its fallback ladder is therefore, in order, never silently:

1. Fetch the specific first-party page directly with `web_fetch` and read the real page. For a
   public page this is an acceptable substitute — it is still the source, not a search engine.
2. Otherwise report the source as **not reachable in this session** and name it.
3. Never substitute a generic web search to fill the gap.

**So when you are dispatched, expect to work at tier 1.** The parent session holds the browser and
has already opened the three required sites to discharge its own `browses` edges; your brief
carries the URLs it found. If the fields you need are not in the page you fetched, say so and ask
the parent to open it in its browser — do not guess, and do not substitute a search engine.

Every source in the report is labeled with how it was read — `browser`, `fetch`, or
`not reachable` — so the main agent can carry an honest coverage limit into the plan.

## How to dispatch it

One task, dispatched **after** `deep-research` has finished. It is deliberately not parallel
with the general search: the forensics has nothing to read until the search says which sources
matter.

```
task(
  agent_name="agent:competition-browser",
  run_in_background=true,
  description="source forensics for <competition>",
  prompt=<self-contained brief>
)
```

The brief must be self-contained — the agent has no access to the conversation. It needs:

1. The competition name and slug.
2. **The actual URLs** `deep-research` surfaced, verbatim. Not "search for X" — the URLs. A brief
   without URLs sends the agent back to generic searching, which is the thing it exists to avoid.
3. The fields to extract per source (the table above).
4. What counts as a hit, and what to do when nothing is found.
5. An explicit reminder that paper numbers are not leaderboard numbers.

Never write "search for what we discussed" or "look into the approach". Those produce nothing,
because the agent cannot see the discussion.

**One agent, reused.** Do not create a new agent per competition. If `competition-browser` is
not installed, say so and do the forensics in the main thread, where the browser is available
anyway — do not silently skip the step, and do not create a new agent without asking.

## Installing the avatar

The agent's `avatar` field is a **relative path resolved inside the agent's own directory**
(`~/.minimax/agents/competition-browser/`), not a URL and not an absolute path. The file must
exist there *before* `agent create` is called; three different errors are easy to hit and all are
rejected at creation time:

- an absolute path → `Avatar must be a relative image path`
- a relative path pointing outside the agent dir → `Agent directory does not exist or cannot be read`
- the directory exists but the image is not in it yet → `Avatar file does not exist or cannot be safely read`

So the install sequence is:

```
1. mkdir                                    ~/.minimax/agents/competition-browser
2. copy assets/agent-avatars/competition-browser.png -> ~/.minimax/agents/competition-browser/avatar.png
3. agent create ... avatar="avatar.png"
   (or agent update afterwards if the agent already exists)
```

Copying the file first is what makes the relative path resolvable, and it is also what makes the
avatar survive distribution: the image travels inside this package, so anyone who installs it
gets the same avatar without any network fetch.
