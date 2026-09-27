# Widget Patterns

Renderer selection, data access API, Chart.js guardrails, CSS/SVG component patterns, and theme change handling for `mavis-widget` iframes.

## Pick the right renderer

| Visual need | Renderer | Reason |
|---|---|---|
| ≤5 data points, single metric | CSS bench bars or SVG | Chart.js is overkill |
| KPI card, progress ring, sparkline | CSS and inline SVG | Lightweight, streams well |
| 6+ data points, standard chart types | Chart.js | Responsive, tooltips, animations |
| Dense scatter, custom heatmap, animation | Canvas (manual) | When Chart.js layout is limiting |
| Spatial data, floor plans, network graphs | SVG embedded in HTML | Deterministic coordinates |

Rule: do not use Chart.js for 5 or fewer data points or a single-metric display — use CSS/SVG instead.

Chart.js is automatically injected by the host when `kind="chart"` or `kind="dashboard"`. No need to add a `<script src="...">` tag yourself.

## Data access API

`<mavis-data>` blocks are parsed by the host and injected into `window.__mavisData`. The script accesses data by name:

```javascript
// in <mavis-script>
const data = window.__mavisData['main'];
// data is already a parsed JS object/array — no JSON.parse needed
```

**Never** use `document.querySelector('[data-name]')` — the data is NOT in the DOM.
**Never** hardcode the same dataset inside the script body — `window.__mavisData` is the single source of truth.

## Chart.js guardrails

### Loading pattern

**CRITICAL:** Every `<canvas>` used by Chart.js MUST be wrapped in a container with an explicit CSS
height (e.g. `height: 320px`). Do NOT rely on the canvas `height` HTML attribute alone — when
`maintainAspectRatio: false`, Chart.js reads the parent container's CSS height. If the parent has no
fixed height, Chart.js will continuously recalculate the canvas size on hover/resize, creating a
positive feedback loop that inflates the iframe to thousands of pixels.

**Dangerous pattern (DO NOT USE):**

```html
<!-- ❌ .chart-box has no CSS height, canvas only has HTML attribute -->
<div class="chart-box" style="flex: 1;">
  <canvas id="myChart" height="220"></canvas>
</div>
```

**Correct pattern:**

```html
<!-- ✅ Container has explicit CSS height -->
<div style="position: relative; width: 100%; height: 320px;">
  <canvas id="main-chart"></canvas>
</div>
```

```javascript
// in <mavis-script>
const data = window.__mavisData['main'];
const t = window.mavisTheme.tokens;
const chart = new Chart(document.getElementById('main-chart'), {
  type: 'line',
  data: { labels: data.labels, datasets: [{ data: data.values, borderColor: t.chart1, tension: 0.25 }] },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } }
  }
});
```

### Chart.js conventions

- Font family: `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif` everywhere.
- Grid lines: use `t.chartGrid`. Subtle, never black. Consider `display: false` on x-axis grid for bar charts.
- Border radius on bars: `borderRadius: 6`.
- Legend: use custom HTML legend rather than Chart.js default for dashboard layouts. Chart.js default is fine for single charts.
- Tooltip: always include `borderWidth: 1, borderColor: t.border`. Background: `t.surface`. Text: `t.text` and `t.textMuted`.
- Responsive: `responsive: true, maintainAspectRatio: false`.
- Animation: `animation: { duration: 600, easing: 'easeOutQuart' }`.
- For horizontal bars, size the wrapper height from the number of bars: `height: bars * 36 + 60`.
- Pad scatter and bubble scales to avoid clipping edge points.
- If category labels matter, disable `autoSkip` and cap rotation deliberately.

### Custom HTML legend pattern

```html
<div class="legend">
  <span class="legend-item"><span class="legend-dot" style="background: var(--mw-chart-1);"></span>Series A</span>
  <span class="legend-item"><span class="legend-dot" style="background: var(--mw-chart-2);"></span>Series B</span>
</div>
```

```css
.legend { display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 8px; font-size: 12px; color: var(--mw-text-muted); }
.legend-item { display: flex; align-items: center; gap: 6px; }
.legend-dot { width: 10px; height: 10px; border-radius: 3px; }
```

## CSS and SVG component patterns

For simple visualizations without Chart.js (≤5 data points or single-metric displays).

### Bench bar (comparison row)

```html
<div class="bench">
  <span class="bench-label">Category A</span>
  <div class="bench-track"><div class="bench-fill" style="width: 72%;"></div></div>
  <span class="bench-val">72%</span>
</div>
```

```css
.bench { display: grid; grid-template-columns: 120px 1fr 52px; gap: 12px; align-items: center; padding: 6px 0; }
.bench-track { height: 18px; background: var(--mw-surface-muted); border-radius: 6px; overflow: hidden; }
.bench-fill { height: 100%; background: var(--mw-accent); border-radius: 6px; }
.bench-val { font-size: 14px; font-weight: 500; color: var(--mw-text); text-align: right; }
```

### KPI card

```html
<div class="kpi">
  <div class="kpi-label">Total value</div>
  <div class="kpi-value">¥2,847</div>
  <div class="kpi-trend up">↑ 12.3% vs prior</div>
</div>
```

```css
.kpi { background: var(--mw-surface); border: 1px solid var(--mw-border); border-radius: 10px; padding: 14px; }
.kpi-label { font-size: 12px; color: var(--mw-text-muted); margin-bottom: 4px; }
.kpi-value { font-size: 24px; font-weight: 700; color: var(--mw-text); }
.kpi-trend { font-size: 12px; margin-top: 4px; }
.up { color: var(--mw-success); }
.down { color: var(--mw-danger); }
```

### SVG donut

```html
<svg width="120" height="120" viewBox="0 0 120 120">
  <circle cx="60" cy="60" r="48" fill="none" stroke="var(--mw-surface-muted)" stroke-width="12"/>
  <circle cx="60" cy="60" r="48" fill="none" stroke="var(--mw-accent)" stroke-width="12"
    stroke-dasharray="217 301" stroke-dashoffset="-75" stroke-linecap="round"/>
  <text x="60" y="64" text-anchor="middle" fill="var(--mw-text)" font-size="18" font-weight="700">72%</text>
</svg>
```

### SVG sparkline

```html
<svg width="80" height="24" viewBox="0 0 80 24">
  <polyline points="0,20 16,14 32,18 48,8 64,12 80,4"
    fill="none" stroke="var(--mw-accent)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
```

## Theme change: update everything, not just axes

When listening for `mavis:theme-change`, you MUST update:

1. **Dataset colors** — `borderColor`, `backgroundColor`, `pointBackgroundColor` for every dataset.
2. **Scale colors** — grid, ticks, axis labels.
3. **Plugin colors** — legend labels, tooltip background/text/border.
4. **Call `chart.update('none')`** — no animation on theme switch.

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
});
```
