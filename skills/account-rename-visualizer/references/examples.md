# Examples

One representative, complete `<mavis-widget>` for the rename panel, grounded in the shape
in `data-schema.json`.

## Scenario

Two accounts are stored, and one is active:

```
* qwyi123    (qwyi123)
  xishengfeng (xishengfeng)
```

The user wants the stored names to match the Kaggle usernames. The panel lists both accounts
with the active one marked, selects the first, and pre-fills the input with its username so
the common case is a single confirm.

Notes on the choices:

- `kind="form"` — a text input plus a confirm button is structured input collection. No
  `new Chart(...)`: Chart.js is not injected for this kind, and a rename has no series.
- Only the confirm button sends. Changing the radio or typing sends nothing, because a
  rename is a decision and a keystroke is not one.
- The confirm button is **enabled** here, because `qwyi123` differs from the stored name
  `work` — the rename would actually do something.
- No token appears anywhere in the data, the DOM, or the prompt metadata.
- The input is pre-filled from `username`, so the default proposal is the identity the user
  asked names to follow.

```html
<mavis-widget
  version="mavis.widget.v1"
  kind="form"
  title="重命名 Kaggle 账号"
  height="400"
  min-height="320"
  max-height="640"
  streaming="html-first"
  capabilities="resize,sendPrompt"
  theme="app"
  token-set="mavis.semantic.v1">
  <mavis-meta type="json">{"summary":"选择账号并确认新名称；默认填入 Kaggle 用户名，改名保留 token 与登录状态","dataSource":"kaggle_accounts action=list","chartType":"table","interactions":[{"id":"rename-account","label":"确认改名","capability":"sendPrompt"}]}</mavis-meta>
  <mavis-style>
    body {
      margin: 0;
      padding: 16px;
      background: var(--mw-bg);
      color: var(--mw-text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      font-size: 14px;
    }
    .field-label { font-size: 12px; color: var(--mw-text-muted); margin-bottom: 8px; }
    .accounts { display: flex; flex-direction: column; gap: 8px; margin-bottom: 18px; }
    .acct {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 12px;
      border: 1px solid var(--mw-border);
      border-radius: 8px;
      background: var(--mw-surface);
      cursor: pointer;
    }
    .acct.sel { border-color: var(--mw-accent); }
    .acct input { accent-color: var(--mw-accent); margin: 0; flex: 0 0 auto; }
    .acct-main { flex: 1; min-width: 0; }
    .acct-name { font-weight: 600; color: var(--mw-text); }
    .acct-sub { font-size: 12px; color: var(--mw-text-muted); margin-top: 2px; }
    .badge {
      flex: 0 0 auto;
      font-size: 11px;
      padding: 2px 8px;
      border-radius: 999px;
      border: 1px solid var(--mw-accent);
      color: var(--mw-accent);
    }
    .from-to { font-size: 12px; color: var(--mw-text-subtle); margin-bottom: 8px; }
    .row { display: flex; align-items: center; gap: 12px; }
    .name-input {
      flex: 1;
      min-width: 0;
      padding: 9px 12px;
      border-radius: 8px;
      border: 1px solid var(--mw-border);
      background: var(--mw-bg);
      color: var(--mw-text);
      font-size: 14px;
      font-family: inherit;
    }
    .name-input:focus { outline: 2px solid var(--mw-accent); outline-offset: -1px; }
    .confirm {
      flex: 0 0 auto;
      min-width: 104px;
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
    .hint { font-size: 12px; color: var(--mw-text-muted); margin-top: 10px; }
    .hint.bad { color: var(--mw-danger); }
    .hint.warn { color: var(--mw-warning); }
    .rule { font-size: 11px; color: var(--mw-text-subtle); margin-top: 6px; }
  </mavis-style>
  <mavis-html>
    <div class="field-label">选择账号</div>
    <div class="accounts" id="accounts"></div>
    <div class="field-label">新名称</div>
    <div class="from-to" id="from-to"></div>
    <div class="row">
      <input type="text" class="name-input" id="name-input" maxlength="64"
             aria-label="新的账号名称">
      <button class="confirm" id="confirm">确认改名</button>
    </div>
    <div class="hint" id="hint"></div>
    <div class="rule">名称可用范围：1-64 位字母、数字、点、连字符或下划线，且以字母或数字开头。</div>
  </mavis-html>
  <mavis-data name="main" type="json">[{"configured":true,"active":"work","accounts":[{"name":"work","username":"qwyi123","display":"qwyi123","active":true,"renamed_from":null},{"name":"second","username":"xishengfeng","display":"xishengfeng","active":false,"renamed_from":null}]}]</mavis-data>
  <mavis-script>
    (function () {
      var cfg = (window.__mavisData['main'] || [])[0] || {};
      var accounts = cfg.accounts || [];
      var listEl = document.getElementById('accounts');
      var fromToEl = document.getElementById('from-to');
      var inputEl = document.getElementById('name-input');
      var confirmBtn = document.getElementById('confirm');
      var hintEl = document.getElementById('hint');
      var NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
      var selected = null;

      function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
          return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
      }

      if (!accounts.length) {
        listEl.innerHTML = '<div class="acct"><div class="acct-main"><div class="acct-sub">还没有保存任何账号。用 kaggle_accounts action="add" 添加后再来改名。</div></div></div>';
        inputEl.disabled = true;
        confirmBtn.disabled = true;
        hintEl.textContent = '没有可改名的账号。';
        return;
      }

      listEl.innerHTML = accounts.map(function (a, i) {
        var ident = a.username ? (a.username + '  ·  Kaggle 用户名 ' + a.username) : a.name;
        var sub = a.username && a.username !== a.name
          ? '存储名 ' + a.name + '  ·  ' + ident
          : ident;
        return '<label class="acct' + (i === 0 ? ' sel' : '') + '" data-idx="' + i + '">' +
          '<input type="radio" name="acct" value="' + i + '"' + (i === 0 ? ' checked' : '') + '>' +
          '<span class="acct-main"><span class="acct-name">' + esc(a.display || a.name) + '</span>' +
          '<span class="acct-sub">' + esc(sub) + '</span></span>' +
          (a.active ? '<span class="badge">当前使用</span>' : '') + '</label>';
      }).join('');

      function current() {
        var i = parseInt(inputEl.value, 10);
        return isNaN(i) ? null : accounts[i];
      }

      function select(idx) {
        selected = accounts[idx];
        listEl.querySelectorAll('.acct').forEach(function (el, i) {
          el.classList.toggle('sel', i === idx);
        });
        fromToEl.textContent = '当前存储名：' + selected.name + '　→　新名称';
        inputEl.value = selected.username || selected.name;
        validate();
      }

      function validate() {
        var acct = current();
        var value = (inputEl.value || '').trim();
        hintEl.className = 'hint';
        if (!acct) {
          hintEl.textContent = '请先选择一个账号。';
          confirmBtn.disabled = true;
          return;
        }
        if (!value) {
          hintEl.textContent = '请输入新名称。';
          confirmBtn.disabled = true;
          return;
        }
        if (!NAME_RE.test(value)) {
          hintEl.className = 'hint bad';
          hintEl.textContent = '名称不合法：只能使用字母、数字、点、连字符或下划线，且必须以字母或数字开头。';
          confirmBtn.disabled = true;
          return;
        }
        if (value === acct.name) {
          hintEl.textContent = '名称未改变。';
          confirmBtn.disabled = true;
          return;
        }
        if (value === acct.username && acct.name === acct.username) {
          hintEl.textContent = '名称与 Kaggle 用户名一致，无需改名。';
          confirmBtn.disabled = true;
          return;
        }
        var clash = accounts.some(function (a) { return a.name === value; });
        if (clash) {
          hintEl.className = 'hint warn';
          hintEl.textContent = '名称「' + value + '」已被另一个账号使用，请换一个。';
          confirmBtn.disabled = true;
          return;
        }
        hintEl.textContent = '改名后 token、登录状态和原添加时间都不变，仍以原账号登录。';
        confirmBtn.disabled = false;
      }

      listEl.querySelectorAll('input[type="radio"]').forEach(function (radio) {
        radio.addEventListener('change', function () {
          select(parseInt(radio.value, 10));
        });
      });
      inputEl.addEventListener('input', validate);

      confirmBtn.addEventListener('click', function () {
        var acct = current();
        var value = (inputEl.value || '').trim();
        if (!acct || confirmBtn.disabled) { return; }
        window.mavis.sendPrompt('把 Kaggle 账号 ' + acct.name + ' 改名为 ' + value, {
          widgetTitle: '重命名 Kaggle 账号',
          action: 'rename-account',
          selectedData: { from: acct.name, to: value, username: acct.username || '' },
          visibleFilters: { accountCount: accounts.length },
          userIntent: '重命名 Kaggle 账号'
        });
      });

      select(0);
    })();
  </mavis-script>
  <mavis-fallback>
    当前有 2 个已保存的 Kaggle 账号：**work**（Kaggle 用户名 `qwyi123`，当前使用中）和 **second**（Kaggle 用户名 `xishengfeng`）。

    告诉我改哪一个、改成什么即可，例如「把 Kaggle 账号 work 改名为 qwyi123」。改名只改存储名，token、登录状态和原添加时间都不变，仍然以原账号登录。

    名称可用范围：1-64 位字母、数字、点、连字符或下划线，且以字母或数字开头。名称不能与其他账号重复。
  </mavis-fallback>
</mavis-widget>
```
