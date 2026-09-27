---
name: experiment-launch
description: Use when about to start a real notebook run - on Kaggle or locally - and the engine, accelerator, time limit or account still has to be chosen, or when a run needs to be monitored, resumed after it stops, or its quota checked. Covers the pre-run choice, the 12h ceiling, live log following, and checkpoint-resume semantics.
---

# Launching and supervising a run

Every run has the same four decisions: which engine, which account, which accelerator, how long.
Get them wrong and the run wastes quota or dies silently. The account's quota is the input that
constrains all four, so read it first.

## Before launching: know the budget

```
kaggle_quota                  # remaining GPU and TPU hours, and the refresh date
kaggle_accounts action=list   # which account is active
```

Read the numbers, do not assume them. A 12h run needs 12h of remaining quota on the accelerator it
requests; if less is left, either shorten the run or wait for the refresh. Record the quota you
started from on the experiment node, so a later failure is attributable.

## Ask the user where to run it

The engine is a real trade-off, not a default, so offer the choice rather than picking silently.
Present it as a form with the two options and the current quota on each:

- **Kaggle cloud** - real accelerator, quota-bounded, needs the notebook staged and pushed, the
  result has to be pulled back. Use it when the job needs a GPU or TPU, or when it should survive
  this machine stopping.
- **Local** - no quota, immediate, full log access, but only as good as the local hardware. Use it
  for short runs, for validating a change before spending quota, and when the work is
  CPU-bound.

If the user already said, do not ask again. Ask only when the choice is genuinely open, and put
the safe default first in the options.

### When the user is not watching

This decision point follows `presence-mode` (an `asks` edge in `../relationships.json`). Check
`kaggle_presence action="get"` **before** you stop to ask.

**Present** — ask, using the two options above.

**Away** — do not block on it. Auto-decide and record:

- engine: **local** for a short or CPU-bound run, since it costs no quota and is trivially
  reversible; **Kaggle** only when the job genuinely needs an accelerator.
- time limit: never more than the remaining quota can absorb, and never a 12h run unattended.

Record it, so the user can audit the reasoning on their return:

```
kaggle_presence action="record"
  decision="launched locally instead of asking about the engine"
  rationale="CPU-bound, costs no quota, and is reversible"
```

**Still ask, away or not:** anything that would retire a kernel, or a run that would consume most
of the remaining quota. `away` lowers the ask threshold for *decisions*; it never authorises
anything irreversible. And if `action="record"` comes back `stopped: true`, stop and wait.

## Launching on Kaggle

```
kaggle_kernel_launch
  folder           = <dir with notebook.ipynb and kernel-metadata.json>
  timeout_seconds  = <= 43200
  accelerator      = gpu | tpu | none
  account          = which saved account pays (default: the active one)
```

`kaggle_kernel_launch` refuses a `timeout_seconds` above 43200 and reports the clamped value
instead of silently accepting it. **12h is the platform maximum for one notebook run** - a longer
job has to checkpoint and resume, not ask for more time.

**Pick the account deliberately.** With several accounts saved, every launch consumes one
account's quota, and the wrong one can silently exhaust a budget you meant to keep. If more than
one account exists and the user did not say which, ask - do not assume the active one.

Accelerator choice: one accelerator for a single long run. Request more GPUs only when the job
parallelises across devices. Prefer TPU for large tensor work that maps to it; otherwise GPU.

## Verify the launch, or the quota is wasted silently

**This is not optional.** The `kernels push --accelerator` flag alone is **not honoured** by the
platform - a kernel pushed that way comes back with no accelerator. The tool therefore also writes
`enable_gpu` / `enable_tpu` into `kernel-metadata.json`, but even that has been observed to not
stick on some runs.

So after every launch:

```
kaggle_kernel_verify  ref=<owner/slug>, expected=<gpu|tpu|none>
```

It reads the kernel's live record and reports `match` or `MISMATCH`. A `MISMATCH` means the job is
running on CPU: it burns no accelerator quota, but it also does not do the work, and it will sit
there looking alive. Fix the metadata and re-launch; do not leave it.

## Retiring a kernel without losing it or leaking quota

A kernel that errors keeps existing, and an existing kernel keeps consuming quota. Before
deleting anything, take the backup; after deleting, confirm the deletion took effect.

```
kaggle_kernel_retire  ref=<owner/slug>, backup_dir=<local folder>
```

The tool, in order:

1. records the current status;
2. `kernels pull` the source and `kernels output` the artifacts into `backup_dir`, plus a
   `RETIRED.json` manifest - **before** the delete, never after;
3. `kernels delete`;
4. re-queries `kernels status` and `kernels list` to **confirm** the kernel is really gone.

Step 4 is the one people skip. `delete` returning success does not guarantee the kernel stopped
existing, and a kernel that survives deletion keeps spending the account's quota. When the tool
reports `NOT CONFIRMED`, treat the quota as still at risk: retry, or escalate.

Use `dry_run: true` first to see what would happen. `skip_backup` exists but should only be used
for a kernel worth nothing - the backup is what makes a retired run reproducible.

## Monitoring: log while it runs, not after

A run that produces no output for an hour is indistinguishable from a hung run. Follow it.

**Do not poll in this turn.** Delegate the fetching to a subagent and let it report only on
the three conditions that actually matter. The full procedure — the subagent brief, the live
interval, and the reporting rules — is in the `log-monitor` skill; follow it rather than
re-deriving it.

The short version:

1. `kaggle_log_monitor action="target"` to register the run's kernel ref (and any local log).
2. Settle the fetch interval with `kaggle_log_monitor action="set"`, or offer the slider GUI
   from the `log-monitor-visualizer` skill.
3. Launch a background `explore` subagent that re-reads `kaggle_log_monitor action="get"` at
   the top of every cycle and reports **only** on: an error in the log, a terminal state, or
   a decision the user has to make.
4. React to the report, and stop the subagent once the run is over.

Do not also poll from the main agent while that subagent is live — two pollers on one run
means duplicate reads and doubled API spend.

## Resuming after a stop

A stopped run is not a finished run. `kernels push` against the **same folder** updates the same
notebook and starts a new version - that is the resume path, not a new experiment.

- Keep the workspace and `kernel-metadata.json` byte-identical between attempts. Changing the `id`
  creates a different notebook and orphans the previous run's logs.
- Checkpoint inside the notebook: write intermediate state to `/kaggle/working` and check for it at
  startup. Without a checkpoint, a resume replays from zero and the 12h budget buys nothing.
- **Record the version number** returned by each push on the experiment node. The tree then shows
  which attempts a single node consumed, which is what makes a 12h-plus-12h result interpretable.
- If the run failed, `kaggle_kernels_logs` first, fix the cause, then re-push the same folder.

## On the experiment tree

Every launch is a node: the change it tested, the engine, the account that paid, the accelerator
requested, the accelerator **actually verified**, the timeout, the quota at launch, the ref and
version, and where the logs and artifacts landed. A run that is not on the tree cannot be compared
to anything later, and its cost cannot be attributed.

Record the requested accelerator *and* what `kaggle_kernel_verify` reported. They can differ, and
the difference is the finding.

### The tree enforces the loop, so use it

`rsi-experiment-tree` is a validated DAG, not a convention, and two of its rules change how you
work here:

1. **Read before you record.** `kaggle_experiment_tree action="read"` returns a `readRevision`, and
   `action="record"` refuses a missing or stale one. So after every completed run, before choosing
   the next experiment, the tree must be read again — the tool will not let you plan the next node
   from a version you remember.
2. **A node is rejected without a hypothesis, a metric carrying the parent's number, and a real
   reason.** "It got better" is refused. If you cannot state why the run should have helped, the
   hypothesis was not really stated.

When a result shows your picture was wrong, record a **`research` node** rather than fudging an
ablation: it names the source you went back to (`forum`, `code`, `web`, `paper`, `model`,
`dataset`, `rules`, `leaderboard`) and what it `opens` for a later experiment. The tree is the
record that you re-read a source on purpose.


## What to tell the user after a launch

One line: what is running, on which engine and accelerator, the time limit, the quota it consumed
from, and the ref to watch. Then report again when the state changes - not on every poll.
