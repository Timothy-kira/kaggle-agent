# Shared component library

Patterns both `kaggle-agent` visualizers use, so a widget is assembled from named pieces
instead of being rewritten each time. The foundation next door (`../genui-widget/`) owns the
protocol; this file owns the shapes that turned out to recur.

Two rules:

- **Compose from these, don't re-derive them.** The two visualizers already share the value /
  apply / validate skeleton, the status-strip and the disabled-confirm behaviour. A third
  widget should reuse them too.
- **These are patterns, not a library.** The widget DSL has no imports. Copy the CSS block
  and the script into the widget, adapt the fields, and keep the validation rules intact.

---

## 1. Value-and-apply control

The backbone of both existing widgets: a displayed current value, a way to change it, and a
confirm that stays disabled until the change is real and valid.

```
┌──────────────────────────────────────────────┐
│ 当前值  120 秒        provenance line        │
│ ┌───────────────────────┐  ┌──────────────┐   │
│ │ [ slider | text input ] │  │  确认         │   │
│ └───────────────────────┘  └──────────────┘   │
│ hint line (why disabled / what will happen)   │
└──────────────────────────────────────────────┘
```

- **Current value is always visible and always labelled "current"**, separately from the
  pending value. Merging them is the single most misleading thing a settings widget can do,
  because the user cannot then tell what is in effect from what they just typed.
- **Confirm is the only `sendPrompt`.** Typing, dragging and selecting update local state only.
- **Confirm is `disabled`, not hidden, when there is nothing to apply.** A missing button
  looks like a bug; a greyed-out one with a reason reads as state.

## 2. Manual entry alongside a slider

**Never make a slider the only way to set a value.** A slider forces approximate reading, is
useless for exact numbers, and is awkward for anyone using a keyboard. Pair it with a text
input and keep the two bound in both directions.

```
<input type="range">  ←→  <input type="number">
```

Binding rules:

- Drag or type in the number field → the slider follows, clamped to the bounds.
- Type in the number field → **do not snap silently while typing.** Clamp on `change` (blur or
  Enter), not on every keystroke, otherwise typing "1" on the way to "120" gets rewritten to
  the minimum and fights the user.
- Show the unit inside or beside the field, and make the field's `aria-label` carry it, so the
  value is never ambiguous.
- The number field may accept any integer the tool allows; the **tool still clamps and snaps**,
  and reports what it actually applied. The widget's job is fast entry, not being the
  authority.

```javascript
// slider -> number, always
slider.addEventListener('input', function () { num.value = slider.value; sync(); });

// number -> slider, on commit only (change fires on blur/Enter, not per keystroke)
num.addEventListener('change', function () {
  var v = parseInt(num.value, 10);
  if (isNaN(v)) { sync(); return; }            // restore the last good value
  num.value = String(clamp(v, MIN, MAX));
  slider.value = num.value;
  sync();
});
```

## 3. Inline validation

Validate locally for speed, but the tool remains the authority. The widget's job is to make an
obviously-wrong value impossible to submit, not to replace server-side checking.

| Rule type | Check in the widget | Why |
|---|---|---|
| Range | `MIN <= v <= MAX` | Outside the range the value is meaningless |
| Format | pattern, e.g. `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` | Catches typos before a round trip |
| Uniqueness | value is not another item's name | A collision is refused by the tool anyway |
| No-op | `v === current` | Nothing to apply; disable confirm |

Three states, all worth building: **valid** (accent border, confirm enabled), **invalid**
(`--mw-danger`, reason, confirm disabled), **taken** (`--mw-warning`, names the other item,
confirm disabled). Distinct colours matter — "you typed something impossible" and "someone
else already has that" call for different corrections.

## 4. Status strip

Small read-only state, for a condition that has no action attached.

```
[ chip chip ]   or   [ one muted sentence ]
```

- Chips for a list (watched targets, accounts, skills).
- One muted sentence when the list is empty, naming the tool call that would populate it. An
  empty container with no explanation reads as a rendering failure.
- Not clickable when there is no per-item action. A chip that looks clickable and is not is
  worse than plain text.

## 5. Provenance line

Where the displayed value came from, in `--mw-text-subtle` and small:

```
revision 3 · last changed 2026-09-27T06:16:40+00:00
```

Include it whenever a value can change outside the widget, because the widget may be showing
state from before someone else's edit. Without it, a stale panel looks authoritative.

## 6. States every settings widget must cover

| State | Behaviour |
|---|---|
| First render, never set | Say the default is in effect; the control still works |
| Unchanged | Confirm disabled, "no change" hint |
| Invalid | Reason inline, confirm disabled |
| Applied | Main agent reports the value the tool actually stored, including any clamp |
| Unsupported host | `<mavis-fallback>` carries the same information and the same sentence a user could reply with |

The last row is not optional. A widget that is the *default* path still needs a working
text fallback, or an unrenderable widget silently removes a capability.

## 7. What a reusable structure must not become

- **Not a fetch layer.** The widget transforms a result the business flow already produced.
- **Not the authority.** Clamping, snapping, uniqueness and persistence belong to the tool.
- **Not a second code path.** Two ways to set the same value is how "what I typed" and "what is
  applied" drift apart.
- **Not a second copy of the foundation.** One shared foundation, one change, both widgets
  correct.
