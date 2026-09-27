---
name: log-monitor
description: "Use when an experiment or notebook run is producing logs that need watching - on Kaggle or locally - and the user wants it tracked instead of the main agent polling. Uses MiniMax Code's built-in scheduled task (定时任务 / cron self) as the heartbeat, because a subagent is one turn and cannot wait: it reports and returns. Covers the tick body, re-reading the fetch interval every tick so a GUI change applies live, and the only three conditions that justify interrupting the main agent - an error in the log, the run reaching a terminal state, or a decision that needs the user - plus the rule that the cron deletes itself on any of them. Also covers when NOT to set up monitoring at all, and the sleep-the-machine caveat."
---

# Watching a run's log without spamming anyone

A long run produces a log nobody can read continuously. The failure this skill prevents is
either of the two dull ones: the main agent polls in a tight loop, burns its own context on
repetitive "still running" updates, and misses the one line that mattered; or nobody
watches, and a run that died at 00:04 is not noticed until morning.

So the fetching is delegated, and the reporting is filtered. The mechanism is **MiniMax Code's own
scheduled tasks (定时任务)**, not something this plugin reinvents: each tick is a fresh turn and
the runtime's scheduler holds the loop, so nothing has to stay awake. Load the built-in `mavis`
skill and read its cron reference for the contract — `cron self` for a periodic external-state
re-check, `cron once` for a single future turn, `cron create` for recurrence the user asked for.
**This skill does not restate that contract**, because a second copy of an official document is a
second copy that goes stale. What follows is only what is specific to watching a Kaggle log.

## This workflow is complete without any widget

Everything below works in plain text, and that path must stay intact. The slider in
`kaggle-agent:log-monitor-visualizer` is the **default** way to settle the interval when the
host can render widgets; the text path is the fallback, not the reverse.

## Before starting: is monitoring even worth it?

Not every run needs a watcher. Skip it when the run finishes in minutes, when the user only
wants the final output and not the progress, or when they have not asked. Starting a monitor
for a 90-second job is more machinery than the job.

Start one when the run is long enough that silence is ambiguous: a multi-hour training run,
a queued job waiting on a GPU, or anything where "no news" could mean "died quietly".

## Step 1: the target is already registered

**Both launchers register the run themselves**, so there is usually nothing to do here:

| Engine | What the launcher attached |
|---|---|
| `kaggle_kernel_launch` | the kernel ref it just pushed |
| `kaggle_local_launch` | the log file it is capturing the run's output into |

Confirm with `kaggle_log_monitor action="get"`. Call `action="target"` yourself only for a log
something else is writing - a second machine, a process you did not start through a launcher:

```
kaggle_log_monitor action="target" kind="kaggle" ref="<owner/slug>"
kaggle_log_monitor action="target" kind="local"  path="<path/to/log>"
```

Register both when a run has a cloud notebook and a local log - they are watched by the same
loop, and a failure in either is worth reporting. Re-registering the same ref replaces
the existing entry, so re-registering after a re-push does not cause a double fetch.

## Step 2: settle the fetch interval — render the control

The interval is a real choice, not a default to pick silently. Get the current value first,
then present it:

```
kaggle_log_monitor action="get"
```

**When the host supports widget rendering, this step is a widget and you must emit it.** Load
`kaggle-agent:log-monitor-visualizer`, then emit exactly one `<mavis-widget>` inline as the
final content of your final message, after every tool call has finished. Do not call a tool
after emitting it. Do not describe the control in prose instead of rendering it — a sentence
about a slider the user cannot see is not a slider.

Build the widget from the `action="get"` output above. Never invent an interval, a revision, or
a target list; the widget transforms that result and fetches nothing itself.

The control must offer **both** a slider and a direct number field, bound in both directions —
a slider alone cannot express an exact value and is awkward from a keyboard. Typing commits on
blur or Enter rather than per keystroke. Assemble it from the shared components in
`../_shared/genui-widget/COMPONENTS.md` rather than writing a fresh control.

Trade-off to state plainly when it matters: a short interval catches a fast crash sooner but
spends more requests and can itself hit Kaggle API rate limits; a long interval is cheap but
a run that fails immediately stays silent for that long. For a run expected to take hours,
something in the 2-5 minute range is the reasonable middle.

Without a widget-capable host, say the interval in a sentence with its range and ask for a
new value:

```
kaggle_log_monitor action="set" interval_seconds=120
```

The value is clamped to 15-900s and snapped to 15s steps; the tool reports the applied value,
so tell the user what actually took effect rather than what was requested.

## Step 3: give the loop a heartbeat — the built-in scheduled task

**A subagent is one turn.** It can check once, report, and return. It cannot wait for a log to
change, because nothing wakes it. Measured on this machine: a watcher subagent ran 6.6 minutes,
reported, and ended `succeeded` — it was not killed, it finished. Nothing in the brief changes
that, because the primitive has no heartbeat. (For the record: there is also no time-based abort —
of 33 subagent tasks the longest that succeeded ran 18.3 minutes, and the ones that ended
`aborted` were explicitly cancelled, not cut off.)

**Use the built-in scheduled task.** Load the `mavis` skill and follow its cron reference; the
call shape is `mavis({ command: "cron self", args: { cron_name, every, prompt } })`, and
`cron self` is the one meant for external state with no completion signal — which is exactly what a
notebook run is. The runtime owns the loop, every tick is a new turn, and `quiet_on_skip` keeps a
tick with nothing to say from messaging anyone.

This skill only supplies the tick body, because that part is specific to a Kaggle log:

```
Check the run registered in kaggle_log_monitor (action="get"), then read its log.
Still running, nothing wrong → exit quietly; say nothing.
Log shows an error      → report it, and delete this cron.
Run reached a terminal state → report the outcome, settle the experiment node, delete this cron.
Log unreadable         → say so once, keep this cron for two more ticks, then delete it.
Never poll between ticks, and never report progress that is only progress.
```

Why each piece is there:

- **`every`** — the tick interval. Pick it from the run's horizon: a 3-minute notebook check is
  reasonable, a 6-hour training run does not need 3-minute ticks. `kaggle_log_monitor
  action="get"` still returns the user's `intervalSeconds`; use it as the *read* cadence inside a
  tick, and change it with `action="set"` or the slider.
- **`action="get"` at the top of every tick** — it re-reads the interval from disk each call,
  which is what makes a slider change take effect. Report the revision you last saw, so "did my
  change take effect" is answerable from evidence.
- **silent ticks** — a tick with nothing to say writes a progress block and exits without
  messaging. That is the behaviour you want.
- **delete the cron** — on an error, a terminal state, or after two unreadable ticks. A loop
  with no exit condition is a leak that bills API calls forever. Manage it with `cron list` /
  `cron get` / `cron delete`; `cron trigger` runs one immediately if the user asks.

**Also say this to the user:** scheduled tasks depend on the desktop app running, and a machine
that sleeps or shuts down may miss a tick. For a run that matters, that is worth saying out loud
rather than discovering at hour six.

### A subagent is still the right tool for one thing

If the watch is short and the whole answer fits in a couple of minutes — confirming a run
actually started, catching an immediate crash — a short `explore` subagent is lighter than
scheduling anything. Launch it with a self-contained brief, in its own words and not by reference
to this conversation:

- the ref or path to watch, and whether it is Kaggle or local;
- that it calls `kaggle_log_monitor action="get"` at the start of every cycle and sleeps the
  returned `intervalSeconds` between fetches;
- that it must never cache the interval from its first read;
- the three reporting conditions below, verbatim;
- what to do when it cannot read the log at all.

The subagent has no parent context. A brief that says "watch the run we discussed" produces a
subagent that watches nothing. **And it will return when it has nothing left to report** — that is
the design, not a fault, so scope it to a bounded wait.

## Step 4: report only on the three conditions

A tick stays silent otherwise. Not quiet in the chat - absent. Progress that is
merely progress is not news.

**Report when:**

1. **The log contains an error.** An exception, a traceback, a failed assertion, an OOM
   kill, a non-zero exit. Report the first meaningful error line and enough surrounding
   context to locate it, not the whole log.
2. **The run reached a terminal state** - stopped, ended, or completed. Report the state,
   the elapsed time, and where the output landed. This is the end of monitoring; tell the
   main agent to delete the cron.
3. **A decision is needed that the user must make** - a budget question, an ambiguous
   failure with two plausible causes, a point where the next step depends on intent. Say
   the question and what the options are. Do not decide unilaterally and do not ask the user
   directly; that is the main agent's job.

**Do not report:** a successful step, a metric that looks normal, a changed ETA with no
decision attached, a repeat of a line already reported, or "still running" on its own.

Silence is the default state, and a watch that reports nothing for an hour has behaved
correctly.

### Reporting a log error

Give the main agent what it needs to act, not the raw tail:

```
ref:        user/exp-n7
state:      running
first seen: 2026-09-27T06:20Z
error:      CUDA out of memory at step 4200 (tried to allocate 2.41 GiB)
context:    3 lines before: batch size 64, seq len 4096
suspected:  the seq-len bump from n7 is the likely cause
```

If the same error appears on consecutive cycles, report it once. A monitor that re-reports
the identical failure every 15s has become noise.

## Step 5: the main agent's side

When a tick reports, act on the condition:

- **Error** - read the logs yourself to confirm, then decide: fix and re-push, reduce the
  offending memory or batch dimension, or stop and record the node as refuted on the
  experiment tree. If the run must be killed, `kaggle_kernel_retire` backs it up first.
- **Terminal** - pull the output (`kaggle_kernels_output` for Kaggle, read the file for
  local), record the result on the experiment tree, and **delete the cron** if the tick did not
  already.
- **Decision** - put the question to the user, with the options and a recommendation. This
  is the only condition where the user is interrupted by a question, so it has to be worth
  answering.

After any of the three, the run is over or paused: **delete the cron** rather than leaving it
polling a dead ref. `cron list` shows what is still watching, and `cron delete` stops it.

### When the user is not watching

This reporting step follows `presence-mode` (an `asks` edge in `../relationships.json`). Read
`kaggle_presence action="get"` before deciding how loudly to report.

**Present** — report all three conditions as written. A terminal state is news they want now,
and the "silence is healthy" rule only means *don't report progress*, not *don't report
outcomes*.

**Away** — a terminal state auto-advances instead of waiting: pull the output, record the result
on the experiment tree, and if the run errored, apply the obvious fix or record the node as
refuted. Then record the decision so the user can audit it:

```
kaggle_presence action="record"
  decision="run errored OOM; recorded node as refuted instead of waiting"
  rationale="unattended, the node had to resolve either way; the tree now carries the evidence"
```

**Auto-advancing still means reading the tree first.** The next node cannot be recorded without a
fresh `kaggle_experiment_tree action="read"`, because the last one changed it. That gate is the
point: an unattended run is more likely to drift onto a stale base precisely because nobody is
watching, so the read is what keeps an overnight loop honest.

An error is **not** auto-fixed by guessing a different architecture. Auto-advance means: record
the result faithfully, choose the reversible next step, and say what you chose. When a result
invalidates the approach rather than the run, the next node is a **`research` node** naming the
source you went back to — not a fabricated ablation.

**Still report, and still stop, away or not:** a decision that spends the last of the quota, and
anything that retires a kernel. If `action="record"` returns `stopped: true`, stop polling and
wait — a monitor that keeps going past its budget is the thing this mode exists to prevent.

## Local runs

A local run has no ref and no quota, so the mechanics differ but the reporting rules do not:

- watch the log file, reading only the bytes appended since the last read;
- a crashed process, a non-zero exit, or a traceback in the file is an error report;
- the process exiting is a terminal report;
- there is no `kaggle_kernel_verify` step, because there is no accelerator grant to verify.

For a local run under a background process, report the pid and the command so the user can
act on it without asking.

## Do not

- Do not poll in the main agent while the cron is also polling.
- Do not restart the loop just to change the interval; write the config and let the next
  cycle pick it up.
- Do not keep a monitor alive after a terminal state; it becomes an API cost with no purpose.
- Do not report a metric or a milestone unless a decision depends on it. Progress is not an
  event.
- Do not treat "the log is quiet" as an error. Silence is the expected healthy state, and the
  most common false alarm in log monitoring is escalating a quiet run.

## When the interval keeps getting changed

Repeated slider changes usually mean the initial value was wrong for the run - a queue that
turned out to be long, or a fast-failing job. If the user edits the interval more than
about three times, say so and ask what the run is actually doing, rather than letting them
keep tuning a number that is compensating for a different problem.
