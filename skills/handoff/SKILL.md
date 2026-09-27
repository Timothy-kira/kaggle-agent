---
name: handoff
description: Use when work on a competition spans more than one session or more than one agent and the next person would otherwise have to re-derive everything - or when the user says "handoff", "接力", "交接", or asks what the current state is. Covers when to offer a handoff, what the document must contain, how it stays consistent with the experiment tree, and when to sync it to a remote repo. Also covers reading a handoff written by another agent.
---

# Handoff between sessions and agents

A competition run outlives a single context window, and often a single tool. When that
happens, everything not written down is lost, and the cost of that loss is specific: a
refuted experiment gets re-run, a 12h quota gets spent re-discovering that an accelerator
was never actually granted, and a binding constraint nobody mentioned gets ignored for
three more runs.

A handoff is that written-down state. It is a **document, not a feature** - the point is
that an agent which has never seen this plugin can read the file and continue correctly.

## Offer it at a checkpoint, not automatically

A handoff is cheap to write and noise if overused. Write one when:

- the user asks for it, in any language ("handoff", "接力", "交接", "记录一下现在到哪了");
- the session is about to end and the work continues;
- work is moving to a different agent, machine, or coding tool;
- a milestone just closed - a base advanced, a plateau was reached, a run burned its whole
  budget.

Do **not** write one after every run. If nothing about the base, the refuted set, or the
next step changed, the old document is still true and rewriting it is churn.

## Ask before writing, with a real choice

When a checkpoint has arrived, ask with a form rather than assuming. The decision that
actually matters is where the next agent is running, because that determines whether a
remote repo is worth configuring:

- **Same machine, different agent** - the local file is enough. Claude Code and MiniMax
  Code share the filesystem, so `handoff_read` from the other tool is sufficient. Say so
  plainly; do not push a GitHub setup on someone who does not need it.
- **Different machine** - needs a remote. Run `handoff_status` first to see whether this
  machine can sync at all, and only then offer it.
- **No handoff** - a legitimate answer. Do not re-ask.

If the user has already said where they want it, do not ask again.

### When the user is not watching

This decision point follows `presence-mode` (an `asks` edge in `../relationships.json`). Read
`kaggle_presence action="get"` before asking.

**Present** — ask with the form above. The choice of where the next agent runs is genuinely the
user's, and it is cheap to get wrong.

**Away** — **drafting** a handoff is auto-decidable, because it is a local file and reversible:
write it, record that you did, and keep working. That is a tier-2 decision.

But **syncing it anywhere is not.** Creating a repo, committing, pushing — every one of those is
tier 3 and needs explicit confirmation whether or not the user is at the keyboard:

```
kaggle_presence action="record"
  decision="drafted the handoff locally; did not push it anywhere"
  rationale="no remote target was chosen, and pushing is not auto-decidable"
```

So an unattended run ends with a correct handoff sitting on disk and an explicit note that it
was not pushed. That is the intended outcome: **progress, not permanence.** If
`action="record"` returns `stopped: true`, stop and wait rather than writing more.

## The document is derived from the tree, never from memory

This is the part that decides whether a handoff is trustworthy. The document must not be
written from recollection, because a handoff that claims a base the tree does not have is
worse than no handoff - the next agent will build on a fiction.

So: pass the tree. `handoff_write` reads `tree.json` for the base node, its metric, the
kept chain and the refuted list, and renders the document from that.

```
handoff_write
  competition   = <name>
  title / url / task / metric / deadline
  next          = the next experiment to run
  hypothesis    = what it should improve, and why
  workspace / account / kernels / quota_at_write
  constraints   = the gotchas worth not rediscovering
  tree          = the current RSI tree, whenever it changed
```

Two consequences worth stating to the user when it matters:

- A missing tree renders "no base yet" rather than a guess. That is the tool refusing to
  invent one.
- `handoff_write` **merges** the tree you pass into what is on disk, so passing a partial
  tree cannot erase history. But it also cannot remove a node, because deletion is not a
  merge. To drop a node, edit `tree.json` deliberately and say so.

## What the document must carry

The order is fixed, because it is the order a cold reader needs:

1. **The rules** - link, task, metric, deadline. A wrong metric invalidates everything below.
2. **Where we are** - the base node, its metric, why it was kept, and **what it now costs**.
   The cost matters as much as the score: it is usually the next binding constraint.
3. **How it was produced** - engine, ref, account that paid, accelerator requested *and
   verified*. A requested accelerator that was never granted is the single most expensive
   thing to forget, and the document carries both values so the gap is visible.
4. **Already refuted** - with the reason. This is the section that stops a repeat run.
5. **Next** - the experiment, its hypothesis, and what to measure.
6. **Constraints and gotchas** - the competition rule that differs from the platform, the
   CLI flag that is silently ignored, the thing that ate the last run.
7. **Where things are** - paths, account, notebooks, quota at the time of writing.

Quota is recorded as it was when written, and must be labelled that way. It goes stale, and
a stale number presented as current is how a run gets cut off mid-way.

## Reading a handoff you did not write

```
handoff_status                 # what exists, and whether this machine can sync it
handoff_read competition=<name> # the document
```

Then read `tree.json` beside it. The document is a summary and can lag; the tree is the
structure. When they disagree, **the tree wins** and the document is out of date.

Treat every claim as dated. "RHAE = 6.1" was true at the last write; re-verify anything
load-bearing against the artifacts before spending quota on it.

## Remote sync is optional, and degrades honestly

`handoff_status` reports the transport without touching the network:

- `git` installed and authenticated -> push normally
- no `git`, but a token available -> REST API, no git needed
- neither -> **local only, and the tool says so**

That last case is not a failure. A same-machine agent still reads the file. Never describe
an unsynced handoff as synced, and never let a sync failure imply the document is gone -
it is on disk either way.

`handoff_sync` creates a repo only when `create_repo: true`, which is an outward action
that needs the user to have asked. Everything else is a push into a repo that already
exists.

## Sync at milestones, not per run

Push when the base advances, when a plateau is confirmed, or when the work is being handed
over. Each sync is one commit per file, so frequent syncs produce a readable history of
how the work actually moved. A commit per experiment makes the tree and the remote tell
the same story.
