---
name: log-monitor
description: "Use when an experiment or notebook run is producing logs that need watching - on Kaggle or locally - and the user wants it tracked instead of the main agent polling. The watch follows the run rather than a clock: each tick feeds the log it read to kaggle_log_monitor action='observe', and the log's own content decides whether the tick is news and how soon the next one comes, so a run that wakes up is watched closely again. An unreadable log is treated as a route problem - action='attempt' hands back the next way to read it and remembers the one that works, and that route belongs on the node. Uses MiniMax Code's built-in scheduled task (定时任务 / cron self) as the heartbeat, because a subagent is one turn and cannot wait: it reports and returns. Covers the tick body, the three conditions that justify interrupting the main agent - an error in the log, the run reaching a terminal state, or a decision that needs the user - plus the rule that the cron deletes itself on any of them. Also covers when NOT to set up monitoring at all, and the sleep-the-machine caveat."
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
1. kaggle_log_monitor action="get"        # targets, watch rules, cadence, repair progress
2. read the run's log, using the route that worked last time (see below)
   could not read it → kaggle_log_monitor action="attempt" ok=false reason="..."
                      → it hands back the next route; take it and try again THIS tick
   read it          → kaggle_log_monitor action="observe" text="<the tail>"
                      → it says which rules matched and what the cadence should do
   error / terminal / decision → report it, settle the node, delete this cron
   nothing          → say nothing
3. kaggle_log_monitor action="tick" verdict=<observe's verdict>
4. cron update schedule=<the interval it returns>
```

**A tick reads the log; it does not consult a clock to decide whether that mattered.**
`action="observe"` is what makes that true: the log's own content decides both whether this
tick is news and how soon the next one comes. Pass it what you read and it answers
`changed`, names the rules that matched, and returns a verdict:

| verdict | what it means | what to do |
|---|---|---|
| `tighten` | the log moved since the last tick — the run is doing something | watch closely, exit quietly |
| `hold` | one quiet check; a run can go quiet mid-training | same cadence, exit quietly |
| `relax` | quiet twice running — genuinely steady | stretch the interval, exit quietly |

**A log that moved snaps the ladder back to its tightest rung.** That is the point: a run
that wakes up in the middle of the 20-minute steady state is interesting again, and a monitor
that keeps polling on schedule without noticing that is just a timer with logging.

## An unreadable log is a route problem, not an ending

The old behaviour — say it once, then delete the cron after two failures — threw away a run
that was probably fine. A log you cannot read usually means the *route* is wrong, not the run:

| route | tool | when it is the one that works |
|---|---|---|
| `logs` | `kaggle_kernels_logs` | the normal case |
| `status` | `kaggle_kernels_status` | the kernel is queued or starting, so no log exists yet |
| `output` | `kaggle_kernels_output` | outputs land before the log endpoint answers |
| `ref` | `handoff_status` | the ref is not the one this run was launched under |

`action="attempt" ok=false reason="..."` walks that list and tells you the next one. Try it in
the same tick. When one finally works, `action="attempt" ok=true recipe="<step>"` says so —
and **that step belongs on the node**, so the next run starts from the route that worked
instead of rediscovering it. Only when `exhausted` is the watch over: say so plainly, delete
the cron, and leave the reason on the node. The run itself is not over; only the watching is.

**The cadence is a ladder, and it is the tool's, not yours:**

| rung | check every | when |
|---|---|---|
| 1 | **1m** | the run is starting; is it even doing the thing |
| 2 | **3m** | it started; is it on the right track |
| 3 | **5m** | shape is visible |
| 4 | **10m** | it is plainly running |
| 5 | **20m** | steady state — leave it here |

`action="tick" verdict=...` moves the ladder according to what the log did and hands back the
interval to re-arm with, so the schedule follows the run instead of a guess you made once. A
run that needs 6 hours does not need 720 reads of an endpoint with nothing to say. `action="get"`
shows which rung you are on. **Registering a run resets the ladder to the first rung.**

**Delete the cron when the watch is over.** That is not tidy-up, it is the exit condition: a
loop with no exit is a leak that bills API calls forever. Delete it when `observe` reports
something, or when `attempt` says every route to the log has failed. `cron list` shows you
what is still watching, and `kaggle_log_monitor action="clear"` retires the stale targets.

**The slider still works, and it overrides the ladder.** `kaggle_log_monitor action="set"`, or the
GUI from `log-monitor-visualizer`, changes the fetch interval a tick reads *within* its rung. The
slider is for "check faster than the ladder would" — a run that is about to fail is worth
30-second reads. It is not for pacing; the ladder owns that.

Why each piece is there:

- **`action="get"` at the top of every tick** — it re-reads from disk each call, so a slider
  change takes effect on the very next tick. Report the revision you last saw, so "did my change
  take effect" is answerable from evidence.
- **silent ticks** — a tick with nothing to say writes a progress block and exits without
  messaging. That is the behaviour you want.
- **`action="observe"` + `action="tick" verdict=...`** — the rung is arithmetic, and arithmetic an
  agent does in its head is arithmetic that drifts. Keeping it in the tool also keeps the *input*
  honest: the cadence is computed from the log's own content, not from a count the agent kept.

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
- that after every read it calls `action="observe" text=<the tail>` and re-arms with
  `action="tick" verdict=<what observe returned>` — the cadence follows the log, not a guess;
- the three reporting conditions below, verbatim;
- that an unreadable log is a **route** problem, not an ending: call
  `action="attempt" ok=false reason=...`, take the route it hands back, and try again; on
  success call `action="attempt" ok=true recipe=<step>` so the working route is remembered.

The subagent has no parent context. A brief that says "watch the run we discussed" produces a
subagent that watches nothing. **And it will return when it has nothing left to report** — that is
the design, not a fault, so scope it to a bounded wait.

## Step 4: report only on the three conditions

A tick stays silent otherwise. Not quiet in the chat - absent. Progress that is
merely progress is not news. `action="observe"` decides this from the log's content, and its
`report` list is the authority on what is worth interrupting someone for.

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
