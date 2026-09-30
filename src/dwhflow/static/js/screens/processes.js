// Süreçler ve job'lar: süzülebilir liste (sol) + seçili sürecin okuma/yazma, sıra, zamanlama, etki ve düzeltmeleri (sağ).

import { ApiError, get, p } from '../core/api.js';
import { clear, debounce, fold, h } from '../core/dom.js';
import { go, link } from '../core/router.js';
import { canEdit, label, options } from '../core/store.js';
import { badge, button, dataTable, empty, layerPill, pageHeader, statusBadge, toastError } from '../core/ui.js';
import * as edit from './_model_edit.js';

export const refreshOnChange = true;

const memory = { slug: null, filters: null };
const blankFilters = () => ({ q: '', kind: '', status: '', tool: '' });
const STATUS_OPTIONS = [{ value: 'draft', label: 'taslak' }, { value: 'confirmed', label: 'onaylı' }];

function listUrl(slug, filters) {
  const params = new URLSearchParams({ limit: '2000' });
  for (const key of ['q', 'kind', 'status']) if (filters[key]) params.set(key, filters[key]);
  return `${p`/api/projects/${slug}/processes`}?${params}`;
}

async function fetchDetail(slug, id) {
  try {
    return await get(p`/api/projects/${slug}/processes/${id}`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return { missing: true, id };
    throw error;
  }
}

export async function render(root, { slug, id }) {
  if (memory.slug !== slug) { memory.slug = slug; memory.filters = blankFilters(); }
  const filters = memory.filters;
  const [list, detail] = await Promise.all([get(listUrl(slug, filters)), id ? fetchDetail(slug, id) : Promise.resolve(null)]);
  const ctx = {
    slug,
    datasets: edit.lazy(() => edit.loadAll(slug, 'datasets')),
    processes: edit.lazy(() => edit.loadAll(slug, 'processes')),
  };
  const current = detail && !detail.missing ? detail : null;

  root.append(pageHeader({
    title: 'Süreçler ve job\'lar',
    subtitle: 'Tabloları okuyan/yazan süreçler, job sırası ve zamanlama; seçince ayrıntı ve düzeltmeler görünür.',
    crumbs: [{ label: 'Pano', href: link('p', slug) }, { label: 'Süreçler' }],
    actions: [
      current ? button('Etki analizi', edit.act(() => edit.showImpact(slug, current.id, current.name))) : null,
      canEdit() ? button('+ Yeni süreç', edit.act(() => edit.createNode(slug, 'process', 'surecler')), { kind: 'primary' }) : null,
    ],
  }));
  root.append(h('div', { class: 'split' }, listPane(slug, list, filters, id), detailPane(ctx, detail)));
  edit.restoreScroll();
}

// ---- liste ---------------------------------------------------------------- //

function listPane(slug, first, filters, activeId) {
  if (!first.total && !Object.values(filters).some(Boolean)) {
    return h('div', { class: 'card' }, empty('Bu projede henüz süreç yok. Job listesi, zamanlayıcı dosyası ya da STTM içeren doküman yükleyin.',
      h('a', { class: 'btn primary', href: link('p', slug, 'dokumanlar') }, 'Dokümanlar')));
  }
  let last = first;
  const visible = () => last.items.filter((r) => !filters.tool || fold(r.tool || '').includes(fold(filters.tool)));
  const table = dataTable({
    rows: visible(), filter: false, rowKey: (r) => r.id, activeKey: activeId || null,
    empty: 'Süzgeçlerle eşleşen süreç yok',
    onRowClick: (r) => { edit.keepScroll(); go('p', slug, 'surecler', r.id); },
    columns: [
      { key: 'name', label: 'Ad', render: (r) => h('span', {},
        h('a', { href: link('p', slug, 'surecler', r.id), text: r.name, onclick: () => edit.keepScroll() }),
        r.manual ? [' ', badge('elle', 'info')] : null) },
      { key: 'kind', label: 'Tür', render: (r) => label.processKind(r.kind), sort: (r) => label.processKind(r.kind) },
      { key: 'tool', label: 'Araç', render: (r) => r.tool || '—' },
      { key: 'load_strategy', label: 'Yükleme', render: (r) => (r.load_strategy ? label.load(r.load_strategy) : '—') },
      { key: 'schedule', label: 'Zamanlama', render: (r) => r.schedule || '—' },
      { key: 'io', label: 'Okur / yazar', num: true, sort: (r) => r.reads + r.writes,
        render: (r) => h('span', { class: 'nowrap', title: `${r.reads} tablo okur, ${r.writes} tablo yazar`, text: `${r.reads} / ${r.writes}` }) },
      { key: 'order', label: 'Önce / sonra', num: true, sort: (r) => r.predecessors + r.successors,
        render: (r) => h('span', { class: 'nowrap', title: `${r.predecessors} önceki, ${r.successors} sonraki job`, text: `${r.predecessors} / ${r.successors}` }) },
      { key: 'status', label: 'Durum', render: (r) => statusBadge(r.status), sort: (r) => label.status(r.status) },
      { key: 'findings', label: 'Bulgu', num: true, render: (r) => (r.findings ? badge(String(r.findings), 'warn') : '0') },
    ],
  });

  const info = h('span', { class: 'small muted' });
  const showInfo = () => {
    info.textContent = last.total > last.items.length
      ? `İlk ${last.items.length} / ${last.total} süreç alındı; süzgeçle daraltın.`
      : `${visible().length} / ${last.total} süreç`;
  };
  let seq = 0;
  const reload = async () => {
    const mine = ++seq;
    try {
      const data = await get(listUrl(slug, filters));
      if (mine !== seq) return;
      last = data;
      table.setRows(visible());
      showInfo();
    } catch (error) { if (mine === seq) toastError(error); }
  };
  const refilter = () => { table.setRows(visible()); showInfo(); };

  const text = h('input', { type: 'search', placeholder: 'Ad ara…', 'aria-label': 'Ada göre ara', value: filters.q });
  text.addEventListener('input', debounce(() => { filters.q = text.value.trim(); reload(); }, 250));
  const tool = h('input', { type: 'search', placeholder: 'Araç…', 'aria-label': 'Araca göre süz', value: filters.tool, style: { maxWidth: '160px' } });
  tool.addEventListener('input', debounce(() => { filters.tool = tool.value.trim(); refilter(); }, 200));
  const select = (key, allLabel, items) => {
    const node = h('select', { 'aria-label': allLabel }, h('option', { value: '' }, allLabel), items.map((o) => h('option', { value: o.value }, o.label)));
    node.value = filters[key];
    node.addEventListener('change', () => { filters[key] = node.value; reload(); });
    return node;
  };
  const kind = select('kind', 'Tüm türler', options('process_kinds'));
  const status = select('status', 'Tüm durumlar', STATUS_OPTIONS);
  const reset = button('Temizle', () => {
    Object.assign(filters, blankFilters());
    text.value = ''; tool.value = ''; kind.value = ''; status.value = '';
    reload();
  }, { kind: 'ghost small', title: 'Süzgeçleri temizle' });
  showInfo();
  return h('div', {}, h('div', { class: 'toolbar' }, text, kind, status, tool, reset, h('span', { class: 'grow' }), info), table.el);
}

// ---- ayrıntı -------------------------------------------------------------- //

function namedList(slug, items, screen, emptyText) {
  if (!items.length) return edit.note(emptyText);
  return h('ul', { class: 'list' }, items.map((x) => h('li', {}, h('a', { href: link('p', slug, screen, x.id), text: x.name }))));
}

const DAY_LABELS = { all: 'her gün', mo: 'Pzt', tu: 'Sal', we: 'Çar', th: 'Per', fr: 'Cum', sa: 'Cmt', su: 'Paz' };

function scheduleView(s) {
  if (!s || !Object.keys(s).length) return edit.note('Zamanlama bilgisi yok.');
  const days = (s.days_of_week || []).map((d) => DAY_LABELS[String(d).toLowerCase()] || d);
  return h('dl', { class: 'kv' },
    s.start_times?.length ? [h('dt', { text: 'Başlama saatleri' }), h('dd', { text: s.start_times.join(', ') })] : null,
    days.length ? [h('dt', { text: 'Günler' }), h('dd', { text: days.join(', ') })] : null,
    s.cron ? [h('dt', { text: 'Cron' }), h('dd', { class: 'mono', text: s.cron })] : null,
    s.calendar ? [h('dt', { text: 'Takvim' }), h('dd', { text: s.calendar })] : null,
    s.event ? [h('dt', { text: 'Olay' }), h('dd', { text: s.event })] : null);
}

function detailPane(ctx, d) {
  const { slug } = ctx;
  if (!d) return h('div', { class: 'card detail' }, empty('Ayrıntı ve düzenleme için listeden bir süreç seçin.'));
  if (d.missing) {
    return h('div', { class: 'card detail' }, empty('Bu süreç modelde yok: silinmiş, birleştirilmiş ya da yeniden okumada adı değişmiş olabilir.',
      h('a', { class: 'btn', href: link('p', slug, 'surecler') }, 'Listeye dön')));
  }
  const known = edit.knownRefs([d, ...d.reads, ...d.writes, ...d.predecessors, ...d.successors]);
  const small = { kind: 'small' };
  const tools = canEdit() ? h('div', { class: 'row', style: { margin: '10px 0 4px' } },
    button('Yeniden adlandır', edit.act(() => edit.renameNode(slug, d)), small),
    button('Tür', edit.act(() => edit.setKind(slug, d, 'process_kinds')), small),
    button('Katman', edit.act(() => edit.setLayer(slug, d)), small),
    button('Sistem', edit.act(() => edit.setSystem(slug, d, ctx.processes)), small),
    button('Açıklama', edit.act(() => edit.setDescription(slug, d)), small),
    d.status === 'draft' ? button('Onayla', edit.act(() => edit.confirmNode(slug, d)), { kind: 'small ok' }) : null,
    button('Birleştir', edit.act(() => edit.mergeNodes(slug, d, { candidates: ctx.processes, screen: 'surecler', noun: 'süreç' })), small),
    button('Bağ ekle', edit.act(() => addLink(ctx, d, 'reads', known)), small),
    button('Sil', edit.act(() => edit.deleteNode(slug, d, 'surecler')), { kind: 'small danger' })) : null;
  const plus = (preset) => (canEdit() ? button('+ Bağ', edit.act(() => addLink(ctx, d, preset, known)), { kind: 'small ghost' }) : null);
  const impact = d.impact || { processes: 0, datasets: 0, consumption: [] };

  return h('div', { class: 'card detail' },
    h('h2', { style: { overflowWrap: 'anywhere' }, text: d.name }),
    h('div', { class: 'row' }, statusBadge(d.status), badge(label.processKind(d.kind), 'muted'), layerPill(d.layer),
      d.manual ? badge('elle eklendi/düzeltildi', 'info') : null),
    tools,
    h('dl', { class: 'kv', style: { marginTop: '12px' } },
      h('dt', { text: 'Kimlik' }), h('dd', { class: 'mono', text: d.id }),
      h('dt', { text: 'Araç' }), h('dd', { text: d.tool || '—' }),
      h('dt', { text: 'Sistem' }), h('dd', { text: d.system || '—' }),
      h('dt', { text: 'Yükleme' }), h('dd', { text: d.load_strategy ? label.load(d.load_strategy) : '—' }),
      h('dt', { text: 'Zamanlama' }), h('dd', { text: d.schedule || '—' }),
      h('dt', { text: 'Açıklama' }), h('dd', { text: d.description || '—' }),
      h('dt', { text: 'Dokümanlar' }), h('dd', {}, d.files.length ? d.files.map((f, i) => [i ? ', ' : '', edit.docLink(slug, f)]) : '—')),
    edit.sectionHead('Okuduğu tablolar', { count: d.reads.length, actions: [plus('reads')] }),
    namedList(slug, d.reads, 'tablolar', 'Okuduğu tablo yok.'),
    edit.sectionHead('Yazdığı tablolar', { count: d.writes.length, actions: [plus('writes')] }),
    namedList(slug, d.writes, 'tablolar', 'Yazdığı tablo yok.'),
    edit.sectionHead('Önce çalışanlar', { count: d.predecessors.length, actions: [plus('after')] }),
    namedList(slug, d.predecessors, 'surecler', 'Bu süreçten önce çalışması gereken job yok.'),
    edit.sectionHead('Sonra çalışanlar', { count: d.successors.length, actions: [plus('before')] }),
    namedList(slug, d.successors, 'surecler', 'Bu süreci bekleyen job yok.'),
    edit.sectionHead('Zamanlama ayrıntısı'),
    scheduleView(d.schedule_detail),
    edit.sectionHead('Etki', { actions: [button('Etki analizi', edit.act(() => edit.showImpact(slug, d.id, d.name)), { kind: 'small ghost' })] }),
    edit.note(`Bu süreç çalışmazsa ${impact.datasets} tablo/dosya ve ${impact.processes} süreç etkilenir.`),
    impact.consumption.length
      ? [h('div', { class: 'small muted', text: 'Etkilenen tüketim nesneleri (rapor, dış alıcı):' }), namedList(slug, impact.consumption, 'tablolar', '')]
      : null,
    edit.sectionHead('Kaynaklar', { count: d.sources.length }),
    edit.sourcesList(slug, d.sources),
    edit.sectionHead('Bulgular', { count: d.findings.length, actions: [h('a', { class: 'btn small ghost', href: link('p', slug, 'bulgular') }, 'Tüm bulgular')] }),
    edit.findingsList(d.findings),
    edit.sectionHead('Bu sürece yapılan düzeltmeler', { count: d.corrections.length }),
    edit.correctionsList(slug, d.corrections),
    edit.sectionHead('Ham bağlar', { count: d.edges.length }),
    edit.edgesList(slug, d.edges, { known }));
}

// ---- bağ ekle ------------------------------------------------------------- //

// Bağ biçimleri: diğer ucun türü, yön ve sunucudaki bağ türü.
const LINK_MODES = {
  reads: { other: 'dataset', edge: 'reads', incoming: true, text: 'Okur: tablo → bu süreç' },
  writes: { other: 'dataset', edge: 'writes', incoming: false, text: 'Yazar: bu süreç → tablo' },
  before: { other: 'process', edge: 'precedes', incoming: false, text: 'Önce çalışır: bu süreç → diğer süreç' },
  after: { other: 'process', edge: 'precedes', incoming: true, text: 'Sonra çalışır: diğer süreç → bu süreç' },
};

async function addLink(ctx, d, preset, known) {
  const { slug } = ctx;
  const [datasets, processes] = await Promise.all([ctx.datasets(), ctx.processes()]);
  const names = {
    dataset: datasets.map((x) => x.name),
    process: processes.filter((x) => x.id !== d.id).map((x) => x.name),
  };
  const folded = { dataset: new Set(names.dataset.map(fold)), process: new Set(processes.map((x) => fold(x.name))) };
  const values = await edit.formModal({
    title: `Bağ ekle: ${d.name}`, submitLabel: 'Ekle',
    intro: 'Okuma/yazma bağı bir tabloyla, "önce/sonra çalışır" bağı başka bir süreçle kurulur. Listede olmayan nesneyi önce ekleyin.',
    fields: [
      { name: 'bag', label: 'Bağ', type: 'select', value: preset, wide: true,
        options: Object.entries(LINK_MODES).map(([value, mode]) => ({ value, label: mode.text })) },
      { name: 'diger', label: 'Diğer uç', required: true },
      edit.reasonField(),
    ],
    // Diğer uç önerileri seçilen bağ biçimine göre tablo ya da süreç adlarıdır.
    onBuild: (grid) => {
      const select = grid.querySelector('[name="bag"]');
      const input = grid.querySelector('[name="diger"]');
      const list = h('datalist', { id: edit.datalistId() });
      input.setAttribute('list', list.id);
      input.after(list);
      const fill = () => {
        clear(list);
        for (const name of names[LINK_MODES[select.value].other]) list.append(h('option', { value: name }));
      };
      select.addEventListener('change', fill);
      fill();
    },
    validate: (v) => {
      const mode = LINK_MODES[v.bag];
      const key = fold(v.diger.trim());
      if (key === fold(d.name)) return 'Süreç kendisine bağlanamaz';
      const wrong = mode.other === 'dataset' ? 'process' : 'dataset';
      if (!folded[mode.other].has(key) && folded[wrong].has(key)) {
        return mode.other === 'dataset' ? 'Okuma/yazma bağının diğer ucu bir tablo/dosya olmalı' : 'Sıra bağının diğer ucu bir süreç olmalı';
      }
      return null;
    },
    onSubmit: async (v) => {
      const mode = LINK_MODES[v.bag];
      const [self, other] = await Promise.all([edit.nodeRef(slug, d.name, known), edit.nodeRef(slug, v.diger, known)]);
      const [kaynak, hedef] = mode.incoming ? [other, self] : [self, other];
      await edit.sendOps(slug, [edit.op('bag_ekle', { kaynak, hedef, tur: mode.edge }, null, v.neden)]);
      return v;
    },
  });
  if (values) edit.changed('Bağ eklendi');
}
