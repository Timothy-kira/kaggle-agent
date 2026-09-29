---
name: genui-scenarios
description: Use when a Kaggle workflow reaches a point where the user has to choose - which account, which execution engine, where a handoff should live, which search engine - or when reporting quota, accelerator availability or account state. Decides whether a widget is warranted at all, and which one. Prevents rendering a fixed panel that no current situation called for, and prevents reporting a state as if it were a choice.
---

# When to render a widget, and which one

A widget is a response to a decision the user actually has to make right now. It is not a
summary panel, and it is not part of a fixed layout. If no decision is pending, the answer is
plain text.

## The test, before rendering anything

Ask: **what decision is in front of the user at this moment?**

- A decision is pending → a widget may carry it, with the real current data.
- No decision is pending → plain text. Do not render quota, do not render an account list, do not
  render a "choose an engine" card for a run that does not exist yet.

Rendering a panel "because it is useful" is the failure this skill exists to prevent. A widget
that arrives with no pending decision is noise, and the user cannot tell it apart from a real
prompt.

## The binding table: which skill emits which widget

This is the contract. Each row is a business skill, the decision point inside it, the widget
that decision produces, and the tool call that supplies the widget's data. **A decision point
with a widget is not optional** — the business skill's own text carries the same instruction,
so neither file depends on the other being read.

| Business skill | Decision point | Widget | `kind` | Data from |
|---|---|---|---|---|
| `kaggle-account-switch` | renaming an account, or "what are these accounts called" | `account-rename-visualizer` | `form` | `kaggle_accounts action="list"` |
| `log-monitor` | settling the fetch interval when starting a watched run | `log-monitor-visualizer` | `interactive` | `kaggle_log_monitor action="status"` |
| `presence-mode` | whether the user is at the keyboard or has stepped away | *no widget yet — a sentence in text* | — | `kaggle_presence action="get"` |
| `handoff` | where the next agent runs (same machine vs another) | *no widget yet — offer as a form in text* | — | `handoff_status` |
| `experiment-launch` | which account pays / engine / accelerator | *no widget yet — offer as a form in text* | — | `kaggle_quota`, `kaggle_accounts` |
| `ruler-audit` | which measurement surface, and accepting the noise floor as the run's floor | *no widget yet — a sentence in text* | — | `kaggle_experiment_tree action="calibrate"` |
| `kaggle-competition-research` | anything mid-sweep | **never a widget** — a sweep is not a choice | — | — |
| `rsi-experiment-tree`, `approach-decision`, `kaggle-cli`, `github-auth` | state reports | **never a widget** — these report, they do not offer | — | — |

Two rules that the table exists to enforce:

- **A business skill with a decision point owns the emission.** It must say "emit a
  `<mavis-widget>`" itself, not "consider offering a GUI". That wording is what made widgets
  silently not appear in earlier versions of this plugin.
- **A skill without a widget row is not permitted to grow one quietly.** If a new decision
  point appears, either build its visualizer properly — shared foundation, components, schema,
  example, static review — or answer in text. There is no third state of describing a panel
  in prose that the user never sees.

### The shape every one of these widgets shares

The foundation is shared at `../_shared/genui-widget/`, and the recurring component shapes at
`../_shared/genui-widget/COMPONENTS.md`. Every widget in this plugin is assembled from:

- a **value-and-apply control** — current value shown and labelled as current, separate from
  the pending value, with a confirm that is disabled rather than hidden when there is nothing
  to apply;
- **manual entry beside any slider**, bound both ways, committing on blur rather than per
  keystroke — a slider alone is never the only way to set a value;
- **inline validation** for range, format and uniqueness, with distinct colours for
  "impossible" versus "taken";
- a **status strip** and a **provenance line** so a panel cannot look authoritative while
  showing stale state.

A new visualizer composes these. It does not re-derive them, and it does not copy the
foundation.

## The scenarios

| Situation | Render | Show |
|---|---|---|
| **Not signed in at all** (no accounts, or `kaggle_auth_status` reports not configured) | **Account picker, in "add" mode** | A clear "sign in first" state and the `kaggle_accounts` action that resolves it. This is the moment the user most needs to be told what to do. |
| An experiment is queued and about to launch | Engine picker | Cloud / local, with the quota fetched **at that moment** |
| Two or more accounts exist **and** a switch is actually needed | Account picker, in "switch" mode | Every account, active one marked, never a token |
| A competition starts, or a handoff checkpoint has arrived | **Handoff picker** | Where the next agent runs, not a list of features. See below. |
| The user says they are leaving, or picks the work back up | **Nothing** | Plain text. "You're away — I'll advance and record the calls I make; the budget is 6." A presence toggle is a mode change, not a choice between options. |
| The research sweep reaches the code and paper stage | **Nothing** | Plain text. There is no search engine to pick *for the known sites* — `deep-research` runs the general search, and GitHub, Hugging Face and arXiv are coverage requirements, not options. |
| A **discovery** search is about to run and the user is present | **Engine picker, in text** | The two engines with their real trade-offs: Google (default) or Bing (faster to paint, and pins `setlang`/`cc` on the URL). This *is* a pending decision — the engines index differently — so it earns a question. |
| Reporting quota, accelerator state, or a finished run | **Nothing** | Plain text. A number does not need a form. |
| "What can this machine sync?" / auth or capability question | **Nothing** | Plain text. `github_auth action=status` output is already a report, not a choice. |

### Reporting state is not offering a decision

The most common misuse is turning a status answer into a form. If the user asked *what is
the state*, the state is the answer - render nothing. A widget here invites a choice that
was never on the table, and the user has to work out that the options are decorative.

Concretely, these are **text**:

- remaining quota and refresh date
- which account is active, when nobody asked to switch
- whether a notebook used the accelerator it asked for
- whether this machine can reach GitHub, and by which transport

These are **widgets**, because a real choice is pending: signing in, switching account,
picking an execution engine, choosing where the handoff goes.

The tell: if every option in your widget leads to the same next action, it is not a
decision. Delete it and answer in prose.

### Presence decides whether there is anyone to click

`presence-mode` and this skill decide together, and neither is sufficient alone
(they are bound by `needs` and `gate` edges in `../relationships.json`).

**Read `kaggle_presence action="get"` before rendering a picker.** If the user is **away**, do
not render a decision widget for a tier-2 decision — the account to use, the engine to search with,
the run to launch. There is nobody to click it, so a picker in away mode is not a convenience, it
is a stall. Take the recorded default instead, which is what `presence-mode` prescribes.

A tier-3 decision — anything irreversible, or "no handoff" versus "push it" — is still asked, and
still rendered, in either mode, because it needs an answer rather than a default. That is the
one case where a widget rendered during an away run is correct: it is the record of a question
that has to be answered before anything irreversible happens.

### The handoff picker asks where the next agent runs

Only render it when a handoff checkpoint has actually arrived, and never on the first
message about a competition unless the work is likely to outlive the session. The question
that decides the widget is not "do you want a handoff" - the user already knows a handoff
exists - it is **where the next agent is running**:

- **Same machine, different agent** → the local file is already enough. Say so; do not
  offer a GitHub setup to someone who does not need one.
- **Different machine** → needs a remote. Run `handoff_status` first; if it reports no
  transport available, the honest option is "set up sync first" or "stays local".
- **No handoff** → a legitimate answer. Do not re-ask at the next checkpoint.

`handoff` has no visualizer yet, so this is offered as a short form in text: the three
options above as a numbered choice, with the same reasoning. When a `handoff-visualizer` is
built later it must be registered in the binding table above first.

Show whether a handoff already exists, and where. A user who has been handed one does not
need to be offered a second one.

### The account picker has three states, not two

Render it whenever authentication is the thing standing between the user and their goal:

1. **Not signed in** → the primary action is adding an account. `kaggle_accounts` with
   `action: "add"` takes a name and a token directly, so the user never has to leave the
   conversation to sign in. If they would rather not paste a token here, give the terminal
   line as the alternative - both paths work, and the choice is theirs.
2. **One account, and it is the wrong one** or the call still 401s → say the token is
   probably expired and offer to replace it.
3. **Two or more accounts** → the account list, with the active one marked, and a switch
   action per account.

Never render the account picker when authentication is fine and nobody asked to switch. One working
account is not a decision.

## Non-negotiables

- **Fetch the data at render time.** If the widget shows quota, call `kaggle_quota` in the same
  turn. A cached number presented as current is worse than no widget.
- **No speculative options.** Every button must lead to an action that is valid right now. If the
  run is not staged yet, do not offer "run on Kaggle" as if it could start this second.
- **No default selection.** The user picks; the choice only becomes real when their click produces
  a new message. Never render a preselected option, and never auto-send.
- **One widget per decision.** Do not combine the account picker, the engine picker and a quota
  dashboard into one panel. They are separate moments and the user answers them separately.
- **Language follows the conversation.** All visible text and all `sendPrompt` messages in the
  language the user is writing in.
- **Every widget embeds its own text fallback.** If it cannot render, the same options must still be
  readable and selectable in plain text.
- **Only the documented tokens.** Theme-sensitive styling uses the host's `--mw-*` tokens with no
  hardcoded fallback colours, and scripts go in the widget's script block, never inline handlers.
- **Follow-up text stays short.** One or two sentences around the widget; the substance goes inside
  it. Do not restate the widget's contents as a table outside it.

## If a widget would not help

Some results are better as text: a rule quoted from a forum, a leaderboard table, an error the
user needs to read carefully, a decision with two options where one sentence is enough. Say it in
prose. A widget is a convenience, not a default.
