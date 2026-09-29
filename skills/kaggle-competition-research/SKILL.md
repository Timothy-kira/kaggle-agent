---
name: "kaggle-competition-research"
description: "Use when researching a Kaggle competition before committing to an approach - what the rules and data actually are, what competitors are doing in Kaggle Code, what the discussion forum says, and what the wider field knows in GitHub, Hugging Face and the papers. Starts with a preflight - handoff_status and the experiment tree - and when work already exists it combines the new research with what is already recorded instead of re-deriving it, or skipping the sweep. Only then: wave 1 launches four Kaggle-native subagents in a single response. Wave 2 then runs entirely in the main thread - the general browser search through the deep-research skill over multiple rounds, then the source forensics on the URLs that search surfaced, with a hard requirement that GitHub, Hugging Face and arXiv are genuinely read rather than quoted from a search snippet, and nothing dispatched to a subagent. The in-app browser is for discovery; reading a known URL is done with web_fetch. Competition data is profiled in a Kaggle CPU notebook and is never downloaded to this machine. Asks what the sweep is for before it starts and again after wave 1, reviews the plan with the user before offering any handoff, reads sources in full rather than skimming titles, and reports coverage limits honestly."
---

# Researching a Kaggle competition

Research exists to produce a **plan**, and a plan is only as good as the evidence under it. This
skill is organised around that: gather from every source that is *inside* Kaggle first,
because that is where the rules, the data and the actual competitive code live, and only then
go outside, using the vocabulary the first wave produced.

## Preflight — is this already being worked on?

**Run this before wave 1. Every time. Research is the expensive path, and re-running it over work
that already exists is the single most wasteful thing this skill can do** — four subagents, a
multi-round browser sweep, and a forensics pass, all to rediscover what is on disk.

```
handoff_status competition="<slug>"        # local handoff? where? base node? sync capability?
kaggle_experiment_tree action="read" competition="<slug>"   # the other place work lives
```

`handoff_status` makes no network call and costs nothing. It answers four things at once: whether
a handoff document exists for this competition, where it would be written, what the current base
node and experiment count are, and whether this machine can sync to a remote repo at all.

Then follow the branch you land in. **Existing work is an input to the research, not a reason to
skip it and not a reason to throw it away** — the wave still runs, and it runs *better*, because
it knows what has already been tried and what the base node cost.

| Preflight says | Do |
|---|---|
| A handoff or a tree with nodes exists | **`handoff_read` it, then run the wave informed by it.** See "Combining with what already exists" below. |
| Nothing local, and this machine cannot sync | Say so in one line, then run the wave. |
| Nothing local, and sync *is* available | **Ask whether there is a handoff in the cloud** before researching — see below. |

### Combining with what already exists

Reading the handoff is not the same as obeying it. The point is that a second research pass on
top of a first is worth far more than a first pass alone:

- **Read it, then `record` nothing new until wave 1 has reported.** Say what the base node is and
  what it cost, so the new evidence is measured against a number that already exists.
- **Spend the wave on what is still open.** The handoff's open questions and its refuted list are
  the search agenda: those are the things a generic sweep would waste its rounds rediscovering.
- **Keep the vocabulary.** Query wave 2 with the terms the handoff already uses and the approach
  it already tried, not with the competition's generic name. That is the same discipline wave 2
  already follows when it queries off wave 1's results.
- **Never overwrite the base node.** A fresh plan is added to the tree as the next node. If the
  new evidence contradicts something the handoff asserts, that is a `record` with an explicit
  verdict and a reason — not an edit of the history.

If you decide a re-run is not worth it, say that and say why. That is a legitimate outcome; a
silent full re-derivation is not.

**The cloud question is a question, not an inference.** Someone else, or you in an earlier
session, may have pushed the work to a repo. `github_auth action="status"` reports the transport
and the configured repo **without a network call**; if a token or repo is missing it says so, which
is exactly the information the user needs to decide. So ask, with the answer attached:

> 本机没有这个比赛的 handoff，但这台机器能同步到远端（transport: git / token 未配置 / repo 未配置）。要不要先拉一份云端的？

Under `presence-mode` this is a **tier-1 decision**: cheap, reversible, changes nothing external,
and getting it wrong costs a full research pass. Ask it when the user is present. When they are
away, record the decision and continue — but say plainly in the report that you assumed no cloud
handoff existed, so an audit can see it.

Never silently overwrite or re-derive a base node the tree already holds. If a handoff exists and
you still want fresh research, say why you are re-running it before you spend the wave.

## The shape: two waves, main agent decides the second

```
Wave 1 (4 subagents, launched in ONE response — see "Launch a wave" below)
  ├─ Kaggle Code       what competitors actually run
  ├─ Discussion forum  what participants are told and asking
  ├─ Overview + rules  the macro facts and the constraints
  └─ Kaggle CPU nb    what the data actually is  (the data never leaves Kaggle)
        │
        ▼   main agent synthesises the four reports into one picture
    published method   kaggle_methods: refresh the index, search it with the words
        │              wave 1 just produced, fetch what is worth reading. Main thread,
        │              no subagent, and it does not change the wave 2 coverage floor.
        ▼   then DIRECTS wave 2
Wave 2 (ENTIRELY in the main thread — one thread, no subagent at all)
  ├─ deep-research     the general browser search, run over multiple rounds here
  │                    (in the main thread, where mcp_browser and web_search exist)
  └─ source forensics  the same thread, opening the GitHub / Hugging Face / arXiv
                       pages the search named, and landing each one on a node
        │
        ▼
  plan written ──▶ Gate A (the decisions) ──▶ Gate B (the whole plan) ──▶ handoff
```

**Wave 2 is one thread, and that is the design.** The general search and the forensics are not
independent tasks — the forensics has nothing to read until the search says which sources matter,
so dispatching them together would brief the second on guesses, which is exactly the
generic-prior-art failure the two-wave split exists to avoid. `deep-research` therefore runs
first and to completion, and the forensics follow in the same thread with the real URLs in hand.

There is a second reason, and it points the other way from the usual one. The in-app browser is
bound to the session that owns it, so a detached subagent has neither `mcp_browser` nor
`web_search` (verified: a background subagent sees only `web_fetch`) — a delegated forensics
pass is structurally limited to plain fetches. **Running it here means the forensics can use the
browser** when a page genuinely needs JavaScript or a signed-in session. Doing this work in the
main thread is not a consolation prize for not having an agent; it is what makes the fuller
method available at all. Note that `deep-research` itself prefers `web_search`/`web_fetch` and
avoids browser automation on its own, so the three required sites are read directly — see the
coverage floor.

**Wave 2's queries come from wave 1's results.** That is the whole reason for the split. The
main agent reads the four reports, notices that everyone's Duck harness forks are converging
on one prompt format, and then the search goes looking for *that* — not for "that competition's
repos" in general. A search dispatched before wave 1 finishes can only search the competition
title, and returns the same generic prior art every time.

**Never start wave 2 before wave 1 has finished.** You would be searching before you know what to
search for, which is the most expensive way to be wrong.

**Synthesis and the plan stay in the main thread.** Comparing four reports is the point, and
it needs all of them at once. Do not delegate the plan.

## Before the wave: ask what it is for

**This is a tier-3 decision, which means it is asked in either presence mode — including when the
user is away.** The reason is specific rather than general: this wave spends four subagents and a
multi-round browser sweep, and what it is pointed at determines all of it. There is no safe
default to fall back to, because running the full field unasked is not a smaller version of the
same job, it is a different job. The turn stops here and waits, and the questionnaire says so
plainly — **nothing is running yet, and nothing is left half-done** — so that answering from
anywhere costs one tap rather than a "what did I just start?".

Ask with `ask_user`, as **text**. Not a widget: mid-research prompts are plain text by the same
`genui-scenarios` rule that keeps decision panels for one-off choices, and a four-way research
agenda is not a form to fill in, it is a sentence to answer.

**Two steps, in one call.** `ask_user` submits a whole form at once and cannot make step 2 depend
on step 1's answer, so the second step must be phrased to stand alone.

Step 1 sets the sweep's reach — **as whole packages, never as a checklist of individual
subagents**, because a set of four independent switches is how a wave gets trimmed into something
that looks parallel and is not. Every option states what it costs:

| Package | What you get | Cost |
|---|---|---|
| **All four** (the default) | Code, forum, rules, and a data profile from Kaggle | The full sweep: four subagents plus a browser pass |
| Rules and code only | The two sources that decide what is possible | No data facts; the plan guesses at the data |
| Rules only | Constraints and the forum, in depth | No competitive code, no data — a plan with nothing to build on |

Step 2 asks what the search should be *pointed at* — what the user actually needs decided. It goes
into each brief's emphasis: a question about the data makes the data profile detailed and the rest
of the wave skimmable, and a question about the field's code does the reverse.

**You may cut a subagent, and if you do the report has to say so.** If the first answer makes one
of them pointless — the sweep is about the scoring dispute and nobody needs a data profile — drop
it, and write which one and why into the plan's coverage-limits section. A trimmed wave is a
legitimate answer to a question about what the sweep is for, and spending a subagent to produce
evidence the user has already said they do not need is its own waste.

What is not legitimate is a wave that quietly lost a member. A trimmed wave and a complete one
produce the same shape of report, and the difference is invisible to whoever reads it next, so
the omitted member has to be named as omitted — the same rule as every other gap in this skill.

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
- **No question may land inside a wave.** Both `ask_user` calls sit *outside* it — the first
  before the first `task`, the second after the last one has been read. An `ask_user` between two
  `task` calls blocks that response, which pushes the remaining dispatches into a later message,
  which is precisely the serialisation this section exists to prevent. A wave that needs a
  decision is a wave that was briefed wrong; fix the brief, do not interleave.

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
on one prompt format, and then searches for *that* — not for "that competition's repos" in
general. A search started before wave 1 finishes can only use the competition title, and returns
the same generic prior art every time.

**Never start wave 2 before wave 1 has finished.** You would be searching before you know what to
search for, which is the most expensive way to be wrong. The correct boundary is: launch all four
wave-1 `task` calls in one response, wait for the whole wave, read all four reports, and only then
begin wave 2 — which from there on is **this thread, with no `task` call in it at all.** The waits
are between waves, never inside one.

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

## Every subagent returns the same four lines

A subagent that reports findings but not method produces a result nobody can re-run, and on this
skill that is the difference between evidence and a rumour that happened to be true. So every
brief ends with the same four-line note, and the main agent refuses to treat a report without it
as complete:

```
Read:        <what you actually opened, by name>
How:         <the command, verbatim, so it can be re-run exactly>
Blocked at:  <the point you got to and what stopped you, or "nothing">
Coverage:    <what you did not read, and why>
```

**"How" has to be copy-pasteable.** Not "used the CLI" — the actual `kaggle kernels list
--competition <slug> --page-size 200`, the page number, the topic id. A method note that cannot be
re-run is a description, and a description is what we were trying to replace.

**"Coverage" is the one that gets skipped, so it is the one that matters most.** "Coverage: all
notebooks" is false the moment a bytes-repr ref was unrepaired, a page was not fetched, or a
notebook was too long to read in full. Name the gap and the reason. The server caps a sweep at 100
records regardless of `--page-size`, so "read everything" is not available as an answer anyway.

**The main agent lifts these into places that already exist — no new concepts.**

| The note | Goes into |
|---|---|
| The data subagent's read/coverage | Its own node's `controls.data`, plus the `reason` — that is what a later run is permitted to lean on |
| The other three subagents' blocked-at and coverage | The handoff's `constraints` |
| Every external source cited | `kaggle_sources`, **added by the main agent** |

The last row is not a style preference. **Subagents cannot write the tree in parallel** — the
`record` gate requires a current `readRevision` and three of four parallel writers get
`stale_read` — and `kaggle_sources` has no lock on its read-modify-write either, so two subagents
adding at once silently lose one. The main agent adds them in sequence, after the wave.

A subagent that returns early with nothing is a legitimate outcome, and the four lines are how it
says so: "Blocked at: the forums endpoint 403s on page 3; Coverage: pages 1–2 only". A subagent
that returns nothing at all is an outage, not a negative result.

## Wave 1 subagent 1 — Kaggle Code

Kaggle Code is the most competition-specific source and the one most often reduced to a
notebook list. Treat a notebook list as a shortlist, then read the notebooks. The point of this
subagent is not a list of notebooks — it is an answer to **"what is in the top of the field's
code, and what did they actually change to score higher?"**

1. Establish the ranked field first, so you know whose code is evidence:
   `kaggle_competitions_leaderboard` for the top N teams (default 10). **It is paginated at 20 per
   page and the first line of the output is `Next Page Token = …`**, which reads like footer noise.
   Take the next page while the token keeps advancing.
2. Sweep the code space. **Use the CLI, not the MCP tool, for this** — the `kaggle_kernels_list`
   MCP tool really does have no competition filter, but the CLI does, and it is exact:
   ```
   kaggle kernels list --competition <slug> --page-size 200
   ```
   That returns competition-scoped kernels only, with no fuzzy-search contamination, and it is
   exact where fuzzy `--search` is not — a `duck` search comes back with text-classification
   notebooks from other competitions and years. Two facts about this command that decide whether
   the sweep works at all, both measured:
   - **The server caps it at 100 records** whatever `--page-size` says (500 and 1000 both return
     100, while `--help` claims a maximum of 200), and it emits a blank line per record, so a raw
     line count inflates the result about 2x. **100 is a ceiling, not the size of the field** —
     never write "the competition has 100 notebooks".
   - **Records with non-ASCII metadata come back as Python bytes reprs.** Verified on
     `example-lab-2026-cell-segmentation`: 9 of 100 refs arrived as `b'some-user/0-947-…'`
     rather than `some-user/0-947-…`, with the title escaped to `\xf0\x9f\x94\xac` and author
     names mangled the same way. **The top-voted notebook in that sweep was one of the nine.** A
     naive extract hands `b'owner/slug'` to `kaggle_kernel_pull` and the pull fails, and it fails on
     exactly the notebooks you most wanted. Strip the `b'` and the trailing quote, and if non-ASCII
     survived as escapes, restore it as utf-8; **list separately anything you could not repair
     rather than dropping it** — a silently dropped ref is indistinguishable from a notebook that
     does not exist.
3. **Read the keepers.** `kaggle_kernel_pull` each one and read `notebook.ipynb`. Record: title,
   author, votes, last run, the model it calls, the prompt/observation format, the action-parsing
   assumption, and the score its author claims.

Report the negative results as findings. "Ranks 2 through 5 have published nothing for this
competition" is a real, decision-relevant result: it means the field's code is behind the
field's scores, and any plan has to build its own baseline.

State the coverage limit explicitly: the leaderboard lists teams, the code list lists
notebooks, public notebooks are a fraction of what competitors actually ran, and votes measure
attention rather than quality — official random-sample notebooks routinely top the vote list.

### There is no score column, so "the best notebooks" needs a proxy you name out loud

`kaggle kernels list` returns `ref / title / author / lastRunTime / totalVotes` and **no score at
all**. So "read the high-scoring notebooks" is not an instruction anyone can execute until you say
what *high-scoring* means here. **Pick one proxy, write it down, and report every number you
derive from it.** These are the options, in descending order of how much they can be trusted:

| Rank | Proxy | What it is worth |
|---|---|---|
| 1 | **Leaderboard cross-reference** — a team on the board, matched to the notebooks by that author | The only one that connects a notebook to a real score |
| 2 | **The score the author claims in their own source** | Unverified, and must be labelled as the author's claim |
| 3 | `totalVotes` | Attention, not quality |

**Read the top band, not all of them.** 10–15 notebooks is a defensible sample; reading 100 is not
required and is usually why this subagent does not finish. What is *not* optional is the other
half of the discipline: **report which notebooks you did not read, and why.** A coverage claim
that only names what was read reads as a claim that everything was read.

### Cluster them: most "independent" notebooks are one codebase

Reading notebooks one at a time produces a list of unrelated methods, and the list is wrong in a
specific, common way — several of them are the same code. Diff the `source` arrays of the notebooks
you pulled and group the ones that share an ancestor: same body apart from imports, paths and
environment constants. **Report, per cluster: the ancestor, how many notebooks forked from it, and
how their votes and claimed scores spread.** Real sweeps of this kind find that 24 of 26
competition notebooks load the same support pack, or that two notebooks are byte-identical across
214,619 characters; that is one contribution to the field, not two, and a plan that treats them
as independent evidence will double-count it.

Say which criterion you used to call two notebooks the same. "Same ancestor" is unfalsifiable
without one.

### Then classify by score band, and find where the movement is

Put each cluster's notebooks into score bands, and then answer one question that the list itself
cannot: **does the score move inside a cluster, or between clusters?**

- **Movement inside a cluster** is attributable — the notebooks share an ancestor, so a difference
  between them is a real change worth reading.
- **Every cluster sitting in the same band** means the field has no increment to copy yet, and the
  plan needs to invent one. That is a finding, and it is more useful than a long list.

### Attribute the gain: diff two adjacent notebooks from the same cluster

Take two notebooks **in the same cluster with adjacent scores** and diff them. The difference is a
candidate change. Write it in the shape this plugin already uses, and reuse that vocabulary
rather than inventing a second one:

- **`factors`** — the symmetric difference, and **it has to be one line**. A diff that turns out to
  be two independent changes is two results, not one: split it and say you are splitting it.
- **`controls`** — what both notebooks share: the same model weights, the same TTA, the same
  evaluation split. This is what makes the difference mean anything; if you cannot name the
  controls, you have a difference and not a comparison.
- **`conditional`** — when a constant appears in one cluster and nowhere else (measured sweeps keep
  finding a dozen environment-variable differences that only one author ever ran), say that it was
  only ever tested inside that one lineage. It is not an independent factor.

**A diff produces a hypothesis; only a re-run settles it.** The numbers here are
**author-reported** — a printed score, a "0.947 LB" in a title — and in a code competition a
public leaderboard entry can move when the host re-runs submissions, so a notebook's number is
not a fixed property of its code. This is the same rule as everywhere else in this plugin:
`audit-report` drives and does not acquit. A diff says "+0.006 came from this change, probably".
To *adquit* it, run both notebooks on Kaggle under today's rules and compare — the machinery for
that is the same declared run as any other (`declare` → `kaggle_kernel_launch` → `verify` →
`kernels_output` → `settle`). **Never write a diff-derived attribution and a reproduced one in the
same voice**, and say which is which in the report.

### And separate method gains from scores the rules gave away

A number on a public notebook is not automatically a number you can go and get. Some of the
highest-scoring notebooks from an earlier era earned that score on an evaluation that no longer
behaves the same way — a label leak, a duplicated train/test row, a post-hoc correction against
published answers, a submission that exploited a limit. **The host fixes those, and when it does
the score evaporates**, which makes such a notebook a trap for anyone who builds on it: it looks
like the strongest evidence in the field and it is evidence about the past.

**Flag it while you read, then let a re-run decide it.** While reading the source, the patterns
worth noting are: a claimed score far above what the method can explain; a lookup table, a hard-coded
answer, or a correction step against published labels; a train/test overlap the notebook itself
never checks; and a `lastRunTime` that predates a competition update. Record those as
*suspected*, and say which of the three you actually observed.

**The only thing that acquits or convicts is a re-run under the current rules.** Same machinery as
above: run it, and let the reproduced score settle it. Two report rows, never blended:

| | Claimed | Reproduced today | Verdict |
|---|---|---|---|
| Method gain | 0.947 | 0.951 | a real, reproducible change |
| Rules-era gain | 0.962 | 0.941 | a score the rules gave away, now gone |

A reproduced score that collapses is not a failed experiment — it is one of the most valuable
things the sweep can find, and it must be reported as a first-class result, not quietly dropped.
Check the forum and the competition's own update notes for a "we fixed the scoring" announcement,
and carry anything you find into the plan's constraints: an unfixed rule is a live hazard for
everyone else too.

## Verified tool behaviour — read this before dispatching any subagent

Tested against a real competition. Getting these wrong wastes an entire wave, so they are
stated once here rather than rediscovered per subagent.

**Get the slug from the CLI, never construct it.** The real slug is longer than the obvious
one — `kaggle-inc-2026-example-challenge`, not `example-challenge`. A wrong slug returns
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

**`kaggle kernels list` mangles non-ASCII refs, and it mangles the ones you wanted.** Measured on
`example-lab-2026-cell-segmentation`: 9 of 100 records came back as Python bytes reprs —
`b'some-user/0-947-…'` instead of `some-user/0-947-…`, titles as `\xf0\x9f\x94\xac`, author
names similarly escaped. **The highest-voted notebook in that sweep was among the nine.** Feeding
an extracted ref to `kaggle_kernel_pull` as-is fails, and it fails on exactly the notebooks that
looked most competitive. Strip the `b'` and the trailing quote, restore escapes as utf-8, and
**list what you could not repair** rather than dropping it. The same command also returns **no
score column** and **caps at 100 records** regardless of `--page-size`, so "the 100 notebooks" is
a ceiling, never a total.

**`competitions leaderboard --show` is paginated at 20 rows, like the forum.** The first line of
its output is `Next Page Token = …`, which reads like footer noise. Page 1 is the top 20 teams;
taking only that and calling it "the leaderboard" misses everyone below them — and the leaderboard
is the only verifiable link between a score and a notebook author.

**`competitions pages` works and is how you read the rules.** `kaggle competitions pages -c <slug>
--content --page-name <name>` returns the full page text to an ordinary participant. Verified on
`kaggle-inc-2026-example-challenge`, which yielded Rules, Evaluation, Code Requirements, Timeline, Prizes
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
`kaggle-inc-2026-example-challenge` every listed file returned 403 on download while the listing was
readable. That is a real outcome, not a credentials problem — and for **competition data** it is
moot, because you must not download it at all: profile it from a notebook on Kaggle instead (see
wave 1 subagent 4). For *documentation* that 403 applies, and then find the same content
legitimately: the organiser often mirrors the starter repo publicly on GitHub
(`example-org/example-challenge-kaggle-starter` mirrored that competition's `Example-Challenge-Agents/`), and PyPI is
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
every listed file 403s on `kaggle-inc-2026-example-challenge` while the listing itself reads fine. Running
the notebook on Kaggle is not a workaround for that 403, it is the ordinary path — the notebook
already has the competition's input directory mounted, and it is the only place the data should
ever be read.

1. Get the file list with `competitions files <slug>` — paginated, and skip `.git/` internals.
   This is a *listing*, not a download, and it is allowed.
2. Write a notebook that, for every file, records shape, dtype, null count, and for text/JSON
   fields the key structure and a real sample. Read the paths Kaggle mounts into the notebook's
   input directory; never fetch them. Do not load anything that does not fit in memory — profile
   a sample and say so.
3. **Declare the run, then launch it.** `kaggle_kernel_launch` refuses to start without
   `declares=<node id>`, so the declaration is a precondition, not bookkeeping. The declaration is
   also what the profile lands on afterwards, and it needs two things the tree will otherwise
   refuse: a `diagnosis` (this is a first pass, so `diagnosis="none"` plus a real
   `diagnosisReason`) and a prediction. Profiling optimises nothing, so there is no direction to
   predict — say that with `expectOmitted` rather than inventing a fake metric:

   ```
   kaggle_experiment_tree action="read" competition="<slug>"      # returns readRevision
   kaggle_experiment_tree action="declare" node={
     "id":"d1","kind":"experiment","parent":null,
     "change":"profile every competition file from a Kaggle CPU notebook",
     "hypothesis":"the files disagree with the Data page somewhere, or the split leaks",
     "diagnosis":"none","diagnosisReason":"first pass on this competition; no run exists yet",
     "expectOmitted":"profiling measures the data, it does not optimise anything; there is no metric to move"
   }
   kaggle_kernel_launch folder="<folder>" accelerator="none" declares="d1"
   kaggle_kernel_verify ref="<user>/<slug>" expected="none"
   kaggle_kernels_output ref="<user>/<slug>" path="<scratch>"
   kaggle_experiment_tree action="settle" declared="d1" node={... "verdict":..., "reason":...}
   ```

   `settle` needs its own `read_revision` like every write, and the metrics it records include
   `controls.data` — put **which files were actually read and how** in there, because "the data
   profile" and "a profile of every file" are very different claims and only the tree knows which
   one a later run was allowed to lean on.

4. A launch that silently landed on an accelerator spends quota nobody meant to spend, and
   `kaggle_kernel_verify expected="none"` is the only thing that tells you it did. Run it
   immediately after the launch, not later.
5. When the run completes, fetch **only the profile**: `kaggle_kernels_output` into a scratch
   folder. That is the few kilobytes of JSON this notebook wrote, not the dataset.
6. Report the problem in terms a plan can use: what one row *is*, what the target is, exactly
   what a submission must look like (filename, format, size), how the split is constituted, and
   any leakage or duplication you can see.
7. Flag anything contradicting the Data page. That page is written for humans and goes stale;
   the files do not.

This is what lets the plan say "we must submit N files of format X" instead of guessing, and
whether a CPU-only baseline is feasible at all.

### When the account that may read the data is not the account that has the GPU

The common shape: **account A has entered the competition, account B has not, and B is the one
with GPU quota left.** A's notebooks can mount the competition input directory; B's cannot. There
is no flag that fixes this, because the permission is a property of the account and the quota is a
property of a different account.

**Do not try to download the data to work around it.** `competitions download -f <path>` against
competition data returns 404 — the API does not download competition files by path — so the
per-file route does not exist, and a whole-competition download is banned above anyway. The data
has to change hands **inside Kaggle**, through a dataset, on a CPU.

```
A  ── CPU notebook ──▶  pack dataset  ──▶  B ── GPU notebook ──▶  runs
(reads the competition      (public or     (mounts the pack,      (B's quota,
 input it is entitled to)     private)       never the competition) spent here)
```

1. **A runs a CPU notebook** — `kaggle_kernel_launch accelerator="none" account="<A>"` — that
   reads the mounted competition input and consolidates it into a few large files under
   `/kaggle/working/pack/`. Consolidation is the point: many small files become one dataset that
   a second account can mount cheaply. Declare and settle this run like any other; the copying is
   still a run with a result, not a setup step that needs no record.
2. **A publishes the pack as a dataset from inside Kaggle**, so the bytes never reach this
   machine:

   ```python
   !kaggle datasets create  -p /kaggle/working/pack -t "<title>" -r zip
   !kaggle datasets version -p /kaggle/working/pack -m "consolidated competition data" -d
   ```

   That yields a ref like `a_user/pack-slug`. **Never put A's token in a notebook cell** — it is
   published with the kernel. How the token reaches the notebook is your call to make (Kaggle
   Secrets, or credentials the runtime already injects); what is not your call is inlining it.
3. **B mounts the pack and spends B's quota** — add the dataset to `kernel-metadata.json` as
   `"dataset_sources": ["a_user/pack-slug"]`, then
   `kaggle_kernel_launch accelerator="gpu" account="<B>"`. B never needs to have entered the
   competition; it needs access to a dataset, which is a much weaker requirement.

**This is also the route when the quota, not the permission, is what ran out.** When
`kaggle_quota` shows the current account is short, the account that can *read the data* and the
account that can *pay for the GPU* are separable, and separating them is the whole trick. Report
which account read the data and which account ran it — "we had to move the data between accounts
because B's quota was the binding constraint" is a fact the plan needs, and one a reader cannot
reconstruct.

**Cost this route honestly.** The pack is a second copy of the data, it is a copy made by a
notebook rather than by Kaggle, and it inherits whatever the competition's own terms say about
redistribution. If the competition forbids it, say so and stop — a pack you are not allowed to
publish is worse than no pack, because the next run will fail at mount time instead of here.

## After the first wave: ask what it changed, then search for that

Read all four reports first, then ask — and **this question is built from the first answer plus
what the wave actually found**, which is the whole reason for asking twice. A question written
before the wave could only ask "what should I look for"; this one can ask "the forum says the
evaluation is contested and 20 of 24 notebooks are one fork — do you want me to chase the scoring
dispute, the shared codebase, or the methods they all skipped?"

**A second `ask_user` call, not a second step of the first one.** The first form was submitted
before any of this existed, so conditioning on it would mean guessing. Waiting is not the point;
constructing the options from the reports is, and that has to happen after they land. Like the
first, this is tier-3: **asked when the user is away exactly as when they are present**, and the
turn stops here. At this point the first wave *has* run, so the questionnaire has to say that —
the work is not lost, it is finished and waiting, and answering from anywhere just redirects the
search rather than restarting anything.

The answer sets the search terms for `deep-research` and what the forensics agent goes after. It
narrows where wave 2 looks; it does not decide whether the coverage floor is met — GitHub,
Hugging Face and arXiv are read either way, because that floor is what makes the research a
research rather than a search.

## Between the waves: published method, before the general search

This sits in the seam on purpose. Wave 1's reports have just been synthesised, which means the
main agent is holding this competition's vocabulary — that everyone's harness forks converge on
one prompt format, that the split is contested, that the data has an odd shape. Using those
words to look for published experimental method is the same act as using them to direct wave 2,
and the skill already argues for that act twice above. Run before wave 2 rather than earlier
because before wave 1 there is nothing to search with but the competition name, and a search on
the competition name returns the same generic prior art every time.

**In the main thread. No subagent.** Wave 2 is one thread because the forensics pass has nothing
to read until the search names its sources; this is the same shape one step earlier, and
dispatching it would brief a subagent on guesses.

Three steps, in order — `kaggle_methods` with `action="refresh"`, then `action="search"`, then
`action="fetch"` per candidate worth reading:

1. **Refresh the index.** It is scraped from the page upstream keeps for its catalogue, not
   shipped here, so it is one request and it is current. `action="probe"` first if you want to
   know whether the cache is stale before spending it.
2. **Search** with the change and the hypothesis you would actually declare — in this
   competition's words, not the competition's name. Ranked candidates come back with the words
   that matched, and a shortlist is a shortlist: reading it is your call, not the tool's.
3. **Fetch** the ones worth reading, by name and at the pinned commit. Downloads land outside
   the package, cached by commit, and are scanned before anything is stored.

**A failed fetch stops and asks.** The tool reports the status, the transport's error and an
offline capability probe, and deliberately does not tell you which of those is your problem.
Hand it to `presence-mode`: present, ask; away, record the decision to continue on cache and
mark it for the user's return. A deferred notice is not a skipped one, and a method that is
silently missing reads as a method that does not exist.

Then run wave 2, which is unchanged and still governed by the coverage floor.

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
| Example | "who is working on the Example Challenge" | `github.com/topics/example-challenge`, `arxiv.org/list/cs.AI/recent` |
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
web_fetch url="https://github.com/topics/example-challenge"
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
with 260 entries, `huggingface.co/models?sort=downloads` with 3.1M models, `github.com/topics/example-challenge`
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

## Wave 2, step 2 — read the sources yourself, in this thread

The general search establishes *that* a repo, a model or a paper exists and roughly what it
claims. This step is the part a general search is not supposed to produce: the specific,
checkable fields a plan gets built on. **You do it yourself, here, in this thread** — it is not
delegated, and nothing in this step may dispatch a subagent.

### What to pull off each source

| Source | Fields |
|---|---|
| GitHub | stars, **licence**, last commit, what it forks, whether it reproduces a top score or is adjacent prior art |
| Hugging Face | id, downloads, licence, base model, claimed score, and what the model card says it was evaluated on |
| arXiv | the paper's own number, its evaluation setup, and any saturation warning the authors give themselves |

**A missing licence is a finding, not a detail.** It constrains what you may build on, and it
belongs in the plan's constraints section.

**Paper numbers are not leaderboard numbers.** A paper's public-set score and a competition
leaderboard score are computed over different, partly hidden sets. Never compare them directly,
and repeat any saturation warning the authors give.

### The failure modes this step exists to prevent

| Failure | What you do instead |
|---|---|
| Quoting a search snippet instead of opening the page | A snippet is a claim *about* a source, not the source. Open it. |
| Falling back to a generic search engine | Forbidden as the method, at every tier. |
| Padding an empty result with loosely related items | An empty result is a result. Report it as empty. |
| Reporting a README's claim as a fact | Say what it actually says, not what its title implies. |
| Missing a licence because it looked like a detail | It is a finding, above. |
| Comparing a paper's benchmark to a leaderboard score | Forbidden outright. |
| Re-running the general search or wave 1 | This step reads URLs the search already surfaced; it does not go looking for more. |
| Presenting an unreadable page as read | Label it `not reachable` and name it. |

### How to read a source, in order

**`web_fetch` first, and normally.** Fetch the specific first-party URL the search gave you and
read the real page. For a public page this is the proper method, not a downgrade — it is still
the source, and not a search engine.

**The browser is a fallback with a stated reason, not a different method.** It is bound to this
session, so it is available here in a way it is not to a detached subagent. Use it when the page
genuinely needs JavaScript, or when it needs a signed-in session that a plain fetch does not
carry — and **say which of the two it was.** A blank first frame is not a blank page: wait, then
re-read, exactly as the search step does.

**If neither works, say so by name and stop.** Report the source as **not reachable in this
session** and name what stopped you. **Never substitute a generic search to fill the gap** — a
search result about a page you could not open is the snippet problem again, one level up.

Every source in the report carries the method it was read with — `fetch`, `browser`, or
`not reachable` — so the main thread's coverage-limits section is honest by construction.

### Land it on a node, or the read evaporates

A field you read and did not record is a field the next session re-reads. Each source that
answered something goes into the evidence store **and** onto a node:

```
kaggle_sources action="add"    kind=code url=<the GitHub/HF/arXiv URL> title=<...>
kaggle_sources action="extract" source_id=<id> quote="<the sentence that carries the claim>"
kaggle_sources action="link"    node_id=<node> relation=supports

kaggle_experiment_tree action="record" node={
  "id":"r3","kind":"research","parent":"<the node this answers>",
  "question":"<what you went to that source to settle>",
  "targets":["code"],                       # or web / paper / model / dataset
  "sources":["<that source_id>"],
  "verdict":"<what the page actually said>",
  "reason":"<the quote, not your summary of it>",
  "opens":"<what this makes possible for a later experiment>"
}
```

`extract` before `link`: a link without the sentence carrying the claim is an assertion nobody
can check, and that is the whole difference between evidence and a rumour. Because this step
now runs in the main thread, sources are added one at a time and in order — the lost-update
problem that made parallel writers dangerous does not apply here, and there is no reason to
batch them either.


## After wave 2: generate before you converge

The sweep has named the field. The next mistake is to take its first plausible answer as the
answer, because by now every option on the table has been read about and the one that sounds best
is the one the last twenty minutes made sound best.
`references/kdense/scientific-brainstorming/` is vendored whole for this step, and three of its
moves are about a single agent rather than a room of them.

**Generate first, evaluate later, and do not interleave.** Upstream's rule is that ideas are
produced without being shown to anything that can rank them, and the reason is anchoring: a
candidate you have already scored is a candidate you will not drop. Here the ranker is you, and
you have just read a great deal, so the discipline is to write the candidates down *before*
re-reading what the sweep found — a short list, unranked, each with the one thing that would make
it wrong.

**Have something that did not propose them attack them.** Upstream puts a different person on
adversarial review for exactly the reason that self-review finds nothing. The nearest equivalent
here is a `verifier` subagent handed the candidate list and the question "what would make this
wrong", with no tree, no history and no stake in it. This is the same dispatch
`technical-report` uses for the finished report, and it is the only mechanism in the package that
can acquit anything.

**Do not let the scores pick the winner.** Upstream is explicit that its matrix is a traceable
decision aid and the judgement stays with a person. The fit is exact: the two gates below already
work this way, and a candidate that wins on a score alone has skipped the gate that exists
precisely because scores are not the decision.

**Not carried:** the facilitation half. Anonymous input, pseudonymous participants, leader-last
sharing and turn-taking are a technique for a room of people, and none of them apply to one agent
at a keyboard. What transfers is the shape — independent generation, adversarial review, a
recorded decision log — and the log is already a node on the tree.

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

**Calibrate in the same place, or not at all.** The anchor decides which set the search may not
score on; it says nothing about whether the search set can resolve the differences the search is
going to look for. A split can be perfectly disjoint and still be too quiet — and a tree with no
noise floor will read every delta below it as a win.

So when you declare the anchor, declare the floor beside it:

```
kaggle_experiment_tree action="calibrate" ruler={"noise": <measured>, "noiseFrom": "<how it was measured>",
  "seedSpread": <...>, "rebuildSpread": <...>, "headroom": <...>, "smallestActionable": <...>}
```

Both numbers come from the same conversation with the user about what the measurement surface is,
and **both belong here, at the end of research, while you still know what those surfaces are** —
for the same reason the split does. A noise floor derived after the first three experiments have
already been read against it is a description of those experiments, not a calibration.

If nothing has been run twice yet, there is no measured floor, and that is worth saying plainly in
the plan rather than shipping a number that looks like a measurement. `ruler-audit` is the skill
that derives it from repeats that already exist, and it refuses a floor computed from two
readings.

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

Then, and only then, **offer the handoff** using the `handoff` skill. Research that is
not written down will be re-derived by the next session, and this is exactly the material
handoff is for. Offer it; do not write it unprompted.

**But a plan nobody approved is not yet a handoff.** Between "the plan is written" and "offer the
handoff" sit two gates, and the second one is the reason this section exists at all: a handoff
file is read by the next agent as *the plan*, so an unapproved plan written to disk becomes an
approved one by the act of landing there.

### Gate A — the decisions that actually branch

Ask with `ask_user`, as text, before finalising anything. Three questions, and only three,
because a user asked to approve a research plan is being asked to make a call, not to proofread:

1. **Which approach.** `approach-decision` has already worked the fork-vs-write trade-off and
   named a killer; this is a confirmation of that call, not a re-derivation. If you find yourself
   re-arguing it, the wrong step is doing the arguing.
2. **Which experiment runs first.**
3. **Which held-out set** — the one `action="anchor"` will declare.

This is **tier-3, asked in either presence mode**. The turn stops here. At this point the whole
research is finished and sitting on disk, so the questionnaire says exactly that: nothing is
running, nothing will be spent while it waits, and answering redirects what happens next rather
than restarting anything.

### Gate B — the whole plan, frozen

After Gate A's answers are folded in, write the final plan to a file and put it up for review.
**There are two ways this passes, and both are correct** — which one applies depends on the
runtime, and a skill cannot promise a tool it does not control:

- **`ExitPlanMode` is available** → write the file, then call `ExitPlanMode({})`. The turn ends
  and the user reviews in place.
- **It is not available, and that is a normal outcome** → write the file, then say plainly which
  path it is at and that **you will not write a handoff, start a run, or push anywhere until it
  is approved.**

Either way, **stop and wait.** Offering the handoff first and reviewing afterwards is the
failure this gate exists to prevent: once the handoff is on disk it looks decided.

### What the gate blocks, and what it does not

| Until the plan is approved | |
|---|---|
| `kaggle_experiment_tree action="anchor"` | **allowed** — declaring the split is part of writing the plan, and delaying it would only shrink the data you chose it from |
| `kaggle_experiment_tree action="declare"` for the first experiment | **allowed** — an announcement costs no quota and changes nothing |
| `kaggle_kernel_launch`, any run | **blocked** — burning a 12h budget on a plan nobody agreed to is the most expensive mistake available here |
| `handoff_write` | **blocked** |
| `handoff_sync`, any remote push | **blocked** — it was already tier-3, and now it has one more gate in front of it |

**Do not read a blocked row as "do not think about it".** Everything that costs nothing and
changes nothing is still fine — the plan can be written, the anchor can be declared, the tree can
be read, sources can be stored. The gate is on *acting*, not on *working*.

### When the user is not watching

**The agenda questions and the plan review are all asked in either mode, including away.** This
inverts the old rule and it is deliberate. Nothing is running when the first one is asked, so
asking costs one tap and loses nothing; running a four-subagent sweep pointed at the wrong
question costs the whole sweep. **Away here means "the work waits for you", not "the work
proceeds without you".** The turn stops at each stop, and each questionnaire states what is and
is not in flight — nothing yet, then one completed wave, then a finished plan — so answering
later is never a guess about what has been spent.

What still self-advances when they are away is everything *between* those stops: once the first
answer is in, the wave launches, the reports are read and synthesised without checking in, the
second question is the next stop, and after that the same is true of Gate A and Gate B. Record
what you decided on their behalf:

```
kaggle_presence action="record"
  decision="ran wave 1 to completion and synthesised before asking the second question"
  rationale="the questions are tier-3 and are asked away; the work between them is reversible and costs no quota"
```

Four stops in a full sweep is a lot of waiting for someone who is not at the keyboard, and that
is the price of asking at all. It is worth naming what each stop is waiting for, so the reason
they are not running something is legible rather than mysterious.

**If `action="record"` returns `stopped: true`**, the away budget is spent: stop and wait rather
than starting another wave on your own judgement.

Every claim carries its source, and keep the four reliability levels apart: what the **host
states** (rules) > what a **notebook claims** (unverified until reproduced) > what a **diff**
suggests (a hypothesis about what changed) > what a **paper argues** (evaluated on its own setup).
They are not equally reliable and the plan should not treat them as such. The middle two are the
ones that get conflated: a re-run is what turns either of them into a fact.

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
