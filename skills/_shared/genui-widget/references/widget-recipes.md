# Widget Recipes

Generic scaffolds and interaction patterns for `mavis-widget` output. Business skills should reference these as starting points and customize for their domain.

## Dashboard scaffold

Recommended structure for any dashboard widget:

1. KPI cards for headline numbers.
2. One primary chart (Chart.js for 6+ data points, CSS/SVG for simple metrics).
3. One supporting table, legend, or inspector.
4. At least two visible sendPrompt actions on interesting data points.

### Starter structure

```html
<!-- in <mavis-html> -->
<div class="kpi-row">
  <div class="kpi">
    <div class="kpi-label">Total</div>
    <div class="kpi-value">$2,847</div>
    <div class="kpi-trend up">+12.3%</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Average</div>
    <div class="kpi-value">$949</div>
    <div class="kpi-trend down">-3.1%</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Margin</div>
    <div class="kpi-value">35.5%</div>
    <div class="kpi-trend up">+2.8pp</div>
  </div>
</div>
<div class="chart-card">
  <div class="chart-header">
    <div class="chart-title">Monthly trend</div>
    <div class="legend">
      <span class="legend-item"><span class="legend-dot" style="background: var(--mw-chart-1);"></span>Current</span>
      <span class="legend-item"><span class="legend-dot" style="background: var(--mw-chart-2);"></span>Prior</span>
    </div>
  </div>
  <div style="position: relative; width: 100%; height: 280px;">
    <canvas id="trend-chart"></canvas>
  </div>
</div>
```

```css
/* in <mavis-style> */
body { margin: 0; padding: 16px; background: var(--mw-bg); color: var(--mw-text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; font-size: 14px; }
.kpi-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 16px; }
.kpi { background: var(--mw-surface); border: 1px solid var(--mw-border); border-radius: 10px; padding: 14px; }
.kpi-label { font-size: 12px; color: var(--mw-text-muted); margin-bottom: 4px; }
.kpi-value { font-size: 24px; font-weight: 700; color: var(--mw-text); }
.kpi-trend { font-size: 12px; margin-top: 4px; }
.up { color: var(--mw-success); }
.down { color: var(--mw-danger); }
.chart-card { background: var(--mw-surface); border: 1px solid var(--mw-border); border-radius: 10px; padding: 16px; }
.chart-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.chart-title { font-size: 14px; font-weight: 500; }
.legend { display: flex; gap: 16px; font-size: 12px; color: var(--mw-text-muted); }
.legend-item { display: flex; align-items: center; gap: 6px; }
.legend-dot { width: 10px; height: 10px; border-radius: 3px; }
```

## Interactive explainer scaffold

Use when the user needs to manipulate parameters or see a metric respond live.

```html
<!-- in <mavis-html> -->
<div class="control-row">
  <label class="control-label">Parameter</label>
  <input type="range" id="param" min="1" max="40" step="1" value="20" class="slider">
  <span id="param-out" class="control-value">20</span>
</div>
<div class="readout">
  <span class="readout-label">Projected value</span>
  <span id="result" class="readout-value">$3,870</span>
</div>
```

```css
.control-row { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.control-label { font-size: 14px; color: var(--mw-text-muted); }
.slider { flex: 1; accent-color: var(--mw-accent); }
.control-value { min-width: 28px; font-size: 14px; font-weight: 500; color: var(--mw-text); }
.readout { display: flex; align-items: baseline; gap: 8px; margin-bottom: 16px; }
.readout-label { font-size: 14px; color: var(--mw-text-muted); }
.readout-value { font-size: 24px; font-weight: 500; color: var(--mw-text); }
```

Rules:

- Keep the widget self-sufficient — no model round-trips for slider changes.
- Round every displayed number.
- If a follow-up needs reasoning, wire it to `window.mavis.sendPrompt()` with metadata.

## Data table scaffold

```html
<!-- in <mavis-html> -->
<div class="table-header">
  <div class="table-title">Items</div>
  <div class="table-meta">12 items - sorted by value</div>
</div>
<table class="data-table">
  <thead>
    <tr><th>Name</th><th class="num">Value</th><th class="num">Change</th></tr>
  </thead>
  <tbody id="table-body"></tbody>
</table>
```

```css
.data-table { width: 100%; border-collapse: collapse; }
.data-table th { padding: 8px 12px; text-align: left; font-size: 12px; font-weight: 500; color: var(--mw-text-muted); background: var(--mw-surface-muted); border-bottom: 1px solid var(--mw-border); }
.data-table th.num { text-align: right; }
.data-table td { padding: 10px 12px; border-bottom: 1px solid var(--mw-border); font-size: 14px; }
.data-table td.num { text-align: right; font-variant-numeric: tabular-nums; }
.data-table tr:last-child td { border-bottom: none; }
.data-table tbody tr:hover { background: var(--mw-surface-muted); }
```

## sendPrompt interaction patterns

The `window.mavis.sendPrompt(text, metadata)` API lets the user click a widget element to automatically send a follow-up message. The widget MUST declare `capabilities="resize,sendPrompt"`.

Always use `addEventListener` in `<mavis-script>` instead of inline `onclick` attributes, and always pass structured metadata as the second argument.

### Pattern 1: Clickable element — drill into a detail

```html
<!-- in <mavis-html> -->
<div class="kpi clickable" data-action="drill-metric">
  <div class="kpi-label">Key metric</div>
  <div class="kpi-value">10.15M</div>
  <div class="kpi-trend down">-1.1%</div>
  <div class="kpi-action">Click to drill in</div>
</div>
```

```javascript
// in <mavis-script>
document.querySelector('[data-action="drill-metric"]').addEventListener('click', function() {
  var data = window.__mavisData['main'];
  window.mavis.sendPrompt(
    'Analyze what caused the decline in this metric.',
    {
      widgetTitle: 'Overview dashboard',
      action: 'drill-metric',
      selectedData: { metric: 'key_metric', value: 1015, yoy: -1.1 },
      visibleFilters: {},
      userIntent: 'explain metric decline'
    }
  );
});
```

```css
.clickable { cursor: pointer; transition: outline-color 0.15s; }
.clickable:hover { outline: 2px solid var(--mw-accent); outline-offset: -2px; }
.kpi-action { font-size: 11px; color: var(--mw-accent); margin-top: 6px; opacity: 0.6; transition: opacity 0.15s; }
.clickable:hover .kpi-action { opacity: 1; }
```

### Pattern 2: Action bar — continue analysis

```html
<!-- in <mavis-html> -->
<div class="action-bar">
  <button class="action-btn" data-action="breakdown">Breakdown by category</button>
  <button class="action-btn" data-action="trend">Trend analysis</button>
  <button class="action-btn" data-action="compare">Cross comparison</button>
</div>
```

```javascript
// in <mavis-script>
var actionMap = {
  'breakdown': { text: 'Break down the data by category.', intent: 'categorical breakdown' },
  'trend': { text: 'Analyze the trend over time.', intent: 'trend analysis' },
  'compare': { text: 'Compare performance across segments.', intent: 'cross comparison' }
};
document.querySelectorAll('.action-btn[data-action]').forEach(function(btn) {
  btn.addEventListener('click', function() {
    var action = btn.getAttribute('data-action');
    var info = actionMap[action];
    if (!info) return;
    window.mavis.sendPrompt(info.text, {
      widgetTitle: 'Overview dashboard',
      action: action,
      selectedData: null,
      visibleFilters: {},
      userIntent: info.intent
    });
  });
});
```

```css
.action-bar { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--mw-border); }
.action-btn { padding: 6px 12px; border-radius: 8px; border: 1px solid var(--mw-border); background: var(--mw-surface); color: var(--mw-text-muted); font-size: 12px; cursor: pointer; transition: all 0.15s; font-family: inherit; }
.action-btn:hover { background: var(--mw-surface-muted); color: var(--mw-accent); border-color: var(--mw-accent); }
```

### Pattern 3: Clickable table row — drill into a specific item

```html
<!-- in <mavis-html> -->
<tr class="clickable" data-action="drill-item" data-item-id="east">
  <td>East</td><td class="num">1,712</td><td class="num up">+18.3%</td>
</tr>
```

```javascript
// in <mavis-script>
document.querySelectorAll('[data-action="drill-item"]').forEach(function(row) {
  row.addEventListener('click', function() {
    var itemId = row.getAttribute('data-item-id');
    var itemData = window.__mavisData['items'].find(function(d) { return d.id === itemId; });
    window.mavis.sendPrompt(
      'Analyze the ' + itemId + ' segment in detail.',
      {
        widgetTitle: 'Overview dashboard',
        action: 'drill-item',
        selectedData: itemData,
        visibleFilters: {},
        userIntent: 'segment deep-dive'
      }
    );
  });
});
```

### When to use sendPrompt

- Clickable elements showing anomalies — let the user click to drill in
- Outlier data points or peaks — let the user ask "why is this different?"
- Bottom action bar — suggest 2-3 natural follow-up actions
- Table rows with interesting patterns — click to get details

### When NOT to use sendPrompt

- Filtering, sorting, toggling views — keep this in local JS
- Every single data point — interaction spam. Pick 2-4 high-value spots.
- Simple tooltips — use CSS `:hover` instead
