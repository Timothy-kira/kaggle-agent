# Color System

This file defines the `--mw-*` CSS token contract and chart palette used inside all `mavis-widget` iframes.

## Core principle

Widget code never reads host CSS variables directly. The host resolves its Argon/Mavis theme tokens, maps them to the stable `--mw-*` namespace via `WidgetThemeBridge`, and injects them into the iframe. Widget HTML and scripts only consume `--mw-*`.

## Semantic CSS variable families

### Surface tokens

| Token | Use |
|---|---|
| `--mw-bg` | iframe document background, global canvas base |
| `--mw-surface` | card background, panel background, tooltip container |
| `--mw-surface-muted` | subtle background: table headers, hover state, secondary panels |

### Text tokens

| Token | Use |
|---|---|
| `--mw-text` | primary text: titles, KPI values, key labels |
| `--mw-text-muted` | secondary text: subtitles, axis labels, table help columns |
| `--mw-text-subtle` | tertiary text: placeholders, low-priority tick marks, empty-state hints |

### Border and accent

| Token | Use |
|---|---|
| `--mw-border` | card borders, grid lines, dividers, table rules |
| `--mw-accent` | primary interaction color, selected state, main CTA |

### Semantic status

| Token | Use |
|---|---|
| `--mw-success` | positive trend, growth, pass, healthy |
| `--mw-warning` | caution, at-risk, approaching threshold |
| `--mw-danger` | negative trend, decline, fail, critical |

## Chart palette

Data series use dedicated chart tokens. These are theme-aware — the bridge picks appropriate values for light and dark modes.

| Token | Purpose |
|---|---|
| `--mw-chart-1` | primary series |
| `--mw-chart-2` | secondary series |
| `--mw-chart-3` | tertiary series |
| `--mw-chart-4` | fourth series |
| `--mw-chart-5` | fifth series |
| `--mw-chart-6` | sixth series |

For more than 6 series, derive additional colors by adjusting opacity: `var(--mw-chart-1) + '88'`.

### Chart structural tokens

| Token | Use |
|---|---|
| `--mw-chart-grid` | grid lines in charts (subtle, never black) |
| `--mw-chart-axis` | axis tick labels and axis lines |

### Map tokens (planned — not yet available in theme bridge)

The following tokens are defined for future map widget support. They are **not yet implemented** in the host theme bridge — using them will resolve to empty values. For now, use `--mw-chart-*` tokens as substitutes for map-related coloring.

| Token | Planned use |
|---|---|
| `--mw-map-route` | route lines, transit paths |
| `--mw-map-node` | markers, station dots |
| `--mw-map-area` | zone fills, region backgrounds |
| `--mw-map-heat` | heat intensity overlay |

## CSS usage pattern

```css
body {
  margin: 0;
  padding: 16px;
  background: var(--mw-bg);
  color: var(--mw-text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  font-size: 14px;
}

.card {
  background: var(--mw-surface);
  border: 1px solid var(--mw-border);
  border-radius: 12px;
  padding: 16px;
}

.kpi-value { font-size: 24px; font-weight: 700; color: var(--mw-text); }
.kpi-up { color: var(--mw-success); }
.kpi-down { color: var(--mw-danger); }

.table th { background: var(--mw-surface-muted); color: var(--mw-text-muted); }
.table td { border-bottom: 1px solid var(--mw-border); }
```

## BANNED in widget CSS and HTML

- Tailwind class names (iframe has no Tailwind)
- Hardcoded hex for backgrounds, text, or borders
- `#000000` or `#ffffff` as text or background colors
- Inline `style="color: #xxx"` for theme-sensitive elements
- Direct references to host CSS variables like `--bg_default_primary`

Exception: hardcoded hex IS allowed for data series fills in inline SVG `stroke`/`fill` attributes when CSS var() is not supported in that context. But prefer `var(--mw-chart-1)` whenever possible — it works in most SVG attributes and updates automatically on theme change.

## JS runtime tokens

Canvas, Chart.js, and any imperative rendering must read colors from `window.mavisTheme.tokens`:

```javascript
const t = window.mavisTheme.tokens;
// t.bg, t.surface, t.surfaceMuted
// t.text, t.textMuted, t.textSubtle
// t.border, t.accent
// t.success, t.warning, t.danger
// t.chart1 .. t.chart6
// t.chartGrid, t.chartAxis
// t.mapRoute, t.mapNode, t.mapArea, t.mapHeat  (planned — not yet available)
```

### Chart.js integration

```javascript
const data = window.__mavisData['main'];
const t = window.mavisTheme.tokens;
new Chart(ctx, {
  type: 'bar',
  data: {
    labels: data.labels,
    datasets: [{
      data: data.values,
      backgroundColor: t.chart1 + '88',
      borderColor: t.chart1,
      borderWidth: 1.5,
      borderRadius: 6
    }]
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { labels: { color: t.text } },
      tooltip: {
        backgroundColor: t.surface,
        titleColor: t.text,
        bodyColor: t.textMuted,
        borderColor: t.border,
        borderWidth: 1
      }
    },
    scales: {
      x: {
        grid: { color: t.chartGrid },
        ticks: { color: t.chartAxis }
      },
      y: {
        grid: { color: t.chartGrid },
        ticks: { color: t.chartAxis }
      }
    }
  }
});
```

### Dynamic SVG coloring

When building SVG elements in script, use JS tokens for attributes that don't support CSS var():

```javascript
const t = window.mavisTheme.tokens;
const line = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
line.setAttribute('stroke', t.chart1);
line.setAttribute('fill', 'none');
```

For most SVG attributes, prefer `var(--mw-chart-1)` directly — it works in `stroke`, `fill`, and `color` attributes and updates automatically on theme change.

## Theme change handling

The host sends `mavis:theme-change` via postMessage when the user toggles light/dark mode. The iframe bootstrap dispatches it as a CustomEvent.

CSS/SVG using `var(--mw-*)` updates automatically — no JS needed. Chart.js instances and script-built elements with hardcoded color values need manual refresh:

```javascript
window.addEventListener('mavis:theme-change', () => {
  const nt = window.mavisTheme.tokens;
  chart.data.datasets.forEach((ds, i) => {
    const c = [nt.chart1, nt.chart2, nt.chart3, nt.chart4, nt.chart5, nt.chart6][i];
    if (c) {
      ds.borderColor = c;
      ds.backgroundColor = c + (ds.type === 'line' ? '18' : 'cc');
    }
  });
  chart.options.scales.x.ticks.color = nt.chartAxis;
  chart.options.scales.y.ticks.color = nt.chartAxis;
  chart.options.scales.y.grid.color = nt.chartGrid;
  chart.options.plugins.legend.labels.color = nt.text;
  chart.options.plugins.tooltip.backgroundColor = nt.surface;
  chart.options.plugins.tooltip.titleColor = nt.text;
  chart.options.plugins.tooltip.bodyColor = nt.textMuted;
  chart.update('none');
  document.querySelectorAll('[data-series]').forEach((el, i) => {
    const c = [nt.chart1, nt.chart2, nt.chart3][i];
    if (c) el.setAttribute('stroke', c);
  });
});
```

Most widgets using CSS vars exclusively do NOT need a theme change listener.

## Dark mode behavior

- All `--mw-*` tokens are automatically updated by the bridge on theme change.
- CSS variables in `<mavis-style>` automatically resolve to the new values.
- SVG `fill` and `stroke` using `var(--mw-*)` update automatically.
- Chart.js instances must manually read the new `window.mavisTheme.tokens` and call `chart.update('none')`.
- If you use fixed hex for data series colors, ensure they have sufficient contrast on both light and dark surfaces.

## Practical guidance

- Use `--mw-chart-1` through `--mw-chart-6` for data series. Do not invent custom ramp colors.
- Use `--mw-success`, `--mw-warning`, `--mw-danger` for trend indicators and status badges. Do not mix semantic and chart tokens for the same purpose.
- Keep most charts to two or three series ramps at most.
- If a chart has a single series, use `--mw-accent` as the primary color.
- If color encodes meaning (positive/negative, above/below threshold), add a one-line legend and use semantic tokens.
