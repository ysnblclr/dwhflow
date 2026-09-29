// Ortak arayüz bileşenleri. Hepsi DOM düğümü döner; metinler textContent ile yazılır.

import { waitJob } from './api.js';
import { $, append, clear, fmtDate, fold, h } from './dom.js';
import { emit, label } from './store.js';

// ---- bildirim ----------------------------------------------------------- //

export function toast(message, kind = 'info', timeout = 4500) {
  let box = $('.toasts');
  if (!box) { box = h('div', { class: 'toasts', role: 'status', 'aria-live': 'polite' }); document.body.append(box); }
  const node = h('div', { class: `toast ${kind}` }, message);
  box.append(node);
  setTimeout(() => node.remove(), timeout);
}

export function toastError(error) {
  toast(error?.detail || error?.message || String(error), 'bad', 7000);
}

// ---- pencereler ---------------------------------------------------------- //

/**
 * modal({title, body, actions:[{label, kind, value}], wide}) → Promise(value | null)
 * Bir eylemin ``onClick`` işlevi false dönerse pencere kapanmaz (doğrulama için).
 */
export function modal({ title, body, actions = [{ label: 'Kapat', value: null }], wide = false }) {
  return new Promise((resolve) => {
    const previous = document.activeElement;
    let backdrop;
    const close = (value) => {
      backdrop.remove();
      document.removeEventListener('keydown', onKey);
      if (previous && previous.focus) previous.focus();
      resolve(value);
    };
    const onKey = (event) => { if (event.key === 'Escape') close(null); };
    const buttons = actions.map((action) => h('button', {
      class: `btn ${action.kind || ''}`, type: 'button',
      onclick: async () => {
        if (action.onClick) {
          try {
            const result = await action.onClick();
            if (result === false) return;
          } catch (error) { toastError(error); return; }
        }
        close(action.value === undefined ? action.label : action.value);
      },
    }, action.label));
    const dialog = h('div', { class: `modal${wide ? ' wide' : ''}`, role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
      h('div', { class: 'modal-head' }, h('h2', { text: title }),
        h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Kapat', onclick: () => close(null) }, '✕')),
      h('div', { class: 'modal-body' }, body),
      h('div', { class: 'modal-foot' }, buttons));
    backdrop = h('div', { class: 'modal-backdrop', onmousedown: (event) => { if (event.target === backdrop) close(null); } }, dialog);
    document.body.append(backdrop);
    document.addEventListener('keydown', onKey);
    const focusable = dialog.querySelector('input, select, textarea') || buttons[buttons.length - 1];
    if (focusable) focusable.focus();
  });
}

export async function confirmDialog(message, { title = 'Onay', confirmLabel = 'Evet', danger = false } = {}) {
  const result = await modal({
    title, body: h('p', { text: message }),
    actions: [{ label: 'Vazgeç', value: false }, { label: confirmLabel, value: true, kind: danger ? 'danger solid' : 'primary' }],
  });
  return result === true;
}

/**
 * formDialog({title, fields, submitLabel, validate}) → Promise(values | null)
 * fields: [{name, label, type: text|password|number|select|textarea|checkbox, options, value, required, hint}]
 */
export function formDialog({ title, fields, submitLabel = 'Kaydet', intro, wide = false, validate }) {
  const form = buildForm(fields);
  const body = h('div', { class: 'stack' }, intro ? h('p', { class: 'muted', text: intro }) : null, form.el);
  let values = null;
  return modal({
    title, body, wide,
    actions: [
      { label: 'Vazgeç', value: null },
      {
        label: submitLabel, kind: 'primary', value: 'ok',
        onClick: () => {
          const missing = fields.filter((f) => f.required && !String(form.values()[f.name] ?? '').trim());
          if (missing.length) { toast(`Zorunlu alan: ${missing.map((f) => f.label).join(', ')}`, 'warn'); return false; }
          values = form.values();
          const problem = validate ? validate(values) : null;
          if (problem) { toast(problem, 'warn'); return false; }
          return true;
        },
      },
    ],
  }).then((result) => (result === 'ok' ? values : null));
}

export function field({ name, label: text, type = 'text', options = [], value, required, hint, placeholder, wide }) {
  let input;
  if (type === 'select') {
    input = h('select', { name }, options.map((o) => h('option', { value: o.value ?? '', selected: String(o.value ?? '') === String(value ?? '') }, o.label)));
  } else if (type === 'textarea') {
    input = h('textarea', { name, placeholder, rows: 4 });
    input.value = value ?? '';
  } else if (type === 'checkbox') {
    input = h('input', { type: 'checkbox', name, checked: !!value });
    return h('label', { class: `field inline${wide ? ' wide' : ''}` }, input, h('span', { text }), hint ? h('span', { class: 'hint', text: hint }) : null);
  } else {
    input = h('input', { type, name, placeholder, required, autocomplete: type === 'password' ? 'new-password' : 'off' });
    input.value = value ?? '';
  }
  return h('label', { class: `field${wide ? ' wide' : ''}` },
    h('span', { class: 'label' }, text, required ? ' *' : ''), input, hint ? h('span', { class: 'hint', text: hint }) : null);
}

export function buildForm(fields) {
  const el = h('div', { class: 'form-grid' }, fields.map((f) => field(f)));
  const values = () => {
    const out = {};
    for (const f of fields) {
      const input = el.querySelector(`[name="${CSS.escape(f.name)}"]`);
      if (!input) continue;
      if (f.type === 'checkbox') out[f.name] = input.checked;
      else if (f.type === 'number') out[f.name] = input.value === '' ? null : Number(input.value);
      else out[f.name] = input.value;
    }
    return out;
  };
  return { el, values };
}

// ---- sayfa parçaları ---------------------------------------------------- //

export function pageHeader({ title, subtitle, crumbs = [], actions = [] }) {
  return h('div', { class: 'page-header' },
    h('div', { class: 'titles' },
      crumbs.length ? h('div', { class: 'crumbs' }, crumbs.map((c, i) => [i ? ' › ' : '', c.href ? h('a', { href: c.href, text: c.label }) : c.label])) : null,
      h('h1', { text: title }), subtitle ? h('div', { class: 'subtitle' }, subtitle) : null),
    h('div', { class: 'row' }, actions));
}

export function button(text, onclick, { kind = '', title, disabled, type = 'button' } = {}) {
  return h('button', { class: `btn ${kind}`, type, title, disabled, onclick }, text);
}

export function loading(text = 'Yükleniyor…') {
  return h('div', { class: 'loading' }, h('span', { class: 'spinner' }), ' ', text);
}

export function empty(text, action) {
  return h('div', { class: 'empty' }, h('span', { class: 'icon', text: '∅' }), h('div', { text }), action ? h('div', { style: { marginTop: '10px' } }, action) : null);
}

export function badge(text, kind = 'muted') {
  return h('span', { class: `badge ${kind}` }, text);
}

export function statusBadge(status) {
  const kinds = { draft: 'warn', confirmed: 'ok', deprecated: 'muted' };
  return badge(label.status(status), kinds[status] || 'muted');
}

export function layerPill(layer) {
  return h('span', { class: `layer ${layer}`, text: label.layer(layer) });
}

export function docStatusBadge(status) {
  const map = { ok: ['okundu', 'ok'], empty: ['akış yok', 'warn'], unsupported: ['desteklenmiyor', 'warn'], error: ['hata', 'bad'] };
  const [text, kind] = map[status] || [status || 'okunmadı', 'muted'];
  return badge(text, kind);
}

export function severityBadge(severity) {
  const map = { error: ['hata', 'bad'], warning: ['uyarı', 'warn'], info: ['bilgi', 'info'] };
  const [text, kind] = map[severity] || [severity, 'muted'];
  return badge(text, kind);
}

export function originBadge(origin) {
  const text = { varsayilan: 'varsayılan', genel: 'genel', proje: 'proje' }[origin] || origin;
  return h('span', { class: `origin ${origin}`, title: 'Bu değerin geldiği ayar katmanı', text });
}

export function tabs(items, active, onChange) {
  const bar = h('div', { class: 'tabs', role: 'tablist' });
  for (const item of items) {
    bar.append(h('button', {
      type: 'button', role: 'tab', class: item.id === active ? 'active' : '', 'aria-selected': String(item.id === active),
      onclick: () => onChange(item.id),
    }, item.label, item.count ? h('span', { class: 'badge muted', style: { marginLeft: '6px' }, text: String(item.count) }) : null));
  }
  return bar;
}

export function stat(value, text, { href, kind } = {}) {
  const inner = [h('b', { text: value == null ? '—' : String(value) }), h('span', { text })];
  return href ? h('a', { class: `stat ${kind || ''}`, href }, inner) : h('div', { class: `stat ${kind || ''}` }, inner);
}

export function evidence(source) {
  const where = [source.file, source.locator].filter(Boolean).join(' · ');
  const who = source.extractor ? ` (${source.extractor})` : '';
  return h('div', { class: 'evidence' },
    h('span', { class: 'src', text: `${where || 'kaynak yok'}${who}` }),
    source.evidence ? h('span', { text: source.evidence }) : null);
}

// ---- menü -------------------------------------------------------------- //

/** Açılır menü. items: [{label, href | onClick, danger, hidden, separator, heading}] */
export function dropdown(trigger, items, { alignRight = false, className = 'nav-item' } = {}) {
  const wrap = h('div', { class: 'menu' });
  const list = h('div', { class: `menu-list hidden${alignRight ? ' align-right' : ''}`, role: 'menu' });
  const button = h('button', { type: 'button', class: className, 'aria-haspopup': 'true', 'aria-expanded': 'false' }, trigger);
  const close = () => { list.classList.add('hidden'); button.setAttribute('aria-expanded', 'false'); document.removeEventListener('mousedown', outside); };
  const outside = (event) => { if (!wrap.contains(event.target)) close(); };
  button.addEventListener('click', () => {
    const opening = list.classList.contains('hidden');
    document.querySelectorAll('.menu-list').forEach((m) => m.classList.add('hidden'));
    if (opening) {
      clear(list);
      for (const item of (typeof items === 'function' ? items() : items)) {
        if (item.hidden) continue;
        if (item.separator) { list.append(h('div', { class: 'menu-sep' })); continue; }
        if (item.heading) { list.append(h('div', { class: 'menu-label', text: item.heading })); continue; }
        const node = item.href
          ? h('a', { href: item.href, role: 'menuitem', class: item.danger ? 'danger' : '', onclick: close }, item.label)
          : h('button', { type: 'button', role: 'menuitem', class: item.danger ? 'danger' : '', onclick: () => { close(); item.onClick(); } }, item.label);
        list.append(node);
      }
      list.classList.remove('hidden');
      button.setAttribute('aria-expanded', 'true');
      document.addEventListener('mousedown', outside);
    } else close();
  });
  wrap.append(button, list);
  return wrap;
}

// ---- tablo ------------------------------------------------------------- //

/**
 * dataTable({columns, rows, onRowClick, rowKey, selectable, filter, empty, pageSize})
 * columns: [{key, label, render(row) → Node|string, sort: (row) → değer | false, num, width, wrap}]
 * Dönüş: {el, setRows(rows), selected() → [key], select(key)}
 */
export function dataTable({ columns, rows = [], onRowClick, rowKey = (r) => r.id, selectable = false,
  filter = true, empty: emptyText = 'Kayıt yok', pageSize = 300, activeKey = null }) {
  let data = rows;
  let sortKey = null;
  let sortDir = 1;
  let query = '';
  let limit = pageSize;
  let active = activeKey;
  const chosen = new Set();
  const wrap = h('div');
  const tbody = h('tbody');
  const foot = h('div', { class: 'table-foot' });
  const search = filter ? h('input', { type: 'search', placeholder: 'Süz…', 'aria-label': 'Tabloyu süz' }) : null;
  const headRow = h('tr');
  const selectAll = selectable ? h('input', { type: 'checkbox', 'aria-label': 'Tümünü seç' }) : null;

  if (selectable) headRow.append(h('th', { style: { width: '28px' } }, selectAll));
  for (const column of columns) {
    const sortable = column.sort !== false;
    const th = h('th', { class: [sortable ? 'sortable' : '', column.num ? 'num' : ''], style: column.width ? { width: column.width } : null }, column.label);
    if (sortable) th.addEventListener('click', () => { sortDir = sortKey === column.key ? -sortDir : 1; sortKey = column.key; draw(); });
    headRow.append(th);
  }

  const valueOf = (row, column) => {
    if (typeof column.sort === 'function') return column.sort(row);
    const v = row[column.key];
    return v == null ? '' : v;
  };

  function visible() {
    let out = data;
    if (query) {
      const needle = fold(query);
      out = out.filter((row) => columns.some((c) => fold(String(valueOf(row, c))).includes(needle)));
    }
    if (sortKey) {
      const column = columns.find((c) => c.key === sortKey);
      out = [...out].sort((a, b) => {
        const x = valueOf(a, column); const y = valueOf(b, column);
        if (typeof x === 'number' && typeof y === 'number') return (x - y) * sortDir;
        return String(x).localeCompare(String(y), 'tr') * sortDir;
      });
    }
    return out;
  }

  function draw() {
    clear(tbody);
    const list = visible();
    headRow.querySelectorAll('th .arrow').forEach((a) => a.remove());
    if (sortKey) {
      const index = columns.findIndex((c) => c.key === sortKey) + (selectable ? 1 : 0);
      headRow.children[index]?.append(h('span', { class: 'arrow', text: sortDir > 0 ? '▲' : '▼' }));
    }
    if (!list.length) {
      tbody.append(h('tr', {}, h('td', { colspan: columns.length + (selectable ? 1 : 0) }, empty(query ? 'Eşleşen kayıt yok' : emptyText))));
    }
    for (const row of list.slice(0, limit)) {
      const key = rowKey(row);
      const tr = h('tr', { class: [onRowClick ? 'clickable' : '', key === active ? 'selected' : ''] });
      if (selectable) {
        const box = h('input', { type: 'checkbox', checked: chosen.has(key), 'aria-label': 'Seç', onclick: (e) => e.stopPropagation(),
          onchange: (e) => { if (e.target.checked) chosen.add(key); else chosen.delete(key); updateFoot(list.length); } });
        tr.append(h('td', {}, box));
      }
      for (const column of columns) {
        const content = column.render ? column.render(row) : row[column.key];
        tr.append(h('td', { class: [column.num ? 'num' : '', column.wrap ? 'wrap' : ''] }, content ?? (column.render ? '' : '—')));
      }
      if (onRowClick) tr.addEventListener('click', () => { active = key; tbody.querySelectorAll('tr.selected').forEach((r) => r.classList.remove('selected')); tr.classList.add('selected'); onRowClick(row); });
      tbody.append(tr);
    }
    updateFoot(list.length);
  }

  function updateFoot(total) {
    clear(foot);
    foot.append(`${total.toLocaleString('tr-TR')} kayıt`);
    if (selectable && chosen.size) foot.append(` · ${chosen.size} seçili`);
    if (total > limit) foot.append(' · ', h('a', { href: '#', onclick: (e) => { e.preventDefault(); limit += pageSize; draw(); } }, `${Math.min(pageSize, total - limit)} tane daha göster`));
  }

  if (selectAll) selectAll.addEventListener('change', () => {
    for (const row of visible()) { const key = rowKey(row); if (selectAll.checked) chosen.add(key); else chosen.delete(key); }
    draw();
  });
  if (search) search.addEventListener('input', () => { query = search.value; limit = pageSize; draw(); });

  append(wrap, [search ? h('div', { class: 'toolbar' }, search) : null,
    h('div', { class: 'card flush' }, h('div', { class: 'table-wrap' }, h('table', { class: 'data' }, h('thead', {}, headRow), tbody)), foot)]);
  draw();
  return {
    el: wrap,
    setRows(next) { data = next; for (const key of [...chosen]) if (!data.some((r) => rowKey(r) === key)) chosen.delete(key); draw(); },
    selected: () => [...chosen],
    clearSelection() { chosen.clear(); if (selectAll) selectAll.checked = false; draw(); },
    setActive(key) { active = key; draw(); },
  };
}

// ---- dosya bırakma ------------------------------------------------------ //

export function dropzone({ onFiles, accept = '', text = 'Dosyaları buraya bırakın ya da seçmek için tıklayın', hint }) {
  const input = h('input', { type: 'file', multiple: true, accept, class: 'hidden' });
  const zone = h('div', { class: 'dropzone', tabindex: '0', role: 'button' }, h('div', { text }), hint ? h('div', { class: 'small muted', text: hint }) : null, input);
  zone.addEventListener('click', () => input.click());
  zone.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('hot'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('hot'));
  zone.addEventListener('drop', (e) => { e.preventDefault(); zone.classList.remove('hot'); if (e.dataTransfer.files.length) onFiles([...e.dataTransfer.files]); });
  input.addEventListener('change', () => { if (input.files.length) onFiles([...input.files]); input.value = ''; });
  return zone;
}

// ---- arka plan işleri --------------------------------------------------- //

/**
 * İşi sol alttaki panelde izler; bitince bildirir ve 'project:changed' yayar.
 * Dönüş: işin son hâli (hata olursa null).
 */
export async function trackJob(job, { title, onDone } = {}) {
  if (!job) return null;
  let dock = $('.jobs-dock');
  if (!dock) { dock = h('div', { class: 'jobs-dock' }); document.body.append(dock); }
  const msg = h('div', { class: 'msg', text: 'sırada…' });
  const card = h('div', { class: 'job' }, h('div', { class: 'title', text: title || job.title || 'İş' }), msg, h('div', { class: 'progress' }, h('div')));
  dock.append(card);
  try {
    const final = await waitJob(job.id, (current) => {
      const last = current.progress?.[current.progress.length - 1];
      msg.textContent = last ? last.message : current.state;
    });
    card.remove();
    toast(`${title || job.title || 'İş'} tamamlandı`, 'ok');
    emit('project:changed', { job: final });
    if (onDone) onDone(final);
    return final;
  } catch (error) {
    card.remove();
    toast(`${title || job.title || 'İş'} başarısız: ${error.detail || error.message}`, 'bad', 9000);
    emit('project:changed', { job });
    return null;
  }
}

export function when(iso) {
  return h('span', { class: 'nowrap', title: iso || '', text: fmtDate(iso) });
}
