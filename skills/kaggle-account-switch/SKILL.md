---
name: kaggle-account-switch
description: Use when the user has more than one Kaggle account, asks to switch, add or rename a Kaggle account, wants to know which Kaggle account is active, or when a Kaggle call fails with 401/403 and the wrong account may be signed in. Covers naming rules - lead with the Kaggle username, keep a user-chosen alias as the stored name - the kaggle_accounts MCP tool, the kaggle-cli account commands, and rendering an account picker or a rename panel.
---

# Switching Kaggle accounts

The plugin holds several named accounts and exactly one is active. Every Kaggle tool uses the
active one, so switching is a single step that changes what every later call does.

## Naming an account

An account has **two names**, and keeping them straight is what makes a list readable:

- the **stored name** is the handle every tool call uses. It may be an alias the user chose.
- the **Kaggle username** is the identity, stored beside the token.

**Lead with the username whenever you talk to the user.** A list of `qwyi123` and
`xishengfeng` tells you which account is which; a list of `work` and `personal` does not. When
the stored name differs from the username, show both - the user may have to type either one,
and the tool takes the stored name:

```
* qwyi123  (存储名: work)      当前使用
  xishengfeng  (存储名: second)
```

When they are identical, show the name once. Do not write `qwyi123 (qwyi123)`.

**Do not invent a name for the user.** When adding an account, pass only the token and let
the name derive from the username:

```
kaggle_accounts action="add" token="<ACCESS_TOKEN>" username="<kaggle username>"
```

Only pass an explicit `name` when the user asked for a specific one. A made-up label such as
`work` throws away information the store already had.

## Renaming

```
kaggle_accounts action="rename" name="<old>" new_name="<new>"
```

A rename changes the stored name in place: the token, the username and the original added date
are all preserved, and the active pointer follows the account it pointed at. So renaming the
active account does not log the user out or change which Kaggle identity is used.

The tool refuses to rename onto an existing name, rather than merging two accounts and
silently dropping one credential. If it refuses, report the reason and let the user pick
another name - do not retry with a modified one on their behalf.

Use the `kaggle-agent:account-rename-visualizer` skill when the user wants a different name for
an account. **When the host supports widget rendering, that skill is the default path and you
must emit its widget** — load it, then emit exactly one `<mavis-widget>` inline as the final
content of your final message, after every tool call has finished, with no tool call after it.
Do not describe the panel in prose instead of rendering it. Build it from
`kaggle_accounts action="list"`; never invent an account, a username, or which one is active.
Assemble it from the shared components in `../_shared/genui-widget/COMPONENTS.md` rather than
writing a fresh control. The name is entered as **free text** — there is no slider here,
because an account name is not an ordered quantity and a slider would imply one exists.

That panel only renames. Switching, adding and removing stay in this skill, in text. Without a
widget-capable host, ask in plain text: "Which account should be renamed, and to what?"

## Check first

Call `kaggle_accounts` with `action: "list"`. It returns every saved account with the active one
marked, the Kaggle username, and the previous name when the account has been renamed. It never
returns a token.

```
* qwyi123  (存储名: work)
  xishengfeng  (存储名: second)
```

Then choose the response by **what is actually blocking the user**, not by how many accounts exist:

| State | What to do |
|---|---|
| **Not signed in** — no accounts, or `kaggle_auth_status` says not configured | **Render the account picker in "add" mode.** Authentication is the blocker; ask for the token and save it with `kaggle_accounts action="add"`. |
| One account, active, and everything works | Do nothing. Say nothing about accounts. |
| One account, but a call returns 401/403 | The token is probably expired or revoked. Say so, offer to re-add it. |
| Two or more accounts, and the user asked to switch, or the wrong one is active | **Render the account picker in "switch" mode** with every account listed. |
| Two or more accounts, and the active one is already correct | Do nothing. Do not make the user choose what they already chose. |
| The user wants a different name for an account | Rename it, or render the rename panel. |

## Switching

```
kaggle_accounts  action: "use", name: "<account>"
```

Report the result in one line. There is no need to re-run the operation that failed.

## Offering the choice in a picker

When a decision is genuinely pending, present it as a form so the user clicks instead of typing.
There are two modes, and the mode follows the blocking state:

**When the user is not watching.** This decision point follows `presence-mode` (an `asks` edge
in `../relationships.json`). Check `kaggle_presence action="get"` **before** rendering a picker
or stopping to ask.

- **Present** — render the picker. The user is there to click it.
- **Away** — do not block. Picking an account is cheap and reversible, so auto-decide: use the
  **active** account, or the one with quota headroom if the active one is empty. Record it with
  `kaggle_presence action="record"`, then continue. A picker rendered for an absent user is
  just a stall.
- **Never auto-decide:** adding an account (it needs a token only the user can supply), removing
  one (it deletes credentials), or renaming one (it rewrites how every later call refers to it).
  Those are tier-3 and stay questions in either mode.

**"Add" mode — not signed in.** Show a sign-in state, the exact command, and the warning about
tokens. The only real action is getting them to run the command; do not offer account names that do
not exist yet.

```
kaggle-cli login --as <name> <ACCESS_TOKEN>
```

**"Switch" mode — several accounts exist.** Read them with `kaggle_accounts` `action: "list"`, then
render a `kind="form"` widget whose options are exactly those account names, each showing its
username and the active one marked.

Rules for both:

- Every option calls `sendPrompt` with the account name and a clear instruction.
- No option is preselected.
- A click only becomes real when its `sendPrompt` produces a new user message. Do not treat
  rendering as consent, and do not auto-send.
- Put the full account list in `<mavis-fallback>` so the choice still works if the widget does not
  render.
- If the host cannot render widgets, ask in plain text instead: "Send me your Kaggle access token
  and I'll save it as <name>" or "Which account: work or research?"

## Adding an account

`kaggle_accounts` with `action: "add"` and a token saves the account in the conversation, with
no terminal step. The Kaggle username is optional, but passing it both labels the account and
supplies the default name:

```
kaggle_accounts action="add" token="<ACCESS_TOKEN>" username="<kaggle username>"
kaggle_accounts action="add" name="<alias>" token="<ACCESS_TOKEN>" username="<kaggle username>"
```

Use the first form unless the user asked for a specific name. `action: "add"` also activates
the account, so a newly added one becomes the active one.

The command line is an alternative for users who prefer not to paste a token into the
conversation. It writes to the same store:

```
kaggle-cli login --as <name> <ACCESS_TOKEN> [kaggle-username]
kaggle-cli use <name>
kaggle-cli rename <old> <new>
```

Then verify with `kaggle_accounts` `action: "list"`. If a token did arrive in chat, save it
as asked and mention once that it now sits in the transcript, so the user knows rotating it
is available.

## Removing

`kaggle_accounts` with `action: "remove"`, `name: "<account>"`. Confirm first: it deletes the
stored token and cannot be undone. If the removed account was active, the tool falls back to
another one automatically.

## When a call fails with 401 or 403

1. `kaggle_accounts` `action: "list"` - is the expected account active?
2. If not, and there is more than one, offer the picker.
3. If the right account is active and it still fails, the token is likely expired or revoked:
   ask the user to sign in again with the command above.

## Notes

- `KAGGLE_API_TOKEN` in the environment overrides the saved selection, so a shell export can
  silently pin a different account. `kaggle_auth_status` shows which source is in play.
- Accounts live in `~/.kaggle-agent/accounts.json`, one file, owner-readable. Adding an account
  never touches the plugin package. A store at the previous `~/.kaggle-cli/accounts.json` is
  migrated across on first read, name for name, so a renamed account is never resurrected as a
  duplicate.
