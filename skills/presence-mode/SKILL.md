---
name: presence-mode
description: Use when a decision point could go either way - whether to ask the user or proceed on a stated default. Decides how much to ask based on whether the user is at the keyboard (present) or has stepped away (away), and keeps the auto-advance budget that stops an unattended run from walking off the end of its own judgement. Covers which decisions auto-advance, which never do regardless of mode, and how to hand an unattended run's reasoning back to the user on their return. Load this before any step that would otherwise stop to ask a question.
---

# Presence mode: ask when they are here, advance when they are not

This skill answers one question, over and over, at every point in a run where the work could
either continue or stop: **is this worth interrupting for?**

## The state lives in the graph, so you can read it without knowing this skill

`relationships.json` declares this state in its `state` block, alongside the relationships it
gates. One cheap read gives you the mode, the budget, the discovery-search engine, and the
ask-or-auto answer:

```
kaggle_presence action="get"
```

It ends with an explicit verdict — `ASK OR AUTO: ask` or `auto` — plus the engine currently
configured. That call reads the graph's state declaration and resolves it through the same tools
you would have called anyway.

This matters because **the previous way of knowing was "read this skill first"**, which quietly
assumes you already know the file exists. Now an agent that has never heard of `presence-mode`
still gets the right answer from one call, because the state is declared where the rest of the
graph already is.

Anything unreadable **fails toward asking**. A pause is recoverable; an unattended irreversible
action is not, so a missing or broken state must never resolve to `auto`.

## The state, and where it lives

Read it with `kaggle_presence action="get"`. It re-reads from disk on every call, so a subagent
that was launched while the user was still sitting there picks up "I'm leaving" on its very next
decision, with no restart. That re-read is the entire mechanism — a value captured once at
launch would keep asking questions all night to nobody.

Set it when the user says something that amounts to presence or absence:

| The user says | Call |
|---|---|
| "I'm here", "watch this with me", "let's do this together" | `kaggle_presence action="set" mode="present"` |
| "I'm going to sleep", "handle it", "don't wait for me", "I'll be back tomorrow" | `kaggle_presence action="set" mode="away"` |

The default, when the user has said nothing at all, is **`present`**. That default is a
deliberate choice and it points the safe way: an unstated user gets a few extra questions, never
unattended irreversible actions. Silence must never mean consent.

## What each mode means

**`present` — the work is collaborative.** The user is watching, so a question is cheap and a
wrong guess is expensive. Ask early, ask often, and prefer asking to guessing. A twelve-hour run
spending six hours on an approach the user would have rejected in thirty seconds is the failure
this mode exists to prevent. Asking is not weakness here; it is the cheap move.

**`away` — the work is autonomous.** Nobody is there to answer, so a question does not make the
work safer, it makes it stall. Take a conservative, reversible, **recorded** default, say what
you chose and why, and keep going.

## Away mode is not permission

This is the line that matters most, and it does not bend:

> `away` lowers the ask threshold for **decisions**. It grants no authority that the tools
> themselves require. Creating a GitHub repo, pushing a handoff to a remote, retiring a kernel,
> or spending the last of someone's accelerator quota still needs explicit confirmation, present
> or not.

The safe default for an away run is **progress, not permanence**. Read, profile, try, measure,
record. Do not publish, delete, or push.

## The three tiers, and where each decision lands

Every decision point in this plugin falls into exactly one of these. The graph in
`../relationships.json` records which, as an `asks` edge from this skill.

### Tier 1 — always auto. Do not ask in either mode.

Nobody would want to be asked these; the question is noise.

- Reading a notebook's source, a forum thread, a paper, a licence file.
- Profiling data, writing a CPU notebook, measuring a shape.
- Fetching a page, opening a URL, checking quota or a kernel's status.
- Recording a node in the experiment tree from a result you already have.

### Tier 2 — auto when away, ask when present.

Reversible, cheap to undo, and the default is obviously right, so unattended is safe.

| Decision | Auto-decided default when away |
|---|---|
| Which account pays | The active one, or the one with quota headroom. Reversible. |
| Read-only probe or cheap baseline | Run it. It costs minutes, not hours. |
| Log-monitor interval | Leave the current value; it is already a deliberate setting. |
| Research **between** its stops | Keep going. The first answer is in and the reports are being read; the next question or review is the following stop. |
| Which engine a **discovery search** uses | The configured default — currently **Google**. This is the one you have to keep the user in the loop about, because the engines index differently. |

When you take one, record it:

```
kaggle_presence action="record"
  decision="<the call you made>"
  rationale="<why that was the safe default>"
```

The rationale is not bookkeeping — it is what lets the user audit your reasoning on their
return instead of re-deriving it from scratch.

### Tier 3 — always ask, in either mode.
Irreversible, externally visible, expensive to undo, or the user's call to make.

- Creating a repo, pushing a handoff, any remote write.
- Retiring or deleting a kernel.
- A run whose time limit would consume most of the remaining quota.
- **The agenda questions of a research sweep** — what to search for, what the findings changed
  the search into, and the review of the finished plan. These are asked in **either** mode, away
  included: nothing is running when the first is asked, and the first wave has already finished
  when the last one is. The work between them self-advances; the questions themselves do not.
  "The user is away" is not a reason to guess what a four-subagent sweep is for, and it is
  certainly not a reason to hand over a plan nobody has read.
- Anything where the two options lead to genuinely different plans.
- Publishing a submission.

If the away budget is spent and a Tier-3 decision arrives, **stop and wait**. Do not pick one and
note it for later.

## The away budget is a circuit breaker, not a permission

An unattended run also has to be *bounded* work. If `away` meant "never ask", an overnight run
could burn twelve hours of quota on a line of attack that was wrong in hour one. So away mode
carries a budget (`kaggle_presence action="get"` shows `remaining`). When
`action="record"` returns `stopped: true`, **stop**. Summarise what was done and what you would
do next, and leave the decision to the user.

Hitting the budget halts the work; it never escalates it. A run that stops early having done
four sound things is a success, not a failure.

## Coming back

When the user returns, the handoff of an unattended run is the ledger, and it is short:

1. **What you decided, and why** — the `record` entries, in order.
2. **What it bought** — the metric, the measured gain, the artefact.
3. **What is still open** — the decision you stopped and waited for.
4. **What you would do next**, so they can say yes to one step instead of re-planning.

Do not dump the whole unattended run. Lead with the decisions, because those are the only part
that needs their judgement.

## A question is still a question

`away` does not mean "never surface anything". It means "do not **block** on a question nobody
can answer". If something genuinely needs them, say so in the final report and stop there — that
is the budget working, not the mode failing.

## Widgets, if the question is one

A question that `genui-scenarios` judges to be a real pending decision can be carried by a
widget — a picker, a form, a slider. This skill and `genui-scenarios` decide together, and
neither one alone is sufficient. When the user is away there is usually no one to click it, which
is itself an argument for auto-deciding tier 2 and recording the choice.

## Cross-references

- `kaggle-account-switch` and `experiment-launch` ask tier 2 questions about identity and cost.
- `handoff` asks a tier 3 question about where the document goes.
- `kaggle-competition-research` self-advances, but its plan is still written and still shown.
- `search-engine` is a tier 2 question: ask which engine when the user is here, use the default
  (**Google**) and record it when they are away. Direct navigation to a URL you already hold is
  exempt either way — there is no engine to choose.
- `genui-scenarios` decides whether an asked question is worth a widget.
- `../relationships.json` holds the `asks` edges and the `state` block, and
  `tools/check_plugin.py` keeps both honest.
