# Shared authoring material

Read-only reference for every `kaggle-agent` visualizer. **Not a Skill** — it is not registered
in `plugin.json` and must not be loaded by name. It exists so two widgets cannot drift apart.

```
_shared/genui-widget/
  SKILL.md        the mavis.widget.v1 protocol, theme tokens, security rules  (forked once)
  references/     color-system, visual-rules, widget-patterns, widget-recipes (forked once)
  COMPONENTS.md   the component shapes these two widgets actually share
```

## Why one copy

The foundation is ~21 KB per copy. Forking it into each visualizer doubled that and created a
silent failure mode: a fix to the theme or security rules would land in one widget and not the
other, and the divergence would only show up as a visual bug in one place.

So it is forked **once, here**, and both visualizers reference it by relative path:

| Visualizer | Path to the foundation |
|---|---|
| `skills/experiment/log-monitor-visualizer/` | `../../_shared/genui-widget/SKILL.md` |
| `skills/identity/account-rename-visualizer/` | `../../_shared/genui-widget/SKILL.md` |

**Do not fork another copy.** If the foundation needs changing, change it here.

## Why `COMPONENTS.md` exists separately

The foundation teaches the protocol — it is generic and would be the same in any plugin. What
these two widgets actually share is narrower and more useful: a current-value-versus-pending-value
control, a confirm that is disabled rather than hidden, manual entry beside a slider, inline
validation, a status strip, and a provenance line.

Those are recorded here so the third widget is assembled rather than rewritten. The protocol
tells you how to write a widget; `COMPONENTS.md` tells you that you have already written this
one twice.

## The binding contract

Which skill emits which widget, at which decision point, is owned by
`skills/collab/genui-scenarios/SKILL.md`. That table is the single source of truth, and each
business skill repeats the instruction in its own body so neither file depends on the other
being read.
