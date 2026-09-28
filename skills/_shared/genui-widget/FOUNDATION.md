---
name: genui-widget
description:
  'GenUI widget authoring infrastructure — provides the <mavis-widget> DSL protocol, security
  constraints, --mw-* theme token system, Chart.js/CSS/SVG rendering patterns, and sendPrompt
  interaction mechanism. Business skills (data-visualize, financial-analysis, etc.) compose on top
  of this to output widgets. Not directly triggered — loaded by reference from business skills.'
packageRole: >-
  Shared authoring reference, forked once from minimax/genui-widget@mavis.widget.v1
  and read by every kaggle-agent visualizer through a relative path.
  This is not a registered Plugin capability and is not loaded as a Skill by name.
  See skills/_shared/README.md and skills/_shared/genui-widget/COMPONENTS.md.
  This file is named FOUNDATION.md, not SKILL.md, and must keep that name: SKILL.md is
  how the package names a capability, so a file with that name is a capability whether or
  not plugin.json lists it, and the marketplace rejected this package for
  UNREFERENCED_CAPABILITY while it was still called SKILL.md. The frontmatter below is
  provenance, not registration.
---

# GenUI Widget

## What this skill is

This is a **tool skill**, not a business skill. It does not respond to user triggers directly.
Instead, business skills (e.g. `data-visualize`, `financial-analysis`) reference this skill for the
DSL protocol, security rules, theme tokens, rendering patterns, and interaction mechanisms needed to
output `<mavis-widget>` blocks.

If the user explicitly invokes `/genui-widget`, treat it as a compact data-visualization request and
produce the widget yourself using this contract. Do not refuse or explain that this is only an
infrastructure skill.

## Output routing contract

Widgets are output **directly in `msg_content` text** — the host streaming parser picks up
`<mavis-widget>` blocks from the text channel and renders them inline as themed, sandboxed iframes.

- Do NOT use the `write` tool to save widget DSL to a file — files on disk are invisible to the
  rendering pipeline. This includes "drafting" the widget to a temp file first and then copying it
  into `msg_content`; that workflow produces a duplicate file artifact the user never asked for.
- Do NOT create workspace directories or HTML files for the visualization.
- Even when the widget is large (200+ lines), output it directly inline in your response text. The
  streaming parser handles arbitrarily long widgets.
- The host renders widgets as interactive iframes with Chart.js, theme sync, and clickable
  drill-down — directly in the conversation.

## Final answer contract

The final assistant message must be concise and render-ready:

- Output exactly one complete `<mavis-widget>...</mavis-widget>` block by default. Do not output
  draft widgets, replacement widgets, or multiple alternative widgets unless the user explicitly
  asks for multiple separate views.
- Use this response shape: optional one-sentence intro, then the widget, then optional one-sentence
  conclusion. Total prose outside the widget should be no more than two short sentences.
- Never output `<think>`, `</think>`, hidden reasoning, planning notes, implementation walkthroughs,
  tool transcripts, validation logs, or model self-talk in `msg_content`.
- Do not duplicate widget content outside the widget as Markdown tables, long bullet lists, or a
  second written analysis. Put the analytical substance inside the widget and its fallback.
- If you realize the first widget design is wrong before sending, fix it in place and send only the
  corrected final widget.

## Theme safety gate

This gate constrains theme values only. The model still generates the layout, visual hierarchy,
component composition, and interaction presentation that best fit the user's data and task.

Use only these host-defined tokens in theme-sensitive CSS:

`--mw-bg`, `--mw-surface`, `--mw-surface-muted`, `--mw-text`, `--mw-text-muted`,
`--mw-text-subtle`, `--mw-border`, `--mw-accent`, `--mw-success`, `--mw-danger`, `--mw-warning`,
`--mw-chart-1` through `--mw-chart-6`, `--mw-chart-grid`, `--mw-chart-axis`.

Do not invent `--mw-*` aliases such as `--mw-fg`, `--mw-muted`, `--mw-bg-soft`, `--mw-card`,
`--mw-primary-bg`, or `--mw-warn-bg`. Do not add fallback values to `var(--mw-*)`; use
`var(--mw-text)`, never `var(--mw-text, #111)`. Do not use a fixed light or dark palette for
background, text, border, SVG, chart, hover, focus, selected, or disabled states. Hardcoded colors
are allowed only for rare data-specific semantic marks when no host token exists.

Before emitting a Widget, scan every `--mw-*` reference and every theme-sensitive color declaration
against this gate. An opening tag with `theme="app"` does not make invalid CSS theme-safe.

## Kind catalog

| Kind          | Purpose                                                                                             |
| ------------- | --------------------------------------------------------------------------------------------------- |
| `chart`       | Single chart or chart pair for trend, comparison, distribution, or correlation (Chart.js available) |
| `dashboard`   | KPI cards plus one or more charts plus optional table or inspector (Chart.js available)             |
| `map`         | Spatial data view with geographic or regional overlay and linked analytics                          |
| `diagram`     | Concept flows, architecture graphs, SVG/HTML diagrams with light interactivity                      |
| `interactive` | Parameter controls that recompute a metric in real time (CSS/SVG/Canvas only, no Chart.js)          |
| `form`        | Structured input collection                                                                         |
| `mockup`      | UI mockup or wireframe preview                                                                      |

## DSL protocol

Every visualization MUST be wrapped in the `<mavis-widget>` DSL envelope. Output the widget inline
in your Markdown response — the streaming parser picks it up from the `msg_content` text channel.
The complete Widget MUST be in the final assistant message, after all tool calls and validation have
finished. Do not call any tool after emitting the Widget. Intermediate assistant messages that also
request a tool are execution progress, not a durable user-visible visualization surface.

```
<mavis-widget
  version="mavis.widget.v1"
  kind="<chart|dashboard|map|diagram|interactive|form|mockup>"
  title="<short descriptive title>"
  height="<initial height in px>"
  min-height="<minimum>"
  max-height="<maximum>"
  streaming="html-first"
  capabilities="resize[,sendPrompt]"
  theme="app"
  token-set="mavis.semantic.v1">
  <mavis-meta type="json">{"summary":"...","dataSource":"...","chartType":"..."}</mavis-meta>
  <mavis-style>/* CSS — short block first for streaming */</mavis-style>
  <mavis-html><!-- visible structure, SVG containers, KPI cards --></mavis-html>
  <mavis-data name="main" type="json">[...]</mavis-data>
  <mavis-script>/* deferred until widget close — reads window.__mavisData, binds interactions */</mavis-script>
  <mavis-fallback>Markdown summary when widget cannot render.</mavis-fallback>
</mavis-widget>
```

### Required opening-tag fields

| Field          | Value                                                                         | Notes                                                                       |
| -------------- | ----------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| `version`      | `mavis.widget.v1`                                                             | Always this value.                                                          |
| `kind`         | `chart` / `dashboard` / `map` / `diagram` / `interactive` / `form` / `mockup` | Drives rendering strategy and default height.                               |
| `title`        | Short descriptive text                                                        | Shown in loading shell, collapse header, accessibility label.               |
| `streaming`    | `html-first`                                                                  | Default. Show structure while data loads.                                   |
| `theme`        | `app`                                                                         | Follow host Argon/Mavis theme.                                              |
| `token-set`    | `mavis.semantic.v1`                                                           | Stable token contract version.                                              |
| `capabilities` | `resize` or `resize,sendPrompt`                                               | Dashboards and widgets with follow-up actions MUST use `resize,sendPrompt`. |

### Dashboard contract

For `kind="dashboard"`, the widget is an analytical surface, not a static image. It MUST include:

1. `capabilities="resize,sendPrompt"` in the opening tag.
2. At least two visible clickable elements, such as KPI cards, anomaly rows, highlighted
   chart-linked controls, or bottom action buttons.
3. Matching `window.mavis.sendPrompt(...)` handlers in `<mavis-script>`.
4. `<mavis-meta>` `interactions` entries whose `"capability"` value is exactly `"sendPrompt"`.

Do not use placeholder capabilities such as `"explain"`, `"drilldown"`, or `"open"`. If a dashboard
genuinely cannot support a meaningful follow-up, use `kind="chart"` instead.

### Compact dashboard budget

For normal analytical dashboards, prefer the compact pattern that fits in one conversation turn:

- 3-4 KPI cards, up to 3 Chart.js charts, and at most one compact insight list/table.
- 2-4 high-value `sendPrompt` targets, not one target per data point.
- `height` around 720-860 for multi-chart dashboards, `min-height` around 520-640, and
  `max-height` up to 1200 when the content genuinely needs it.
- The host may grow the iframe beyond the declared `max-height` to fit measured content,
  up to a host safety cap. Still provide realistic height values; do not rely on host
  auto-growth for layout.
- Avoid full written reports, repeated chart variants, and large raw-data tables inside the widget.

### Recommended heights

| kind           | height | min-height | max-height |
| -------------- | ------ | ---------- | ---------- |
| chart (single) | 400    | 280        | 560        |
| chart (combo)  | 480    | 320        | 640        |
| dashboard      | 560    | 400        | 900        |
| map            | 480    | 320        | 720        |
| interactive    | 420    | 300        | 700        |
| diagram        | 400    | 280        | 640        |
| form           | 360    | 240        | 600        |
| mockup         | 480    | 320        | 800        |

Use the compact dashboard budget above for dense analytical dashboards with multiple charts.

### Sub-block streaming order

1. `<mavis-style>` — short CSS first so structure is styled on arrival.
2. `<mavis-html>` — visible structure arrives next; SVG containers, KPI cards, section headers.
3. `<mavis-data>` — JSON dataset(s) injected into `window.__mavisData` by the host (NOT in the DOM).
4. `<mavis-script>` — deferred until widget close; reads data from `window.__mavisData['name']`.
5. `<mavis-fallback>` — Markdown summary capturing key insight and top data points.

Avoid hidden tabs or `display: none` sections during streaming. Stack content vertically until
scripts run.

### `<mavis-meta>` contract

Always include:

```json
{
  "summary": "One-sentence takeaway",
  "dataSource": "user-provided or description of origin",
  "chartType": "line|bar|pie|area|scatter|radar|heatmap|combo|table|kpi|dashboard"
}
```

Recommended additional fields:

```json
{
  "datasetShape": {
    "rows": 36,
    "dimensions": ["month", "year"],
    "measures": ["ridership"]
  },
  "interactions": [
    {
      "id": "drill-anomaly",
      "label": "Explain anomaly",
      "capability": "sendPrompt"
    }
  ]
}
```

`datasetShape` helps the host display dataset summary information and aids testing. `interactions`
declares clickable elements for auditability — it does not grant capabilities (that is still
controlled by the opening tag `capabilities` attribute).

### `<mavis-fallback>` contract

Always provide:

1. The key insight or conclusion
2. Top 3-5 data points as a simple list or Markdown table
3. Trend direction if applicable

The `<mavis-fallback>` block handles render failures (script error, CSP block). It is NOT an excuse
to write a full Markdown analysis outside the widget "just in case."

- **`<mavis-fallback>` inside the widget**: 2-4 sentence summary + top 3-5 data points. Shown when
  the widget iframe fails.
- **Prose outside the widget**: 1-3 sentences of intro/conclusion. NOT a duplicate of the widget
  content.
- **Do NOT output Markdown tables that repeat widget data.** The widget IS the visualization. If it
  renders, external tables are redundant. If it fails, the fallback covers it.

## Data handling rules

1. Use deterministic local or user-provided data. No auth, no remote APIs, no hidden services.
2. Structure datasets as schema-first arrays of objects:

```json
[
  { "month": "2025-01", "value": 906, "year": 2025, "yoy": -1.1 },
  { "month": "2025-02", "value": 820, "year": 2025, "yoy": -12.5 }
]
```

3. Round every displayed number. Use `Intl.NumberFormat` for locale-aware formatting or `.toFixed()`
   for fixed decimals.
4. Never reconstruct data from DOM elements in scripts. Read from `window.__mavisData['name']` only.
5. Multiple datasets are allowed — use short, descriptive names: `main`, `events`, `benchmarks`.
   Each `<mavis-data>` block must have a unique `name` attribute.

## Security constraints

The following are hard prohibitions for all widget output:

- No `<script>` tags inside `<mavis-html>`. Scripts must go in `<mavis-script>` only.
- No `eval()`, `new Function()`, or dynamic `import()`.
- No reading `document.cookie`, `localStorage`, or `sessionStorage`.
- No `fetch()`, `XMLHttpRequest`, or any remote API calls from widget code.
- No remote fonts, remote images, or remote map tiles. Use inline SVG, CSS shapes, or `data:` URIs.
- No fabricating user clicks or forging data sources in prompt text.
- No embedding sensitive file paths, tokens, API keys, or environment variables in widget output.

Acceptable exceptions:

- `kind="chart"` or `kind="dashboard"` may use Chart.js from the host-controlled CDN (auto-injected
  by the host). **Other kinds do NOT load Chart.js — using `new Chart(...)` outside these two kinds
  causes a script error.**
- User-provided HTTP/HTTPS URLs may appear as `openLink` targets, but must not be fetched inside the
  iframe.
- Maps use local SVG/Canvas surrogates, not external tile servers.

## Renderer and Chart.js sizing rules

**Chart.js requires `kind="chart"` or `kind="dashboard"`.** The host only injects Chart.js CDN and
the corresponding CSP allowance for these two kinds. Using `new Chart(...)` with any other kind
(e.g. `interactive`, `diagram`, `map`) will cause a `Chart is not defined` script error because the
CDN script is never loaded. If a widget needs Chart.js, set the kind accordingly — use
`kind="interactive"` only for CSS/SVG/Canvas-based controls that do NOT depend on Chart.js.

For standard analytical charts, choose the renderer deliberately:

- For trend, comparison, distribution, or multi-series dashboards with 6+ data points, use Chart.js
  with `kind="chart"` or `kind="dashboard"`. Do not hand-draw ordinary line charts, grouped bars,
  stacked bars, or area charts with manual Canvas/SVG/CSS.
- Manual Canvas is only for nonstandard renderers such as dense heatmaps, custom animations, or
  visuals that Chart.js cannot express well.
- CSS/SVG is preferred for KPI cards, simple bars, progress rings, sparklines, and displays with
  fewer than 6 data points.

Every Chart.js `<canvas>` must be inside a wrapper with explicit CSS height. Do not rely on the
canvas `height` attribute or an auto-height flex parent. With `maintainAspectRatio: false`, Chart.js
reads the parent height; an unconstrained parent can cause hover/resize feedback loops that make the
iframe grow indefinitely.

```html
<div class="chart-wrap">
  <canvas id="main-chart"></canvas>
</div>
```

```css
.chart-wrap { position: relative; width: 100%; height: 260px; }
.chart-wrap.short { height: 220px; }
```

```javascript
new Chart(document.getElementById('main-chart'), {
  type: 'line',
  data: chartData,
  options: { responsive: true, maintainAspectRatio: false }
});
```

## sendPrompt interaction

When `capabilities` includes `sendPrompt`, the widget can trigger a follow-up conversation turn.
This is the most important cross-boundary interaction.

### Requirements

- Clickable areas must be visible to the user (buttons, cards, highlighted rows). No hidden
  auto-sends.
- The `text` parameter must be a user-readable question, not a hidden system instruction.
- **Language matching**: The `text` parameter and `userIntent` field MUST use the same language as
  the user's conversation. Check `conversationLanguage` in `<agent-context>` — when it is `zh`, all
  sendPrompt `text` and `userIntent` values MUST be Chinese; when `en`, use English. When the field
  is absent, infer from the user's most recent message. The skill template examples are in English —
  always adapt them to the conversation language.
- Always pass structured `metadata` as the second argument to provide context for the model:

```javascript
window.mavis.sendPrompt('Explain why this metric dropped after the holiday.', {
  widgetTitle: 'Performance overview',
  action: 'explain-drop',
  selectedData: { month: '2025-02', value: 820, yoy: -12.5 },
  visibleFilters: { year: 2025 },
  userIntent: 'explain selected insight',
});
```

### Metadata fields

| Field            | Type   | Purpose                                                |
| ---------------- | ------ | ------------------------------------------------------ |
| `widgetTitle`    | string | Title of the originating widget                        |
| `action`         | string | Stable identifier for the clicked element              |
| `selectedData`   | object | Data point(s) the user selected or the card represents |
| `visibleFilters` | object | Current filter/view state at time of click             |
| `userIntent`     | string | Short description of what the user is asking for       |

### Implementation pattern

Use `addEventListener` in `<mavis-script>` instead of inline `onclick` attributes, and read data
from `window.__mavisData`:

```javascript
document.querySelector('[data-action="explain-drop"]').addEventListener('click', function () {
  var point = window.__mavisData['main'].find(function (row) {
    return row.month === '2025-02';
  });
  window.mavis.sendPrompt('Explain why this metric dropped after the holiday.', {
    widgetTitle: 'Performance overview',
    action: 'explain-drop',
    selectedData: point,
    visibleFilters: { year: 2025 },
    userIntent: 'explain selected insight',
  });
});
```

### When to use sendPrompt

- Clickable elements showing anomalies — let the user drill in
- Outlier data points or peaks — let the user ask "why is this different?"
- Bottom action bar — suggest 2-3 natural follow-up actions
- Table rows with notable patterns — click to get details

### When NOT to use sendPrompt

- Filtering, sorting, toggling views — keep in local JS
- Every single data point — pick 2-4 high-value interaction spots
- Simple tooltips — use CSS `:hover` instead

## Core engineering rules

- Every required interaction should change something the reviewer can see: a series, rows,
  highlighted segment, inspector panel, or metric readout.
- Key values must be visible in the layout or legend, not only in hover tooltips.
- Build explicit states: loading, empty, error, and the working state the task requires.
- Prefer flat surfaces, sentence case, 12px+ text, and a small number of color ramps.

## Final output self-check

Before sending a widget, fix any violation of this checklist:

- Widget is inline in `msg_content`, not stored in or read back from a file.
- Opening tag has all required fields and valid heights for the `kind`.
- `kind="dashboard"` has `capabilities="resize,sendPrompt"`, at least two visible click targets, and
  at least two `window.mavis.sendPrompt(...)` handlers.
- `mavis-meta.interactions[].capability` is `"sendPrompt"` for every follow-up interaction.
- `<mavis-script>` reads data only from `window.__mavisData`, never from DOM-embedded JSON.
- CSS uses only valid `--mw-*` tokens for theme-sensitive styling and has no hardcoded fallback
  colors.
- Prose outside the widget is short and does not duplicate widget tables, KPIs, or fallback content.
- Final `msg_content` contains exactly one `<mavis-widget>` opening tag and exactly one closing tag,
  unless the user explicitly requested multiple widgets.
- Final `msg_content` contains no `<think>`, hidden reasoning, planning notes, tool logs, or
  implementation narrative.
- Standard analytical dashboards with 6+ data points use Chart.js (`new Chart(...)`) rather than
  manual Canvas drawing.
- **Chart.js usage (`new Chart(...)`) only appears in `kind="chart"` or `kind="dashboard"` widgets.**
  Other kinds do not load Chart.js and will cause a script error.
- Every Chart.js canvas is wrapped by an explicit-height container such as `.chart-wrap`.
- The final response follows the concise shape: one short intro, one widget, one short conclusion.

## Reference files

The compact contract above is enough for standard widgets. Read these references only when needed:

- [references/color-system.md](references/color-system.md) — full token contract, JS runtime tokens,
  and dark-mode behavior.
- [references/visual-rules.md](references/visual-rules.md) — complex streaming, typography, layout,
  interaction, and state rules.
- [references/widget-patterns.md](references/widget-patterns.md) — renderer selection, data access
  API, Chart.js guardrails, CSS/SVG patterns, and theme-change code.
- [references/widget-recipes.md](references/widget-recipes.md) — generic scaffolds and sendPrompt
  examples.
