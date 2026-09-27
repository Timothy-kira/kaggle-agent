# Examples

One representative, complete `<mavis-widget>` for the interval control, grounded in the shape
in `data-schema.json` and built from the shared components in
`../../_shared/genui-widget/COMPONENTS.md`.

## Scenario

A multi-hour training run is being monitored. `kaggle_log_monitor action="status"` returns:

```
interval:   120s (allowed 15-900, step 15s)
revision:   1
updated:    2026-09-27T06:16:40+00:00
targets:    1
  - kaggle: user/exp-n7
```

The user wants to watch more closely during a risky phase. They can either drag the slider to
roughly 45s or type `45` into the number field — the two are bound, and the slider is never
the only way in.

Choices worth noting:

- `kind="interactive"` — a single scalar control. No `new Chart(...)`: Chart.js is not injected
  for this kind, and one number is not a chart.
- **Slider and number field are bound both ways.** Typing snaps on `change` (blur/Enter), not
  on every keystroke, so typing "1" on the way to "120" is not rewritten to the minimum.
- The number field is enabled even when the slider is at the applied value, so a user can type
  an exact figure directly; only the **confirm** is disabled, because there is nothing to apply.
- No `sendPrompt` while typing or dragging. Only the confirm button sends.
- No token anywhere in the data, the DOM, or the prompt metadata.

```html
<mavis-widget
  version="mavis.widget.v1"
  kind="interactive"
  title="Log 抓取间隔"
  height="400"
  min-height="320"
  max-height="640"
  streaming="html-first"
  capabilities="resize,sendPrompt"
  theme="app"
  token-set="mavis.semantic.v1">
  <mavis-meta type="json">{"summary":"当前 log 抓取间隔 120 秒；可拖动滑块或直接输入秒数，确认后由主 Agent 写入配置，监控 subagent 下一轮即生效","dataSource":"kaggle_log_monitor action=status","chartType":"kpi","interactions":[{"id":"apply-interval","label":"确认应用新的抓取间隔","capability":"sendPrompt"}]}</mavis-meta>
  <mavis-style>
    body {
      margin: 0;
      padding: 16px;
      background: var(--mw-bg);
      color: var(--mw-text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      font-size: 14px;
    }
    .head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 4px; }
    .head-label { font-size: 12px; color: var(--mw-text-muted); }
    .applied { font-size: 28px; font-weight: 700; color: var(--mw-text); font-variant-numeric: tabular-nums; }
    .applied .unit { font-size: 14px; font-weight: 500; color: var(--mw-text-muted); margin-left: 2px; }
    .prov { font-size: 11px; color: var(--mw-text-subtle); }
    .row { display: flex; align-items: center; gap: 12px; margin: 14px 0 8px; }
    .slider { flex: 1; min-width: 0; accent-color: var(--mw-accent); }
    .num {
      flex: 0 0 88px;
      width: 88px;
      padding: 9px 10px;
      border-radius: 8px;
      border: 1px solid var(--mw-border);
      background: var(--mw-bg);
      color: var(--mw-text);
      font-size: 14px;
      font-family: inherit;
      font-variant-numeric: tabular-nums;
    }
    .num:focus { outline: 2px solid var(--mw-accent); outline-offset: -1px; }
    .confirm {
      flex: 0 0 auto;
      min-width: 108px;
      padding: 9px 16px;
      border-radius: 8px;
      border: 1px solid var(--mw-accent);
      background: var(--mw-accent);
      color: var(--mw-bg);
      font-size: 13px;
      font-family: inherit;
      cursor: pointer;
    }
    .confirm:disabled { cursor: default; opacity: 0.45; border-color: var(--mw-border); background: var(--mw-surface-muted); color: var(--mw-text-subtle); }
    .ticks { display: flex; justify-content: space-between; font-size: 11px; color: var(--mw-text-subtle); margin: 0 0 12px; font-variant-numeric: tabular-nums; }
    .pending { font-size: 13px; color: var(--mw-text-muted); margin-bottom: 10px; }
    .pending b { color: var(--mw-accent); font-weight: 600; }
    .hint { font-size: 12px; color: var(--mw-text-muted); }
    .hint.bad { color: var(--mw-danger); }
    .targets { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--mw-border); }
    .targets-label { font-size: 12px; color: var(--mw-text-muted); margin-bottom: 8px; }
    .chip { display: inline-block; padding: 3px 10px; margin: 0 6px 6px 0; border-radius: 999px; border: 1px solid var(--mw-border); background: var(--mw-surface); color: var(--mw-text); font-size: 12px; }
    .chip .kind { color: var(--mw-text-subtle); margin-right: 6px; }
    .empty { padding: 10px 12px; border-radius: 8px; background: var(--mw-surface-muted); color: var(--mw-text-muted); font-size: 12px; }
  </mavis-style>
  <mavis-html>
    <div class="head">
      <span class="head-label">当前间隔</span>
      <span class="applied"><span id="applied-value">120</span><span class="unit">秒</span></span>
      <span class="prov" id="prov"></span>
    </div>
    <div class="row">
      <input type="range" class="slider" id="interval" min="15" max="900" step="15" value="120"
             aria-label="log 抓取间隔滑块（秒）">
      <input type="number" class="num" id="interval-num" min="15" max="900" step="15" value="120"
             aria-label="直接输入抓取间隔秒数">
      <button class="confirm" id="confirm" disabled>确认应用</button>
    </div>
    <div class="ticks">
      <span>15</span><span>450</span><span>900</span>
    </div>
    <div class="pending">待应用 <b id="pending-value">120</b> 秒</div>
    <div class="hint" id="hint">与当前一致，无需确认。</div>
    <div class="targets">
      <div class="targets-label" id="targets-label">监控目标</div>
      <div id="targets-body"></div>
    </div>
  </mavis-html>
  <mavis-data name="main" type="json">[{"intervalSeconds":120,"revision":1,"updatedAt":"2026-09-27T06:16:40+00:00","minSeconds":15,"maxSeconds":900,"stepSeconds":15,"exists":true,"targets":[{"kind":"kaggle","ref":"user/exp-n7"}]}]</mavis-data>
  <mavis-script>
    (function () {
      var cfg = (window.__mavisData['main'] || [])[0] || {};
      var MIN = Number(cfg.minSeconds || 15);
      var MAX = Number(cfg.maxSeconds || 900);
      var STEP = Number(cfg.stepSeconds || 15);

      var slider = document.getElementById('interval');
      var num = document.getElementById('interval-num');
      var appliedEl = document.getElementById('applied-value');
      var pendingEl = document.getElementById('pending-value');
      var confirmBtn = document.getElementById('confirm');
      var hintEl = document.getElementById('hint');
      var provEl = document.getElementById('prov');
      var bodyEl = document.getElementById('targets-body');
      var labelEl = document.getElementById('targets-label');

      var applied = Number(cfg.intervalSeconds || 120);
      appliedEl.textContent = String(applied);

      var prov = [];
      if (cfg.revision) { prov.push('revision ' + cfg.revision); }
      if (cfg.updatedAt) { prov.push('最后修改 ' + cfg.updatedAt); }
      if (!cfg.exists) { prov.unshift('当前使用默认值'); }
      provEl.textContent = prov.join(' · ');

      [slider, num].forEach(function (el) {
        el.min = MIN;
        el.max = MAX;
        el.step = STEP;
      });
      slider.value = String(applied);
      num.value = String(applied);

      function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
          return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
      }

      function renderTargets(list) {
        if (!list || !list.length) {
          labelEl.textContent = '监控目标';
          bodyEl.innerHTML = '<div class="empty">尚未注册监控目标。用 kaggle_log_monitor action="target" 注册一个 kernel ref 或本地日志路径后再开始监控。</div>';
          return;
        }
        labelEl.textContent = '正在监控 ' + list.length + ' 个';
        bodyEl.innerHTML = list.map(function (t) {
          var name = t.kind === 'local' ? String(t.path || '').split(/[\\/]/).pop() : (t.ref || '');
          return '<span class="chip"><span class="kind">' + (t.kind === 'local' ? '本地' : 'Kaggle') + '</span>' + esc(name) + '</span>';
        }).join('');
      }
      renderTargets(cfg.targets);

      function clamp(v) { return Math.max(MIN, Math.min(MAX, v)); }

      function sync() {
        var next = Number(slider.value);
        pendingEl.textContent = String(next);
        hintEl.className = 'hint';
        if (next === applied) {
          confirmBtn.disabled = true;
          hintEl.textContent = '与当前一致，无需确认。';
        } else {
          confirmBtn.disabled = false;
          hintEl.textContent = '确认后由主 Agent 调用 kaggle_log_monitor 写入配置，监控 subagent 下一轮即按新间隔抓取。';
        }
      }

      // slider -> number: live, no clamp needed, the slider is already in range.
      slider.addEventListener('input', function () {
        num.value = slider.value;
        sync();
      });

      // number -> slider: on commit only. 'change' fires on blur/Enter, so typing
      // "1" on the way to "120" is not rewritten to the minimum mid-keystroke.
      num.addEventListener('change', function () {
        var v = parseInt(num.value, 10);
        if (isNaN(v)) {
          num.value = slider.value;
          return;
        }
        var clamped = clamp(v);
        num.value = String(clamped);
        slider.value = String(clamped);
        if (v !== clamped) {
          hintEl.className = 'hint bad';
          hintEl.textContent = '超出允许范围 ' + MIN + '-' + MAX + ' 秒，已调整为 ' + clamped + ' 秒。';
        }
        sync();
        if (v === clamped) { sync(); }
      });

      confirmBtn.addEventListener('click', function () {
        var next = Number(slider.value);
        if (next === applied || confirmBtn.disabled) { return; }
        window.mavis.sendPrompt('把 log 抓取间隔改为 ' + next + ' 秒', {
          widgetTitle: 'Log 抓取间隔',
          action: 'apply-interval',
          selectedData: {
            intervalSeconds: next,
            previousSeconds: applied,
            revision: cfg.revision || 0
          },
          visibleFilters: { targetCount: (cfg.targets || []).length },
          userIntent: '修改 log 抓取间隔'
        });
      });

      sync();
    })();
  </mavis-script>
  <mavis-fallback>
    当前 log 抓取间隔为 **120 秒**（revision 1，最后修改 2026-09-27T06:16:40+00:00），正在监控 1 个目标：`user/exp-n7`。

    可在 15-900 秒之间调整，步长 15 秒。直接告诉我新数值即可，例如「把 log 抓取间隔改为 45 秒」；确认后写入配置，监控 subagent 会在下一轮抓取时按新间隔执行，无需重启。

    若要更快发现崩溃可调小间隔，但要付出更多请求；间隔过大会让启动即失败的实验长时间不被发现。
  </mavis-fallback>
</mavis-widget>
```
