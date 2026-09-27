# Component recipes

Domain-specific components for the `log-monitor` business result. The generic pieces —
value-and-apply, manual entry beside a slider, inline validation, status strip, provenance
line — live in the shared library at `../../../_shared/genui-widget/COMPONENTS.md` and are not
restated here. This file records only what is specific to log monitoring.

---

## Interval control

### Business question

"What interval is the monitor fetching at, and what do I want it to be?"

A single scalar with a confirm step, so it renders as a control plus a confirm button — not a
chart, not a table. The generic shape is the shared **value-and-apply control**; what follows
is what makes it *this* widget.

### Selection rule

Always this component when the fetch interval is the pending decision. There is no second
candidate: a chart of one number is noise, and the value has to be adjustable, not merely
reported.

### Required input

From `kaggle_log_monitor action="status"`, no other source:

| Field | Use | Null policy |
|---|---|---|
| `intervalSeconds` | applied value; both inputs start here | required |
| `minSeconds` / `maxSeconds` / `stepSeconds` | bounds and step for **both** inputs | required, constants 15 / 900 / 15 |
| `revision` | shown as a quiet provenance marker | default 0 when never set |
| `updatedAt` | "last changed" line | omit the line entirely when null |
| `targets` | count of watched logs | empty renders an explicit empty state |
| `exists` | distinguishes "default in use" from "explicitly set" | false → show the default marker |

### Widget kind and layout

`kind="interactive"`, `capabilities="resize,sendPrompt"`, no Chart.js.

```
┌──────────────────────────────────────────────────────────┐
│ 当前间隔  120 秒            revision 1 · 最后修改 06:16 UTC│
│ ┌────────────────────────┐ ┌──────┐ ┌────────────┐      │
│ │ 15 ──────●──────── 900 │ │ 120  │ │  确认应用    │      │
│ └────────────────────────┘ └──────┘ └────────────┘      │
│  15              450              900                    │
│ 待应用 45 秒                                             │
│ 确认后由主 Agent 写入配置，subagent 下一轮即生效            │
│ ──────────────────────────────────────────────────────── │
│ 正在监控 1 个  [Kaggle user/exp-n7]                        │
└──────────────────────────────────────────────────────────┘
```

Slider, then a number field, then the confirm button — the slider and the button share a row,
per the layout the user specified, with the number field between them.

### Local interactions

- **Slider drag** — updates the number field and the pending label live. Purely local, no
  prompt.
- **Type in the number field** — commits on blur or Enter, **not per keystroke**, then clamps
  to the bounds and moves the slider. This is the shared **manual entry** pattern, and it is
  not optional: a slider alone forces approximate reading, is useless for an exact figure, and
  is awkward from a keyboard.
- **Confirm click** — the only `sendPrompt`. Disabled when the pending value equals the
  applied value, with a "no change" hint, rather than a click that would do nothing.
- **Tick labels** — min, mid and max on the track, so the range is locatable without hovering.

Nothing else is interactive. Do not add a "reset to default" button: it is a second mutation
path for a value the user can already set, and two ways to change one number is how the
applied value and the intended value drift apart.

### Reasoning follow-up

Exactly one, on the confirm button:

```javascript
window.mavis.sendPrompt('把 log 抓取间隔改为 45 秒', {
  widgetTitle: 'Log 抓取间隔',
  action: 'apply-interval',
  selectedData: { intervalSeconds: 45, previousSeconds: 120, revision: 1 },
  visibleFilters: { targetCount: 1 },
  userIntent: '修改 log 抓取间隔'
});
```

`text` and `userIntent` in the conversation language. `selectedData` carries the new and
previous values so the main agent can report the delta without re-reading anything. No token,
no absolute path, no config file path.

### State presentation

| State | Rendered as |
|---|---|
| Working | Applied value in the header, slider at the applied position, button enabled. |
| Pending | Pending value labelled 待应用 in `--mw-accent`; applied value still visible above it. |
| Unchanged | Button disabled, `--mw-text-subtle` hint "与当前一致". |
| Never set | `exists:false` → header reads "当前使用默认值 120 秒". |
| No targets | `--mw-surface-muted` strip: "尚未注册监控目标" plus the tool call that registers one. |
| Clamped by the tool | Applied value shown, with a note that the request was adjusted. |

### Prohibitions

- The widget must not write the config file. It has no filesystem access and must not imply
  it does.
- It must not fetch the log, or any other data, at render time.
- It must not claim the change is in effect before the tool has confirmed it. Before
  confirmation the wording is "待应用"; after it, the main agent reports the applied value.
- It must not place an absolute log path or any credential in the DOM or in prompt metadata.
  A local target is displayed by basename only.
- No `submitForm`. The confirm button is a `sendPrompt` action.

---

## Watched targets strip

### Business question

"Is anything actually being watched, and what?"

Shown under the slider so the interval is not read in isolation — an interval on a monitor
with no targets is a setting for nothing.

### Required input

`targets[]` from the same status call. Display `ref` for Kaggle targets, basename for local
ones.

### Rendering

One flat row of small chips, or a single `--mw-surface-muted` strip when empty. Not a table:
there is never more than a handful, and a table would imply columns of data that do not exist.

### Prohibitions

Not clickable — there is no per-target action to take from here, and a chip that looks
clickable but is not is worse than plain text. The subagent's report is what a target's state
comes from, and that belongs in the conversation, not this widget.
