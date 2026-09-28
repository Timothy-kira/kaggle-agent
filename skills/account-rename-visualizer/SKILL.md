---
name: account-rename-visualizer
description: Use whenever a saved Kaggle account is to be given a different name, or when the user asks what the accounts are called and any of those names is wrong or unclear - not only when they explicitly ask for a GUI. Renders a rename panel with a text input and confirm button as a mavis.widget.v1 widget, listing each account by its Kaggle username and proposing that username as the default new name. Load this and emit the widget; do not describe it in prose. Not for switching, adding or removing accounts, which the kaggle-account-switch business skill handles in text.
---

# Account rename panel

This skill renders **one small panel**: a text input for a new account name, with a confirm
button beside it. It is the GUI surface for renaming a saved Kaggle account.

## Scope, deliberately narrow

It renames. It does not:

- **switch** the active account. That is a different decision with different consequences, and
  it belongs in the account switcher;
- **add** an account, which needs a token;
- **remove** one, which destroys a credential and needs a confirmation, not a free-text field.

Folding those into one panel would give the user three unrelated mutations behind a single
confirm button, and a mistyped confirm would become ambiguous about which one they meant.

## What it does and does not do

It **does** show the current accounts, with the Kaggle username as the primary label, and let
the user type a new name for one of them.

It **does not** perform the rename. A widget has no filesystem access, and it must never
pretend otherwise. It sends one prompt; the main agent calls
`kaggle_accounts action="rename"`, and that call is what changes anything. A rename looks
instant but is not done until the tool confirms it.

**A rename never needs a token.** Renaming changes the label a user types, not the credential
behind it, so the panel must never accept, display, echo or transmit one — not in the input,
not in the DOM, not in prompt metadata. If a rename flow ever seems to need a token, it is
actually an add, and that belongs elsewhere.

## Why the username is the proposed default

The stored name is a handle; the Kaggle username is the identity. A list containing
`qwyi123` and `xishengfeng` tells you which account is which at a glance, while a list of
`work` and `personal` does not. So the input is pre-filled with the username, and the user
overwrites it only if they want a meaningful alias. It is a proposal, not a constraint.

When an account is already named after its username, the panel says so and does not suggest
a pointless rename.

## When to render it

Render when the user wants to rename an account and the change is the pending decision. Do
not render it:

- to report the account list, with no rename in mind - that is text;
- when the user has only one account and is happy with its name;
- more than once per turn;
- when the host cannot render widgets. Then ask in plain text: "Which account should be
  renamed, and to what?" The business skill works unchanged.

## Required flow

1. Call `kaggle_accounts action="list"` and build the panel from that output. Never invent an
   account, a username, or which one is active.
2. Emit exactly one `<mavis-widget>` using the `mavis.widget.v1` DSL as the final content of
   the final message, after all tool calls finish. Do not call a tool after emitting it.
3. Read the shared foundation at `../../_shared/genui-widget/FOUNDATION.md` and its linked
   references first. The theme-token contract, the security rules and the fallback contract
   are part of this skill.

The foundation is **shared, not copied**. There is one copy in this package at
`skills/_shared/genui-widget/`, and both visualizers read it. Do not fork another copy into
this skill; if the foundation needs a change, change the shared one so the two widgets cannot
drift apart.

## Component choice

Use the **rename panel** recipe from `references/components.md`, with `kind="form"`: a
text input and a confirm button is structured input collection, which is exactly what
`form` is for. No Chart.js - this kind does not load it.

## Language

The `text` and `userIntent` of every `sendPrompt` must be in the conversation's language.
When the conversation is Chinese, both are Chinese.

## States to cover

| State | Behaviour |
|---|---|
| One or more accounts | One row per account; the active one marked; the input offers that account's username as the default. |
| Only one account and its name already matches the username | Say so, and do not push a rename. A rename to the same value is a no-op, not a useful action. |
| Input left at the current name | Confirm disabled, with a "名称未改变" hint. |
| Input matches another account's name | Warn inline that the name is taken, and disable confirm. The tool would refuse anyway; catching it locally is faster feedback. |
| Illegal characters | Show the rule inline (letters, digits, dot, dash, underscore; must start with a letter or digit; max 64) while typing, and disable confirm. |
| No accounts stored | Empty state naming the tool call that adds one. Do not offer to rename nothing. |
| Tool refused a rename | Report the tool's reason verbatim; the main agent owns that conversation, not the widget. |

## What the confirm prompt says

One short, user-readable sentence naming the account and the new name:

```
把 Kaggle 账号 work 改名为 qwyi123
```

```javascript
window.mavis.sendPrompt('把 Kaggle 账号 work 改名为 qwyi123', {
  widgetTitle: '重命名 Kaggle 账号',
  action: 'rename-account',
  selectedData: { from: 'work', to: 'qwyi123', username: 'qwyi123' },
  visibleFilters: { accountCount: 2 },
  userIntent: '重命名 Kaggle 账号'
});
```

`selectedData` carries the old name, the new name and the username, so the main agent can
report the result without re-deriving it. Never place a token in prompt metadata.

## After the user confirms

Call `kaggle_accounts action="rename"` with `name` and `new_name`, then report the outcome.
A rename keeps the token, the username and the original added date, and the active pointer
follows the account it pointed at. Say that explicitly, because a user renaming an account
reasonably expects to be logging in somewhere else and is not.

If the tool refuses because the target name is taken or invalid, report that reason as-is. Do
not retry with a modified name on the user's behalf - a name is a label they chose, and
silently picking a different one is how the two identities drift apart.
