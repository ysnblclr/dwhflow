// Karşılaştır: iki diyagram çalıştırmasının (ya da bir çalıştırma ile güncel modelin) model farkı.

import { get, p } from '../core/api.js';
import { fmtDate, h } from '../core/dom.js';
import { link } from '../core/router.js';
import { label, store } from '../core/store.js';
import { badge, button, dataTable, empty, layerPill, loading, pageHeader, stat, toast } from '../core/ui.js';

export const refreshOnChange = true;

const CURRENT = 'guncel';
const FIELD_LABELS = {
  name: 'ad', layer: 'katman', kind: 'tür', system: 'sistem', status: 'durum', description: 'açıklama', columns: 'kolonlar',
};
const RUN_STATES = { queued: 'sırada', running: 'sürüyor', error: 'hatalı' };

function kindLabel(value) {
  if (!value) return '—';
  const dataset = label.datasetKind(value);
  return dataset !== value ? dataset : label.processKind(value);
}

function runLabel(run) {
  const state = RUN_STATES[run.state] ? ` (${RUN_STATES[run.state]})` : '';
  return `${fmtDate(run.created_at)} · ${run.created_by || '?'} · ${run.ready ?? 0}/${run.total ?? 0} diyagram${state}`;
}

function fieldValue(field, value) {
  if (value === null || value === undefined || value === '') return '—';
  if (field === 'layer') return label.layer(value);
  if (field === 'status') return label.status(value);
  if (field === 'kind') return kindLabel(value);
  if (Array.isArray(value)) return value.join(', ') || '—';
  return String(value);
}

function section(title, rows, columns, note) {
  if (!rows?.length) return null;
  const table = dataTable({ rows, columns, rowKey: (r) => r.key, filter: rows.length > 15, pageSize: 200 });
  return h('div', { class: 'card' },
    h('div', { class: 'card-head' }, h('h2', { text: title }), badge(String(rows.length), 'muted')),
    note ? h('p', { class: 'small muted', text: note }) : null,
    table.el);
}

function renderDiff(diff, slug) {
  const current = diff.b === CURRENT;
  const nodeLink = (row) => (current && (row.type === 'dataset' || row.type === 'process')
    ? h('a', { href: link('p', slug, row.type === 'dataset' ? 'tablolar' : 'surecler', row.id), text: row.name })
    : h('span', { text: row.name }));
  const nodeColumns = [
    { key: 'name', label: 'Ad', render: nodeLink },
    { key: 'type', label: 'Nesne', render: (r) => (r.type === 'process' ? 'süreç' : 'tablo/dosya') },
    { key: 'layer', label: 'Katman', sort: (r) => label.layer(r.layer), render: (r) => layerPill(r.layer) },
    { key: 'kind', label: 'Tür', sort: (r) => kindLabel(r.kind), render: (r) => kindLabel(r.kind) },
  ];
  const removedColumns = nodeColumns.map((c) => (c.key === 'name' ? { ...c, render: (r) => h('span', { text: r.name }) } : c));
  const changeCell = (r) => {
    if (r.field === 'columns') {
      return h('div', {},
        (r.old || []).length ? h('div', {}, h('span', { class: 'compare-old', text: r.old.join(', ') })) : null,
        (r.new || []).length ? h('div', {}, h('span', { class: 'compare-new', text: `+ ${r.new.join(', ')}` })) : null);
    }
    return h('div', {}, h('span', { class: 'compare-old', text: fieldValue(r.field, r.old) }),
      h('span', { class: 'compare-arrow', text: '→' }), h('span', { class: 'compare-new', text: fieldValue(r.field, r.new) }));
  };
  const linkName = (r) => h('span', {}, h('span', { text: r.source_name }), h('span', { class: 'compare-arrow', text: '→' }), h('span', { text: r.target_name }),
    r.draft ? [' ', badge('taslak', 'warn')] : null);
  const processes = (list) => ((list || []).length ? list.join(', ') : '—');
  const linkColumns = [
    { key: 'name', label: 'Bağ', sort: (r) => `${r.source_name} ${r.target_name}`, render: linkName },
    { key: 'processes', label: 'Süreçler', wrap: true, sort: (r) => processes(r.processes), render: (r) => h('span', { class: 'small', text: processes(r.processes) }) },
  ];
  const keyed = (list, prefix) => (list || []).map((r, i) => ({ ...r, key: `${prefix}${i}` }));

  if (diff.empty) {
    return h('div', { class: 'card' }, empty('Fark yok: iki model aynı.'));
  }
  const nodesChanged = keyed(diff.nodes_changed, 'nc');
  return h('div', { class: 'stack' },
    h('div', { class: 'grid-3' },
      stat(diff.nodes_added?.length || 0, 'eklenen nesne', { kind: diff.nodes_added?.length ? 'ok' : '' }),
      stat(diff.nodes_removed?.length || 0, 'kaldırılan nesne', { kind: diff.nodes_removed?.length ? 'bad' : '' }),
      stat(nodesChanged.length, 'nesne alanı değişti', { kind: nodesChanged.length ? 'warn' : '' }),
      stat(diff.links_added?.length || 0, 'eklenen tablo bağı', { kind: diff.links_added?.length ? 'ok' : '' }),
      stat(diff.links_removed?.length || 0, 'kaldırılan tablo bağı', { kind: diff.links_removed?.length ? 'bad' : '' }),
      stat(diff.links_changed?.length || 0, 'değişen tablo bağı', { kind: diff.links_changed?.length ? 'warn' : '' }),
      stat(`+${diff.edges_added || 0} / −${diff.edges_removed || 0}`, 'ham kenar (kolon eşlemeleri dahil)')),
    section('Eklenen nesneler', keyed(diff.nodes_added, 'na'), nodeColumns),
    section('Kaldırılan nesneler', keyed(diff.nodes_removed, 'nr'), removedColumns),
    section('Değişen nesneler', nodesChanged, [
      { key: 'name', label: 'Ad', render: nodeLink },
      { key: 'field', label: 'Alan', sort: (r) => FIELD_LABELS[r.field] || r.field, render: (r) => FIELD_LABELS[r.field] || r.field },
      { key: 'change', label: 'Eski → yeni', wrap: true, sort: false, render: changeCell },
    ], 'Nesneler kimlikle eşleşir; kimlik addan türediği için yeniden adlandırılan nesne kaldırılmış + eklenmiş görünebilir.'),
    section('Eklenen tablo bağları', keyed(diff.links_added, 'la'), linkColumns),
    section('Kaldırılan tablo bağları', keyed(diff.links_removed, 'lr'), linkColumns),
    section('Değişen tablo bağları', keyed(diff.links_changed, 'lc'), [
      { key: 'name', label: 'Bağ', sort: (r) => `${r.source_name} ${r.target_name}`, render: linkName },
      { key: 'draft', label: 'Durum', sort: (r) => `${r.was_draft}${r.draft}`, render: (r) => (r.was_draft === r.draft
        ? h('span', { class: 'muted', text: r.draft ? 'taslak' : 'onaylı' })
        : h('span', {}, h('span', { class: 'compare-old', text: r.was_draft ? 'taslak' : 'onaylı' }), h('span', { class: 'compare-arrow', text: '→' }),
          h('span', { class: 'compare-new', text: r.draft ? 'taslak' : 'onaylı' }))) },
      { key: 'processes', label: 'Süreçler', wrap: true, sort: false, render: (r) => {
        const before = processes(r.old_processes);
        const after = processes(r.processes);
        return before === after ? h('span', { class: 'small', text: after })
          : h('span', { class: 'small' }, h('span', { class: 'compare-old', text: before }), h('span', { class: 'compare-arrow', text: '→' }),
            h('span', { class: 'compare-new', text: after }));
      } },
    ]));
}

export async function render(root, { slug }, query = {}) {
  const runs = await get(p`/api/projects/${slug}/runs`);
  const base = link('p', slug, 'karsilastir');

  root.append(pageHeader({
    title: 'Karşılaştır',
    subtitle: 'İki diyagram çalıştırmasının ya da bir çalıştırma ile güncel modelin farkı: eklenen, kaldırılan ve değişen nesneler ve tablo bağları.',
    crumbs: [{ label: 'Projeler', href: '#/projeler' }, { label: store.projectInfo?.ad || slug, href: link('p', slug) }, { label: 'Karşılaştır' }],
  }));

  if (!runs.length) {
    root.append(h('div', { class: 'card' }, empty('Henüz diyagram çalıştırması yok.', h('div', { class: 'stack' },
      h('p', { class: 'small', text: 'Karşılaştırma, her diyagram çalıştırmasında saklanan model kopyaları arasında yapılır. Diyagramlar ekranında diyagram ürettiğinizde bir çalıştırma oluşur; sonra onu güncel modelle ya da başka bir çalıştırmayla karşılaştırabilirsiniz.' }),
      h('div', {}, h('a', { class: 'btn primary', href: link('p', slug, 'diyagramlar') }, 'Diyagramlar'))))));
    return;
  }

  const ids = new Set(runs.map((r) => r.id));
  const a = ids.has(query.a) ? query.a : runs[0].id;
  const b = query.b === CURRENT || ids.has(query.b) ? query.b : CURRENT;
  if ((query.a && !ids.has(query.a)) || (query.b && query.b !== CURRENT && !ids.has(query.b))) {
    root.append(h('div', { class: 'banner warn' }, h('span', { class: 'grow', text: 'Adresteki çalıştırma bulunamadı (silinmiş olabilir); en yeni çalıştırma seçildi.' })));
  }

  const selectA = h('select', { 'aria-label': 'Önce (A)', style: { width: 'auto', maxWidth: '100%' } },
    runs.map((r) => h('option', { value: r.id, selected: r.id === a }, runLabel(r))));
  const selectB = h('select', { 'aria-label': 'Sonra (B)', style: { width: 'auto', maxWidth: '100%' } },
    h('option', { value: CURRENT, selected: b === CURRENT }, 'Güncel model (düzeltmeler dahil)'),
    runs.map((r) => h('option', { value: r.id, selected: r.id === b }, runLabel(r))));
  const results = h('div');
  let token = 0;

  const show = async (first, second) => {
    const mine = ++token;
    results.replaceChildren(loading('Karşılaştırılıyor…'));
    try {
      const diff = await get(p`/api/projects/${slug}/compare?a=${first}&b=${second}`);
      if (mine === token) results.replaceChildren(renderDiff(diff, slug));
    } catch (error) {
      if (mine === token) results.replaceChildren(h('div', { class: 'banner bad' }, h('span', { class: 'grow', text: error.detail || error.message })));
    }
  };

  const compare = () => {
    const first = selectA.value;
    const second = selectB.value;
    if (first === second) { toast('Aynı çalıştırma iki kez seçildi', 'warn'); return; }
    const target = `${base}?a=${encodeURIComponent(first)}&b=${encodeURIComponent(second)}`;
    if (location.hash === target) show(first, second);
    else location.hash = target; // adres paylaşılabilir; yönlendirici ekranı yeniden çizer
  };

  root.append(h('div', { class: 'card', style: { marginBottom: '14px' } },
    h('div', { class: 'form-grid' },
      h('label', { class: 'field' }, h('span', { class: 'label', text: 'Önce (A)' }), selectA),
      h('label', { class: 'field' }, h('span', { class: 'label', text: 'Sonra (B)' }), selectB)),
    h('div', { class: 'row', style: { marginTop: '10px' } }, button('Karşılaştır', compare, { kind: 'primary' }),
      h('a', { class: 'btn', href: link('p', slug, 'diyagramlar') }, 'Diyagramlar'),
      h('span', { class: 'small muted', text: 'Bağlar tablo düzeyindedir; süreç üzerinden giden akışlar tek bağ sayılır.' }))),
  results);

  if (a === b) {
    results.replaceChildren(h('div', { class: 'banner warn' }, h('span', { class: 'grow', text: 'Aynı çalıştırma iki kez seçildi; farklı iki model seçin.' })));
  } else {
    await show(a, b);
  }
}
