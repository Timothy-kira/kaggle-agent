# Visual Rules

Generic visual rules for all `mavis-widget` iframes rendered inside Mavis.

## Non-negotiables

- Stay local-first: deterministic or user-provided data, no login, no remote APIs, no external tile servers.
- Prefer flat surfaces over glossy decoration. No gratuitous gradients, blur, glow, or shadow.
- Use sentence case everywhere.
- Use two text weights at most: `400` and `500`.
- Never render unreadable UI: minimum 12px text, dark-mode-safe contrast, obvious selected states.
- Key values must be visible in the layout, not only in hover tooltips. Screenshots must remain interpretable.

## Streaming rules

The `mavis-widget` sub-blocks stream in order: style, html, data, script.

- `<mavis-style>` should be short. Style the structure so it looks correct on first paint.
- `<mavis-html>` should show visible structure immediately: cards, chart containers with explicit height, section headers.
- Avoid hidden tabs, carousels, or `display: none` sections during streaming. Stack content vertically until post-render script behavior kicks in.
- Canvas containers must have wrapper divs with explicit height; do not set the canvas height directly in CSS.
- Avoid heavy decoration that flickers in partial renders.

## Typography

- Typical scale: `18` for widget title, `14` for body and labels, `12` for secondary text, `24-28` for KPI values.
- Font family: always `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif` for text. `'JetBrains Mono', monospace` for code or data values when appropriate.
- Do not use `600` or `700` weight for body text. Reserve `500` for emphasis, `700` only for KPI headline numbers.
- Do not bold text mid-sentence inside the widget.
- Round every displayed number. Use `Intl.NumberFormat` for locale-aware formatting.

## Layout

- Use CSS Grid or Flexbox. No floats.
- KPI row: `grid-template-columns: repeat(N, 1fr)` where N = number of KPIs.
- Chart container: wrapper div with explicit height, canvas inside.
- Dashboard: grid with named areas or explicit column spans.
- No `position: fixed`; it breaks auto-height inline rendering.
- Avoid nested scrolling when a stacked layout can work.
- The widget container should feel natural at full message width. Do not add gratuitous wrapper shells.

## Surfaces

- Use `var(--mw-surface)` for card backgrounds. `var(--mw-surface-muted)` for subtle backgrounds like table headers.
- Border: `1px solid var(--mw-border)`.
- Border radius: `10-12px` for cards, `6px` for inner elements.
- No drop shadows by default. If needed, use `0 2px 8px rgba(0,0,0,0.04)` max.
- Hover: `transition: opacity 0.15s` or subtle background change. No translate or scale effects.

## General do/don't

Do:
- Make every interaction visually inspectable.
- Expose key numbers outside tooltips.

Don't:
- No emoji as icons. Use SVG paths or geometric marks.
- No gradients, drop shadows, blur, glow, or neon effects.
- No dark outer backgrounds unless the specific scene requires it.
- No `border-left` accent cards with rounded corners.
- No external script or asset origins beyond Chart.js CDN.
- No comments in markup and CSS; they waste tokens and slow streaming.
- No `text-transform: uppercase` or `letter-spacing` on labels. Use sentence case as-is.
- Never reference specific color names ("blue bar", "red line") in widget text or notes. Theme tokens resolve to different colors in light vs dark mode. Describe by meaning instead: "highlighted bar", "primary series", "accent color".

## Interaction and state rules

- Every required interaction should change something the reviewer can see: a chart series, table rows, highlighted segment, inspector panel, or metric readout.
- Keep filtering, sorting, toggling, and simple recomputation in front-end code. Do not bounce every small interaction back to the model.
- Use `sendPrompt()` only when the next step benefits from the model reasoning over the user's selection.

### Required states

Every widget should handle:

- **Working**: the normal data-populated state.
- **Loading**: show the layout frame with muted placeholders. KPI cards show `--`, chart containers show a subtle background.
- **Empty**: keep the layout intact. Explain why no data is shown in a single centered line.
- **Error**: preserve the surrounding layout. Show a short inline message, not a full-page error.

## Dark mode

- Every widget must work on both light and dark backgrounds because the host can switch at any time.
- All text, background, and border colors come from `--mw-*` tokens — they automatically adapt.
- Chart.js colors come from `window.mavisTheme.tokens` and must be updated on `mavis:theme-change`.
- If you use fixed hex for data series, ensure they are readable on both light and dark surfaces.

## Quick QA checklist

- Does the first paint already show a coherent structure?
- Are the data source and values deterministic?
- Would the widget still be readable in the opposite theme?
- Are all numbers rounded?
