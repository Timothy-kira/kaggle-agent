# identity — 身份与账号

**What this category answers:** who am I acting as, and whose quota is paying for it.

Almost every other action in this plugin runs under one of these accounts, so a wrong answer
here is not a small problem — it silently spends the wrong budget and submits under the wrong
identity.

| Skill | Use it when |
|---|---|
| [`kaggle-cli`](../kaggle-cli/SKILL.md) | Any Kaggle call from this machine: sign in, list, push, pull, check status, read logs. The tool reference, and the rule that a token pasted in chat is still saved. |
| [`kaggle-account-switch`](../kaggle-account-switch/SKILL.md) | More than one account exists and you need to know which is active, or the active one is wrong. Covers the naming rule: **lead with the Kaggle username**, keep a user-chosen alias as the stored name. |
| [`account-rename-visualizer`](../account-rename-visualizer/SKILL.md) | An account needs a different stored name. Renders the rename panel. **Load and emit it — do not describe it in prose.** |

## The naming model, in one paragraph

An account has two names and they do different jobs. The **Kaggle username** is the identity —
it is what every user-facing list leads with, because seeing `qwyi123` tells you exactly which
account it is and seeing `work` does not. The **stored name** is the handle every tool call
uses, and it may be an alias you chose. When adding an account you normally pass only the
token: the name is derived from the username. They coincide often enough that a list of
usernames is the clearest thing to show.

## The rule that causes the most confusion

Adding an account needs a token, and the token is the user's decision, not yours. Ask for it
and use `kaggle_accounts action="add"`; do not invent a name for it, and do not route the user
to a terminal to avoid the conversation. If a token does arrive in chat, it is saved — that is
what was asked — and the only consequence worth one sentence is that it now sits in the
transcript.

## Cross-references

- `experiment-launch` depends on this category: it selects which account pays for a run.
- `genui-scenarios` decides *when* an account picker is warranted at all, and this category
  decides what it offers.

## Bound edges (rendered from `../relationships.json`)

These rows are **rendered from `skills/relationships.json`**, the single source of truth for
how the skills in this package relate, and for the live state those relationships are gated
on. `tools/check_plugin.py` fails the build if this table drifts from that file, so the graph
cannot rot back into loose prose. To change a relationship, edit `relationships.json` and
re-run the checker.

Edge types: `needs` = prerequisite, `dispatches` = launched at runtime, `produces` = this
skill's output is the other's input, `browses` = must actually open this source, `asks` =
presence-mode decides whether to ask here, `enforces` = a tool validates this, `loops` = a
read-then-decide cycle gated by the tool, `widget`/`gates` = the GenUI binding.

Where two mechanisms legitimately touch the same skill, the split of labour is declared under
`divisionOfLabour` in the same file, and the checker refuses a duplicated claim that has no
such declaration.

| From -> type -> To | When this edge is live |
|---|---|
| `kaggle-account-switch` -> needs `kaggle-cli` | every list, switch and status call is a kaggle_accounts / kaggle-cli call | <!-- edge:kaggle-account-switch->kaggle-cli:needs --> |
| `kaggle-account-switch` -> dispatches `account-rename-visualizer` | a stored name has to change, not just be re-labelled | <!-- edge:kaggle-account-switch->account-rename-visualizer:dispatches --> |
| `account-rename-visualizer` -> needs `kaggle-account-switch` | it renders that skill's decision point and owns no decision of its own | <!-- edge:account-rename-visualizer->kaggle-account-switch:needs --> |
| `kaggle-competition-research` -> needs `kaggle-cli` | slug, leaderboard, forum and kernels-list all go through the CLI tools | <!-- edge:kaggle-competition-research->kaggle-cli:needs --> |
| `experiment-launch` -> needs `kaggle-cli` | quota, accelerator market state and the push itself | <!-- edge:experiment-launch->kaggle-cli:needs --> |
| `experiment-launch` -> needs `kaggle-account-switch` | whose quota pays for this run is decided before anything is pushed | <!-- edge:experiment-launch->kaggle-account-switch:needs --> |
| `genui-scenarios` -> gates `kaggle-account-switch` | decides whether picking an account warrants a picker at all | <!-- edge:genui-scenarios->kaggle-account-switch:gate --> |
| `presence-mode` -> asks `kaggle-account-switch` | picking which account is a cheap reversible call when the user is away, and a question when they are present | <!-- edge:presence-mode->kaggle-account-switch:asks --> |

## Bound edges (rendered from `../../relationships.json`)

These rows are **rendered from `skills/relationships.json`**, the single source of truth for
how the skills in this package relate, and for the live state those relationships are gated
on. `tools/check_plugin.py` fails the build if this table drifts from that file, so the graph
cannot rot back into loose prose. To change a relationship, edit `relationships.json` and
re-run the checker.

Edge types: `needs` = prerequisite, `dispatches` = launched at runtime, `produces` = this
skill's output is the other's input, `browses` = must actually open this source, `asks` =
presence-mode decides whether to ask here, `enforces` = a tool validates this, `loops` = a
read-then-decide cycle gated by the tool, `widget`/`gates` = the GenUI binding.

Where two mechanisms legitimately touch the same skill, the split of labour is declared under
`divisionOfLabour` in the same file, and the checker refuses a duplicated claim that has no
such declaration.

| From -> type -> To | When this edge is live |
|---|---|
| `kaggle-account-switch` -> needs `kaggle-cli` | every list, switch and status call is a kaggle_accounts / kaggle-cli call | <!-- edge:kaggle-account-switch->kaggle-cli:needs --> |
| `kaggle-account-switch` -> dispatches `account-rename-visualizer` | a stored name has to change, not just be re-labelled | <!-- edge:kaggle-account-switch->account-rename-visualizer:dispatches --> |
| `account-rename-visualizer` -> needs `kaggle-account-switch` | it renders that skill's decision point and owns no decision of its own | <!-- edge:account-rename-visualizer->kaggle-account-switch:needs --> |
| `kaggle-competition-research` -> needs `kaggle-cli` | slug, leaderboard, forum and kernels-list all go through the CLI tools | <!-- edge:kaggle-competition-research->kaggle-cli:needs --> |
| `experiment-launch` -> needs `kaggle-cli` | quota, accelerator market state and the push itself | <!-- edge:experiment-launch->kaggle-cli:needs --> |
| `experiment-launch` -> needs `kaggle-account-switch` | whose quota pays for this run is decided before anything is pushed | <!-- edge:experiment-launch->kaggle-account-switch:needs --> |
| `genui-scenarios` -> gates `kaggle-account-switch` | decides whether picking an account warrants a picker at all | <!-- edge:genui-scenarios->kaggle-account-switch:gate --> |
| `presence-mode` -> asks `kaggle-account-switch` | picking which account is a cheap reversible call when the user is away, and a question when they are present | <!-- edge:presence-mode->kaggle-account-switch:asks --> |
