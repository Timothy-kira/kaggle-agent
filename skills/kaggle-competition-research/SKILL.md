---
name: "kaggle-competition-research"
description: "Use when researching a Kaggle competition before committing to an approach - what the rules and data actually are, what competitors are doing in Kaggle Code, what the discussion forum says, and what the wider field knows in GitHub, Hugging Face and the papers. Wave 1 launches four Kaggle-native subagents in a single response. Wave 2 then runs sequentially in the main thread: the general browser search through the deep-research skill over multiple rounds, with a hard requirement that GitHub, Hugging Face and arXiv are genuinely opened and parsed rather than quoted from a search snippet, followed by the reusable competition-browser agent for source-specific forensics on the URLs that search surfaced. Reads sources in full rather than skimming titles, and reports coverage limits honestly."
---

# Researching a Kaggle competition

Research exists to produce a **plan**, and a plan is only as good as the evidence under it. This
skill is organised around that: gather from every source that is *inside* Kaggle first,
because that is where the rules, the data and the actual competitive code live, and only then
go outside, using the vocabulary the first wave produced.

## The shape: two waves, main agent decides the second

```
Wave 1 (4 subagents, launched in ONE response — see "Launch a wave" below)
  ├─ Kaggle Code       what competitors actually run
  ├─ Discussion forum  what participants are told and asking
  ├─ Overview + rules  the macro facts and the constraints
  └─ Kaggle CPU nb    what the data actually is  (the data never leaves Kaggle)
        │
        ▼   main agent synthesises, then DIRECTS wave 2
Wave 2 (SEQUENTIAL, in the main thread — no parallel subagents)
  ├─ deep-research     the general browser search, run over multiple rounds here
  │                    (in the main thread, where mcp_browser and web_search exist)
  └─ competition-browser   source forensics on the specific GitHub / Hugging Face /
                           arXiv pages that search named, dispatched AFTER deep-research
        │
        ▼
  main agent writes the plan → offer the handoff
```

**Wave 2 is deliberately not parallel, and that is a design decision, not a limitation.** The
general search and the source forensics are not independent tasks — the forensics has nothing to
read until the search says which sources matter. Dispatching them together means the forensics
agent gets briefed on guesses, which is exactly the generic-prior-art failure the two-wave split
exists to avoid. So: `deep-research` runs first and to completion, in the main thread, and only
then is `competition-browser` dispatched with the real URLs in hand.

There is also a hard capability reason. The in-app browser is bound to the session that owns it,
and the main thread is that session; a detached subagent does not have it (verified: a background
subagent sees neither `mcp_browser` nor `web_search`). The general search is the part that must
genuinely browse, so it belongs in the main thread. Note that `deep-research` itself prefers
`web_search`/`web_fetch` and avoids browser automation on its own, so the three required sites are
read by the main agent directly — see the coverage floor.

**Wave 2's queries come from wave 1's results.** That is the whole reason for the split. The
main agent reads the four reports, notices that everyone's Duck harness forks are converging
on one prompt format, and then the search goes looking for *that* — not for "ARC-AGI-3 repos" in
general. A search dispatched before wave 1 finishes can only search the competition title, and
returns the same generic prior art every time.

**Never start wave 2 before wave 1 has finished.** You would be searching before you know what to
search for, which is the most expensive way to be wrong.

**Synthesis and the plan stay in the main thread.** Comparing four reports is the point, and
it needs all of them at once. Do not delegate the plan.

## Launch a wave — one response, all of them, `run_in_background=true`

**This is the section that stops a wave from quietly becoming serial.** A wave described as
"parallel" but dispatched one subagent at a time is the single most common way this skill gets
used wrong, and it looks exactly like correct behaviour from the inside: every subagent returns
good results, the report is complete, and it simply took four times as long as it should have.

The rule is mechanical, not aspirational:

- **Issue every `task` call for a wave in the SAME assistant response.** Four `task` calls in one
  message = four subagents starting at once. One `task` call per assistant message = serial
  execution, no matter what the diagram above says.
- **Pass `run_in_background=true` on every call in the wave.** This is what actually overlaps them.
- **Never call `task_output` inside the same response that launched the wave.** Reading a result
  blocks that response, which forces the next dispatch into a later response — and that is exactly
  how a "parallel" wave degenerates into a queue.
- Do not put a `task` call after a `bash` call you need to wait on, and do not interleave a
  synthesise-then-decide step between two members of the same wave.

Correct — four calls, one message, no waiting in between:

```
task(agent_name="explore", run_in_background=true, description="wave1-kaggle-code",     prompt=<self-contained brief>)
task(agent_name="explore", run_in_background=true, description="wave1-forum",          prompt=<self-contained brief>)
task(agent_name="explore", run_in_background=true, description="wave1-overview-rules", prompt=<self-contained brief>)
task(agent_name="worker",   run_in_background=true, description="wave1-data-profile",  prompt=<self-contained brief>)
```

Each call returns immediately with a `task_id` (and, once the child exists, a `session_id`).
**Record all of them, then say nothing that waits.** The wave completes on its own and each
finished child wakes the conversation with a `<background-task-finished>` notice. Read the
results with `task_output(task_id)` **only after the whole wave has been launched** — that is
the moment synthesis is allowed, and it is the moment all four reports are finally in hand at
once. Synthesis before the last child lands is the other way this wave gets serialised.

**Evidence this is real, not aspirational.** Verified in this runtime: three
`task(run_in_background=true)` calls issued in one response started three subagents at once,
each received its own distinct `session_id`, and all three completed.

**There is no `team-plan` to call.** Do not try to route a wave through a Team Engine plan or a
`team-plan:` path. That renderer was retired from the local runtime — the old multi-file plan
pipeline now fails fast on purpose, and no `team-plan:` schema, template or `mavis` command is
documented anywhere in this installation. The verified parallel mechanism is the one above:
multiple background `task` calls in a single response. Wave membership is a property of *your
message*, not of any orchestration feature.

**Wave 2's queries come from wave 1's results.** That is the whole reason for the split. The
main agent reads the four reports, notices that everyone's Duck harness forks are converging
on one prompt format, and then sends the GitHub subagent to look for *that* — not for
"ARC-AGI-3 repos" in general. A wave-2 subagent dispatched before wave 1 finishes can only
search the competition title, and returns the same generic prior art every time.

**Never run wave 2 in parallel with wave 1.** You would be searching before you know what to
search for, which is the most expensive way to be wrong. The correct boundary is: launch all four
wave-1 `task` calls in one response, then wait for the whole wave, then read all four reports, then
launch all three wave-2 calls in one response. The waits are between waves, never inside one.

**Synthesis and the plan stay in the main thread.** Comparing four reports is the point, and
it needs all of them at once. Do not delegate the plan.

## Read sources in full, never just the title

This is the rule most likely to be skipped, so state it plainly: **a title, a vote count and a
one-line abstract are not research.**

- A notebook's title tells you nothing about its method. **Pull the notebook source and read
  it.** `kaggle_kernel_pull` downloads it; then read the code. The prompt format, the action
  loop, the model name and the parsing assumptions are all in the source, and all four decide
  whether the notebook is worth building on.
- A forum thread's title tells you nothing about the rule it changed. **Read the messages.**
  One post can be a 2-word "no", and the paragraph under it is the actual constraint.
- A paper's abstract tells you its claim, not its result. Read enough to find the number, the
  evaluation setup, and whether the authors say the result is saturated.
- A GitHub README's first line tells you nothing about whether the code runs. Read the
  install path, the last commit, and the licence.

If a source is too long to read completely, say which part you read and which you skipped. A
partial read presented as a full one is worse than an admitted gap, because the plan will be
built on it.

Record for each source: **what it actually says, the quote or line that proves it, and how
confident you are.** A claim without a quote is a rumour.

## Wave 1 subagent 1 — Kaggle Code

Kaggle Code is the most competition-specific source and the one most often reduced to a
notebook list. Treat a notebook list as a shortlist, then read the notebooks.

1. Establish the ranked field first, so you know whose code is evidence:
   `kaggle_competitions_leaderboard` for the top N teams (default 10).
2. Sweep the code space. **Use the CLI, not the MCP tool, for this** — the `kaggle_kernels_list` MCP
   tool really does have no competition filter, but the CLI does, and it is exact:
   ```
   kaggle kernels list --competition <slug> --page-size 200
   ```
   That returns competition-scoped kernels only, with no fuzzy-search contamination. On
   `arc-prize-2026-arc-agi-3` it returned 1,100 kernels where fuzzy `--search` returned
   unrelated notebooks from other competitions and years. **Gotcha: the CLI prints a blank line per
   record, so counting lines inflates the count roughly 2x — dedupe before counting.**
   Only fall back to `kaggle_kernels_list search=...` (or `kaggle kernels list -s`) when you need
   to hunt by author or method name across all of Kaggle, and say what you filtered out.
3. **Read the keepers.** For each notebook that plausibly belongs to this competition and
   looks competitive, `kaggle_kernel_pull` it and read the source. Record: title, author,
   votes, last run, the model it calls, the prompt/observation format, the action-parsing
   assumption, and the score its author claims.

Report the negative results as findings. "Ranks 2 through 5 have published nothing for this
competition" is a real, decision-relevant result: it means the field's code is behind the
field's scores, and any plan has to build its own baseline.

State the coverage limit explicitly: the leaderboard lists teams, the code list lists
notebooks, public notebooks are a fraction of what competitors actually ran, and votes measure
attention rather than quality — official random-sample notebooks routinely top the vote list.

## Verified tool behaviour — read this before dispatching any subagent

Tested against a real competition. Getting these wrong wastes an entire wave, so they are
stated once here rather than rediscovered per subagent.

**Get the slug from the CLI, never construct it.** The real slug is longer than the obvious
one — `arc-prize-2026-arc-agi-3`, not `arc-prize-2026`. A wrong slug returns
`403 Forbidden`, which looks exactly like an auth failure and sends you off diagnosing
credentials that were fine.

```
kaggle_competitions_list search="<name>"     # the only reliable source of the slug
```

**The forum is paginated, and the pagination is easy to miss.** The topic list ends with
`Next Page Token = N`, which reads like footer noise. Page 1 is about 20 topics out of
several hundred.

```
kaggle_competitions_forums competition=<slug>              # page 1, note the token
kaggle_competitions_forums competition=<slug> page=2       # and onward
```

Stop when the token stops advancing. Reporting page 1 as "the forum" is the single most likely
way to miss the thread that changed a rule.

**`authorName` is always empty on the forum endpoints** — topic list and messages alike. Do
not report an author the API did not give you; read the signature inside the body if you need
one, and say where it came from.

**Message bodies are HTML.** Strip the tags, keep the links. A quoted rule often lives in a
`<blockquote>`, and that is usually the exact text that matters.

**`kaggle kernels list --competition <slug>` is exact and is the right way to sweep.** Only the
MCP tool `kaggle_kernels_list` lacks a competition filter; the CLI has `--competition`, `--dataset`
and `--user`. If you use the MCP tool's `search` instead, it is a fuzzy substring match
across all of Kaggle, so it returns notebooks from other competitions and other years — a
`duck` search comes back with text-classification notebooks from 2018. Filter the result down
to what plausibly belongs here, and say what you filtered out.

**`kaggle_kernel_pull` is how you read a notebook.** There is no "read notebook source" tool:
you download it and read the file. That is the only way to see the prompt format and the action
parsing, which is what actually decides whether a notebook is worth building on.

**`competitions pages` works and is how you read the rules.** `kaggle competitions pages -c <slug>
--content --page-name <name>` returns the full page text to an ordinary participant. Verified on
`arc-prize-2026-arc-agi-3`, which yielded Rules, Evaluation, Code Requirements, Timeline, Prizes
and data-description — every hard constraint of that competition, without opening a browser. Run
`pages` with no page name first to list what is available. Only `competitions hosts` genuinely
403s. This is the highest-value command in the whole sweep; do not skip it.

**`competitions files` is paginated, and `--page-size 200` gets it in one call.** The default page
size emits a `Next Page Token` even when everything fits on one page, and the listing includes
`.git/` internals when a competition ships a repo — skip those, report the real files.

**The browser is a fallback with a hard limit, not a substitute.** Kaggle's pages are a
client-rendered SPA: an unauthenticated fetch returns an empty `<div id="root"></div>` and
`"logged_in": false`, with all content arriving from authenticated XHRs. The in-app Browser
therefore only works if it already holds a signed-in Kaggle session; otherwise it renders a
blank shell. **Verify a signed-in marker before trusting anything the browser returns**, and if
there is none, fall back to the CLI and the forum and record the gap as a limit — never report
an empty page as "nothing found".

**Authentication.** The bare `kaggle` command is not authenticated on its own; the plugin
injects `KAGGLE_API_TOKEN` per call. A subagent that shells out to `kaggle` directly will get
"Authentication required". Either use the plugin's MCP tools, or set `KAGGLE_API_TOKEN` in that
subprocess's environment.

## Wave 1 subagent 2 — the discussion forum
The forum is where the rules actually change, and it is the only place. A rule learned from a
leaderboard or a README is a rumour until the host states it here.

1. List topics, then **walk every page**, as described above.
2. **Read the messages of the topics that matter.** One row is metadata; the messages are where
   the constraint lives. `kaggle_competitions_forums competition=<slug> topic_id=<id>` returns
   every message with votes and full content.
3. Prioritise by votes **and** recency. A high-vote March announcement sets the current rules; a
   zero-vote thread from yesterday is where a rule just changed. Read both ends, and read the
   most recent pages even when their votes are zero.

Per topic record: title, date, votes, comment count, and **the one thing it changed** — with the
quote that proves it. When a rule is stated twice and the versions differ, that discrepancy is
itself a finding and belongs in the plan as an open question.

## Wave 1 subagent 3 — overview and rules

The macro facts and the constraints. Be precise about which parts you can actually reach.

**Reachable from the CLI:**
- `kaggle_competitions_list search="<name>"` → deadline, category, reward, team count, whether
  you have entered.
- `competitions submission-limits -c <slug>` → your submission counts and daily allowance. A
  real participant-scoped endpoint; read it rather than assuming the limit.
- `competitions files -c <slug> --page-size 200` → the data listing. Skip the `.git/` internals.
- `competitions pages -c <slug> --content --page-name <name>` → the **full text of the Overview,
  Rules, Prizes, Evaluation, Code Requirements, Timeline and data-description pages**. This is
  where the binding constraints actually are. List the page names with a bare `pages` call first.

**When a constraint still cannot be read, say so — never infer it from a leaderboard or a README.**
The browser is the fallback for a page the CLI listing does not cover, and it only works if it
already holds a signed-in Kaggle session; verify a signed-in marker first, because an
unauthenticated Kaggle SPA renders an empty shell and reporting that as "the Rules page is blank" is
a false negative.

**`competitions download` may 403 even when `competitions files` lists the file.** On
`arc-prize-2026-arc-agi-3` every listed file returned 403 on download while the listing was
readable. That is a real outcome, not a credentials problem — and for **competition data** it is
moot, because you must not download it at all: profile it from a notebook on Kaggle instead (see
wave 1 subagent 4). For *documentation* that 403 applies, and then find the same content
legitimately: the organiser often mirrors the starter repo publicly on GitHub
(`arcprize/ARC-AGI-3-Kaggle-Starter` mirrored that competition's `ARC-AGI-3-Agents/`), and PyPI is
an ungated route for the vendored wheels. Say which route you used.

If you cannot authenticate, do not infer the rules from a leaderboard or a README. Report the
limit, carry forward every rule the forum or a host post did state, and put the unreadable
constraints into the plan's open questions so the next agent knows exactly what to verify by
hand.

Where you do get a rule, **quote it** and name the page or topic it came from. A plan built on
a misread limit is worthless.

## Wave 1 subagent 4 — the data, analysed in a Kaggle CPU notebook

Read the data, do not infer it from the Data page description. This subagent writes a **CPU-only**
notebook and runs it **on Kaggle**, because a data profile must not cost accelerator quota, and
because **competition data must never be pulled onto this machine**.

**The data stays on Kaggle. Do not run `competitions download` or `datasets download`.** Not for
the profile, not for one sample, not "just to check a single file" — a listing is not a reason to
copy bytes. The ban is not only policy: the download is frequently impossible anyway, because
every listed file 403s on `arc-prize-2026-arc-agi-3` while the listing itself reads fine. Running
the notebook on Kaggle is not a workaround for that 403, it is the ordinary path — the notebook
already has the competition's input directory mounted, and it is the only place the data should
ever be read.

1. Get the file list with `competitions files <slug>` — paginated, and skip `.git/` internals.
   This is a *listing*, not a download, and it is allowed.
2. Write a notebook that, for every file, records shape, dtype, null count, and for text/JSON
   fields the key structure and a real sample. Read the paths Kaggle mounts into the notebook's
   input directory; never fetch them. Do not load anything that does not fit in memory — profile
   a sample and say so.
3. Launch it on the free CPU tier — `kaggle_kernel_launch accelerator="none"` — then
   `kaggle_kernel_verify expected="none"`. A launch that silently landed on an accelerator spends
   quota nobody meant to spend, and the verify is the only thing that tells you it did.
4. When the run completes, fetch **only the profile**: `kaggle_kernels_output` into a scratch
   folder. That is the few kilobytes of JSON this notebook wrote, not the dataset.
5. Report the problem in terms a plan can use: what one row *is*, what the target is, exactly
   what a submission must look like (filename, format, size), how the split is constituted, and
   any leakage or duplication you can see.
6. Flag anything contradicting the Data page. That page is written for humans and goes stale;
   the files do not.

This is what lets the plan say "we must submit N files of format X" instead of guessing, and
whether a CPU-only baseline is feasible at all.

## Wave 2, step 1 — the general search, in the browser, on an engine the user chose

After all four wave-1 reports are in, the main agent synthesises them into one research
question, then searches for it **through the in-app browser on a real search engine**. A brief
that could have been written before wave 1 is too vague — if you can state it without wave 1's
findings, you have not synthesised yet, so go back and read the reports.

### Discovery search vs direct navigation — one of them asks, the other does not

This distinction is the rule, and getting it wrong in either direction is bad: ask every time and
you interrogate the user before opening a URL you already hold; never ask and the engine is
picked silently by whoever ran last.

| | Discovery search | Direct navigation |
|---|---|---|
| When | you do not have the URL yet | you already have the exact URL |
| Example | "who is working on ARC-AGI-3" | `github.com/topics/arc-prize`, `arxiv.org/list/cs.AI/recent` |
| Do this | **ask the engine first, then open the search page** | just open it — **no engine question** |
| Tool | `kaggle_search_engine action="ask"` | nothing; `mcp_browser action="open_tab"` |

The three coverage sources (GitHub, Hugging Face, arXiv) are reached by direct navigation once
you know the URL, so they never trigger the engine question. Only the open-ended "what else is
out there" searches do.

### Before a discovery search, call the tool — it decides whether to ask

```
kaggle_search_engine action="ask" query="<the search terms>"
```

It returns the available engines **and the current presence mode**, and it tells you which of
the two paths you are on. Do not pick an engine yourself and do not skip this call.

**Present** → ask the user with `ask_user` (Bing, Google, or a named engine), then apply it:

```
kaggle_search_engine action="use" engine="<bing|google>"
```

If the user already named an engine this turn, skip the question and just apply it.

**Away** → do not ask. Take the current default, record it, and continue:

```
kaggle_presence action="record"
  decision="discovery search on Bing without asking"
  rationale="user is away; the engine choice is reversible and changes no external state"
```

The engine choice is a **tier-2 decision** under `presence-mode`: reversible, cheap, and it
changes nothing external. That is exactly why it may be auto-decided when the user is away — and
exactly why it should not be auto-decided when they are sitting there, where a two-second question
is free.

### Opening the search, and reading it

```
mcp_browser action="open_tab" input={url: "<the url the tool returned>"}
mcp_browser action="query"   input={kind: "text", selector: "<the selector the tool returned>"}
```

Read the results with `query(kind="text")`. **A blank screen is not an empty result** — both
engines were measured rendering slowly enough that the first frame after navigation can still be
blank while the DOM is already full. If the page looks empty, `wait`, then read again. Never
report "nothing found" off a single un-waited frame.

### What the general search is for

It is the multi-round, open-ended sweep: who is working on this, what approaches exist, what the
field argues about, and which specific pages are worth opening. Both engines were driven through
the real browser on this machine:

- **Bing** — `https://www.bing.com/search?q=<q>&setlang=en&cc=us&count=30`, results in
  `#b_results`. The `setlang` pin matters: without it Bing follows the machine's region and a
  query can come back with Japanese titles and snippets.
- **Google** — `https://www.google.com/search?q=<q>&hl=en&num=20`, results in `#search, #r, body`.
  It redirects to a regional host and paints later than Bing; wait before judging the screen.

Use `deep-research` alongside this for the multi-round *structure* — background, direction,
analysis, verification, writing. It supplies the plan and the discipline; the browser supplies the
results, because `deep-research` on its own prefers `web_search`/`web_fetch` and will not drive a
browser. Neither half is optional.

- **It runs over multiple rounds by design.** `deep-research` is a five-step flow — background,
  direction, analysis and plan, search and verify, write — and each step deepens the last. Do not
  collapse it into one search pass; the multi-round structure is what makes the result grounded
  rather than a first plausible hit.

### The coverage floor: GitHub, Hugging Face and arXiv must be genuinely read

This is a hard requirement, and it is recorded in the graph as three `browses` edges from
`kaggle-competition-research` to `github`, `huggingface` and `arxiv`. The research is **not
complete** until the source's own page has actually been opened and parsed for each of those three
sites.

**The floor is about the source, not the tool.** Read the real page — by whatever means actually
reads it. The browser is the tool for *search*; it is not the tool that decides which pages matter,
and page-reading is not delegated to it.

| You need | Use |
|---|---|
| To **discover** candidate URLs — an open-ended sweep | the in-app browser, on the engine the user picked |
| To **read a URL you already hold** — a repo, a model card, an abstract | `web_fetch` |
| A page that genuinely needs JS rendering, or a signed-in session | the in-app browser, and say why you needed it |

So the default for the three required sites is a direct fetch, not a navigation:

```
web_fetch url="https://github.com/topics/arc-prize"
web_fetch url="https://huggingface.co/models?search=<task>"
web_fetch url="https://arxiv.org/list/<category>/recent"
```

These are direct reads of a known URL, so no engine question applies and no browser is needed.
`web_fetch` is the better tool here for three reasons: it returns the page as text, which is what
you are extracting fields *from*; it needs no session-bound browser, so the same move works in a
subagent; and it does not render, so it cannot hand you a blank first frame you mistake for an
empty page.

**Reach for the browser only when fetching is not enough**, and name the reason: a page that
returns an empty shell without JavaScript, a listing that only populates client-side, or anything
needing a signed-in session. Then load the skill once as the only tool call in that step, and follow
its own rule — after the first Browser action, do not reload the skill unless the runtime says the
receipt is invalid. **If a page looks blank, `wait` and re-read before concluding anything.**

Both paths were exercised on this machine, which is what makes the floor achievable rather than
aspirational: `web_fetch` returns real content from `github.com/topics/…`, `huggingface.co/models`
and `arxiv.org/list/…`; and `mcp_browser` opened and rendered the same three (`arxiv.org/list/cs.AI/recent`
with 260 entries, `huggingface.co/models?sort=downloads` with 3.1M models, `github.com/topics/arc-prize`
with 10 public repos and their stars and update dates visible). Use whichever the page needs.

What does **not** satisfy it:

- a search-engine result listing those pages,
- a snippet that describes them,
- a cached summary,
- "nothing relevant came up", which is a finding you may report *after* having genuinely looked.

A site you could not reach is a legitimate outcome — but it has to be an honest one. If a page
needs a signed-in session neither `web_fetch` nor the browser holds, say that specific site was not
reachable and why. Never present a snippet as if the page had been read; that is the one failure
this floor exists to prevent.

Record per source how it was actually read — `fetch`, `browser`, or `not reachable` — and carry
those labels into the plan's coverage-limits section, where an admitted gap belongs.

## Wave 2, step 2 — source forensics with the `competition-browser` agent

The general search establishes *that* a repo, a model or a paper exists and roughly what it
claims. This step is the part a general search is not supposed to produce: the specific,
checkable fields a plan gets built on. Dispatch it **after** `deep-research` has finished, with
the real URLs from that search in the brief:

```
task(agent_name="agent:competition-browser", run_in_background=true, description="source forensics for <competition>", prompt=<self-contained brief>)
```

Its persona enforces what matters here: it opens the **real source** rather than quoting a
snippet, it reports nothing-found as nothing-found instead of padding the list, and it labels
every source with the method it used. It carries three `browses` edges of its own — to `github`,
to `huggingface`, to `arxiv` — because a forensics pass is worthless if it never opened the page.

The brief must be self-contained; the agent has no access to this conversation:

1. The competition name and slug.
2. The **actual URLs** `deep-research` surfaced, verbatim. Not "search for X" — the URLs.
3. The fields to extract per source: **GitHub** — stars, **licence**, last commit, whether it
   reproduces a top score or is adjacent prior art, what it forks. **Hugging Face** — id,
   downloads, licence, base model, claimed score, and what the model card says it was evaluated
   on. **arXiv** — the paper's own number, its evaluation setup, and any saturation warning the
   authors give themselves.
4. What counts as a hit, and what to do when nothing is found.
5. An explicit reminder that paper numbers are not leaderboard numbers.

Never write "search for what we discussed" or "look into the approach" — the agent cannot see the
discussion, and a brief without URLs sends it back to generic searching, which is the thing it
exists to avoid.

**One agent, reused.** Do not create a new agent per competition. If `competition-browser` is not
installed on this machine, say so plainly and do the same forensics in the main thread, where the
browser is available anyway — do not silently skip the step, and do not create a new agent
without asking.

Two rules that apply to everything this step touches:

- **A missing licence is a finding, not a detail.** It constrains what you may build on, and it
  belongs in the plan's constraints section.
- **Paper numbers are not leaderboard numbers.** A paper's public-set score and a competition
  leaderboard score are computed over different, partly hidden sets. Never compare them
  directly, and repeat any saturation warning the authors give.

## Before any experiment: declare the held-out set

This is the step that makes "it got better" mean something, and it has to happen **here**, at the
end of research, before a single run exists.

```
kaggle_experiment_tree action="anchor" held_out="<the evaluation set the search must not score on>"
```

Pick it from what this competition actually offers:

- the **private** half of a public/private leaderboard split,
- a held-out task you carve out of the training data, fixed and recorded,
- a second competition or dataset used as a transfer check.

After declaring it, any node whose `metric.split` or `metric.rankSource` names that set is
rejected by the tree. That is the point: the improving system must not be able to reach its own
evaluation, or every number it reports is a number it has already seen.

**Why it belongs to research and not to the tree skill.** Splitting the data after the first
experiment does not make anything disjoint — by then you have already tuned against it. The split
is a research decision, made while you still know what the training and evaluation surfaces are.

**What this does not give you.** An anchor that is genuinely external, decided by someone who is
not you. This plugin can enforce disjointness; it cannot supply independence. Say so in the plan's
known limits rather than implying the problem is solved.

## Then: write the plan, and offer the handoff

The main agent produces a plan, in this order:

1. **Constraints that bind** — the rules and limits that actually restrict the approach, each
   quoted, each with its source. Lead with these; they invalidate any plan that ignores them.
2. **What the data is** — from the notebook profile, not the Data page.
3. **Where the field is** — ranked teams, their scores, what code exists, and the explicit
   coverage limits.
4. **What to build on** — from wave 2, with licences and staleness.
5. **The approach**, and the first experiment with its hypothesis and its metric.
6. **The held-out set**, declared via `kaggle_experiment_tree action="anchor"`, and what it is
   and why it is disjoint from the search.
7. **Open questions** — including any rule the host left ambiguous and any contradiction found
   between sources.

Then, once the plan exists, **offer the handoff** using the `handoff` skill. Research that is
not written down will be re-derived by the next session, and this is exactly the material
handoff is for. Offer it; do not write it unprompted.

### When the user is not watching

Research self-advances. This decision point follows `presence-mode` (an `asks` edge in
`../relationships.json`).

**Away** — do not stop to ask whether to keep researching. Research is a document, not an
action: it costs no quota and changes nothing external. Run the waves to completion, write the
plan, and record the decisions you took:

```
kaggle_presence action="record"
  decision="ran both waves to completion and wrote the plan without checking in"
  rationale="research changes nothing external; the plan is for the user to read on return"
```

The plan is still written, still complete, and still shown on their return — self-advancing is
not self-deleting. The presence mode changes whether you *stop to ask*, never whether you do the
work.

**Still ask, away or not:** nothing in the research path is destructive, so the honest answer is
usually "none" — except that if `action="record"` returns `stopped: true`, the budget is spent
and you stop and wait rather than starting another wave on your own judgement.

Every claim carries its source, and keep the three reliability levels apart: what the **host
states** (rules) > what a **notebook claims** (unverified until reproduced) > what a **paper
argues** (evaluated on its own setup). They are not equally reliable and the plan should not
treat them as such.

## Honest limits

State what you could not reach. These are the ones actually observed, not hypotheticals:

- **Rules page text is usually unreadable.** `competitions pages` is host-only, and the
  browser needs a signed-in Kaggle session that an in-app browser often does not have. This is
  the most consequential limit, because a plan built on a guessed submission limit or a guessed
  run-time cap is invalid. Say it is unreadable rather than inferring it.
- private notebooks are invisible, and that is where the best work usually is;
- author-name matching is name-level, not team-membership-level — the CLI has no membership
  endpoint;
- forum `authorName` is empty, so authors come from message signatures or nowhere;
- the forum is paginated, so any count of "how big is this forum" from one page is wrong;
- notebook counts depend entirely on how you listed them: `kernels list --competition` is scoped,
  but a fuzzy `--search` sweeps in other competitions and years, and the CLI's blank line per
  record inflates any raw line count about 2x;
- a guessed slug 403s and looks like a credentials problem;
- a search that returns nothing is reported as nothing, never padded with adjacent results
  dressed up as relevant;
- **a site you did not actually open is not covered.** The graph holds `browses` edges to
  `github`, `huggingface` and `arxiv`; if one of those was only ever seen as a search result, a
  snippet, or a cached summary, then it was not covered, and saying so is the honest report. The
  floor is "the page was opened and parsed", and a site that turned out to be unreachable is
  reported by name with the reason — which is a legitimate outcome, and a different thing entirely
  from quietly counting a snippet as coverage.

A limit you discovered is a finding worth stating plainly. A gap you papered over becomes a
wrong plan.
