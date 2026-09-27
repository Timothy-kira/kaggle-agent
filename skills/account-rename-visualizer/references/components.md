# Component recipes

Domain-specific components for the Kaggle account-rename business result. The generic pieces —
value-and-apply, inline validation, status strip, provenance line — live in the shared library
at `../../../_shared/genui-widget/COMPONENTS.md` and are not restated here. This file records
only what is specific to account renaming.

---

## Rename panel

### Business question

"这个账号叫什么？我想把它改成什么？"

A rename is a single free-text decision about a single account. That makes it an input plus a
confirm, not a chart, a table, or a multi-select. The generic shape is the shared
**value-and-apply control**; what follows is what makes it *this* widget.

### Selection rule

Always this component when a rename is the pending decision. Do not add switching, adding or
removing controls to it: three unrelated mutations behind one confirm button make a
mistyped confirm ambiguous.

### Required input

From `kaggle_accounts action="list"`, no other source:

| Field | Use | Null policy |
|---|---|---|
| `accounts[].name` | the stored name, shown as the secondary label and used as `from` | required |
| `accounts[].username` | the Kaggle identity, the primary label, and the pre-filled `to` | when absent, pre-fill with the stored name |
| `accounts[].active` | marks the active account | required |
| `accounts[].renamed_from` | shown once as provenance so the user can find an old label | omit the line when absent |

### Widget kind and layout

`kind="form"`, `capabilities="resize,sendPrompt"`, no Chart.js.

```
┌────────────────────────────────────────────────────────────┐
│ 重命名 Kaggle 账号                                            │
├────────────────────────────────────────────────────────────┤
│ 选择账号                                                     │
│ ┌────────────────────────────────────────────────────────┐ │
│ │ ◉ work        当前使用 · Kaggle 用户名 qwyi123           │ │
│ │ ○ second      Kaggle 用户名 xishengfeng                  │ │
│ └────────────────────────────────────────────────────────┘ │
│ ┌──────────────────────────────────────┐  ┌──────────────┐   │
│ │ qwyi123                             │  │   确认改名     │   │
│ └──────────────────────────────────────┘  └──────────────┘   │
│ 名称可用 · 1-64 位字母、数字、点、连字符或下划线                 │
└────────────────────────────────────────────────────────────┘
```

Radio selection of the account, then a free-text input pre-filled with that account's
username, with the confirm button to its right. The input is the only editable thing.

### Local interactions

- **Account radio** — selecting an account re-fills the input with its username. Purely
  local, no prompt.
- **Typing** — validates inline against the same rules the tool enforces, and against the
  other accounts' names. Disables confirm on failure. No prompt while typing.
- **Confirm** — the only `sendPrompt`. Disabled when the name is unchanged, illegal, or taken.

The rules the widget checks are exactly the tool's `NAME_RE`: 1-64 characters, starting with
a letter or digit, then letters, digits, dot, dash or underscore. Duplicating the check
locally is what makes the feedback instant; the tool still validates and still has the final
say, because a widget is not a trust boundary.

### Reasoning follow-up

Exactly one, on the confirm button:

```javascript
window.mavis.sendPrompt('把 Kaggle 账号 work 改名为 qwyi123', {
  widgetTitle: '重命名 Kaggle 账号',
  action: 'rename-account',
  selectedData: { from: 'work', to: 'qwyi123', username: 'qwyi123' },
  visibleFilters: { accountCount: 2 },
  userIntent: '重命名 Kaggle 账号'
});
```

`text` and `userIntent` in the conversation language. No token, ever.

### State presentation

| State | Rendered as |
|---|---|
| Ready | Username pre-filled, confirm enabled when the value differs from the current name. |
| Name unchanged | Confirm disabled, "名称未改变". |
| Name taken by another account | `--mw-warning` inline note, confirm disabled. |
| Illegal characters | `--mw-danger` inline note stating the rule, confirm disabled. |
| Already named after the username | Confirm disabled, "名称与 Kaggle 用户名一致，无需改名". |
| Never renamed | No `renamed_from` line. |
| Once renamed | A quiet line under the account, so a user who filed a token under the old label can find it. |
| No accounts | Empty strip naming `kaggle_accounts action="add"`. |

### Prohibitions

- The widget must not perform the rename, and must not imply that it has.
- It must not accept, display or transmit a token. A rename never needs one.
- It must not offer switching, adding, or removing. Those are other decisions.
- It must not auto-send on radio change or on blur. Only the confirm button sends.
- It must not invent a name for an account whose username is unknown; it pre-fills the
  stored name in that case and says why.

---

## Account list block

### Business question

"我有哪些账号，哪个在用？"

The panel's context, not a separate widget. A list of accounts is a handful of rows, so it
renders as stacked radio rows rather than a table — a table would imply sortable columns of
data that do not exist.

Identity leads, stored name follows: `work  ·  Kaggle 用户名 qwyi123`. When the two are
identical, show the name once. A user needs to see both because they may have to type either
one, and the tool takes the stored name.
