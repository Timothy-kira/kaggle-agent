---
name: log-monitor-visualizer
description: Use whenever the log-monitor workflow reaches the point of setting or showing the log fetch interval - which is a normal step of starting any monitored run, not something the user has to ask for a GUI. Renders the interval slider with a confirm button, the live applied value and what is being watched, as a mavis.widget.v1 widget. Load this and emit the widget; do not describe it in prose. Not for rendering log content itself; the business flow in kaggle-agent:log-monitor always reaches its result in text first.
---

# Log interval visualizer

This skill renders **one control**: the log fetch interval, as a slider with a confirm button
to its right. It is the GUI surface for the `kaggle-agent:log-monitor` business skill.

## What this widget does and does not do

It **does**:

- show the interval currently in effect, and what the monitoring subagent last reported;
- let the user pick a new interval with a slider;
- make the user confirm before anything is applied, so a stray drag cannot silently change a
  running job's polling cost;
- send exactly one prompt on confirm, carrying the new value.

It **does not**:

- write the config file. A widget has no filesystem or tool access. The prompt it sends is
  what causes the main agent to call `kaggle_log_monitor action="set"`, and that call is what
  actually changes the interval;
- fetch, parse, or display log content;
- claim the change is live until the config has been written. Say "will apply" before
  confirming, and report the applied value after.

That boundary is the point. The widget proposes; the tool decides; the subagent picks it up.

## When to render it

Render when the user is about to start or adjust a monitor and would rather set the interval
by slider than type a number. Do not render it:

- when reporting the interval as a fact, with no change pending;
- after a run reached a terminal state and monitoring is over;
- more than once per turn.

When the host cannot render a widget, the same control must still be usable as a sentence:
"Current interval is 120s. Set it to a value between 15 and 900 seconds (15s steps) and I will
apply it." The business skill works unchanged without this file.

## Required flow

1. Call `kaggle_log_monitor action="status"` and use **that** output for the data block. Never
   invent an interval, a revision, or a target list. `action="get"` is the same config for a
   subagent mid-run; either returns the applied interval, and the applied value is the only
   one the control may start from.
2. Emit exactly one `<mavis-widget>` using the `mavis.widget.v1` DSL as the final content of
   the final message, after all tool calls have finished. Do not call a tool after emitting
   it. Prose outside the widget is at most one short sentence before and one after.
3. Read the shared foundation at `../../_shared/genui-widget/FOUNDATION.md` and its linked
   references before writing the widget. The theme-token contract, the security rules, and
   the fallback contract are part of this skill, not optional reading.

The foundation is **shared, not copied**. There is one copy in this package at
`skills/_shared/genui-widget/`, and both visualizers read it. Do not fork another copy into
this skill; if the foundation needs a change, change the shared one so the two widgets cannot
drift apart.

## Component choice

Use the **interval slider** recipe from `references/components.md`, with
`kind="interactive"`. Chart.js is not used: a single scalar parameter is not a chart, and
`interactive` must stay CSS/SVG-only.

## Language

Both the `text` and the `userIntent` of every `sendPrompt` must be written in the
conversation's language. When the conversation is Chinese, both are Chinese - `userIntent` is
user-intent context and has no English-only exemption.

## States to cover

| State | Behaviour |
|---|---|
| Config not yet written | Show the default in effect and say it has never been set; the slider still works. |
| Slider moved, not confirmed | Show the pending value distinctly from the applied one, and enable the confirm button. |
| Slider at the applied value | Keep the confirm button visible but disabled, with a short "no change" hint. A disabled button is clearer than a confirm that silently does nothing. |
| No targets registered | Say nothing is being watched yet, and name the tool call that registers one. Do not imply a monitor exists. |
| Tool returned a clamp | Report the value actually applied, not the value requested. |
| Widget cannot render | The `<mavis-fallback>` block carries the same information and the same sentence a user could reply with. |

## What the confirm prompt says

One short, user-readable question naming the new value. Not a hidden instruction, not a
multi-step plan:

```
把 log 抓取间隔改为 45 秒
```

with metadata:

```javascript
window.mavis.sendPrompt('把 log 抓取间隔改为 45 秒', {
  widgetTitle: 'Log 抓取间隔',
  action: 'apply-interval',
  selectedData: { intervalSeconds: 45, previousSeconds: 120, revision: 1 },
  visibleFilters: { targets: 1 },
  userIntent: '修改 log 抓取间隔'
});
```

`selectedData` carries the new value and the previous one, so the main agent can report the
change without re-deriving what it was. Never place a token, a local absolute path, or the
config file's own path in prompt metadata.

## After the user confirms

The main agent calls `kaggle_log_monitor action="set" interval_seconds=<value>`, then reports
the value that actually took effect, including any clamp or 15-second snap. It does not tell
the user the change is live until the tool has confirmed it - a slider that moved is a
proposal, not a state change.
