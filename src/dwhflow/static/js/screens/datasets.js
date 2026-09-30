// Tablolar ve dosyalar: süzülebilir liste (sol) + seçili nesnenin ayrıntısı, analizleri ve düzeltmeleri (sağ).

import { ApiError, get, p } from '../core/api.js';
import { debounce, fold, h } from '../core/dom.js';
import { go, link } from '../core/router.js';
import { canEdit, label, options } from '../core/store.js';
import { badge, button, dataTable, empty, layerPill, pageHeader, statusBadge, toastError } from '../core/ui.js';
import * as edit from './_model_edit.js';

export const refreshOnChange = true;

// Süzgeçler proje başına hatırlanır (satıra tıklama ve düzeltme sonrası yeniden çizimde kaybolmasın).
const memory = { slug: null, filters: null };
const blankFilters = () => ({ q: '', layer: '', kind: '', status: '', system: '' });
const STATUS_OPTIONS = [{ value: 'draft', label: 'taslak' }, { value: 'confirmed', label: 'onaylı' }];
const DIRECT_DATASET_KINDS = new Set(['derives_from', 'transfers']);

function listUrl(slug, filters) {
  const params = new URLSearchParams({ limit: '2000' });
  for (const key of ['q', 'layer', 'kind', 'status']) if (filters[key]) params.set(key, filters[key]);
  return `${p`/api/projects/${slug}/datasets`}?${params}`;
}

async function fetchDetail(slug, id) {
  try {
    return await get(p`/api/projects/${slug}/datasets/${id}`);
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
    title: 'Tablolar ve dosyalar',
    subtitle: 'Dokümanlardan çıkarılan veri nesneleri; seçince bağları, kolonları, kaynakları ve düzeltmeleri görünür.',
    crumbs: [{ label: 'Pano', href: link('p', slug) }, { label: 'Tablolar ve dosyalar' }],
    actions: [
      button('Yol bul', edit.act(() => edit.findPaths(slug, ctx.datasets, current ? current.name : ''))),
      current ? button('Etki analizi', edit.act(() => edit.showImpact(slug, current.id, current.name))) : null,
      canEdit() ? button('+ Yeni nesne', edit.act(() => edit.createNode(slug, 'dataset', 'tablolar')), { kind: 'primary' }) : null,
    ],
  }));
  root.append(h('div', { class: 'split' }, listPane(slug, list, filters, id), detailPane(ctx, detail)));
  edit.restoreScroll();
}

// ---- liste ---------------------------------------------------------------- //

function listPane(slug, first, filters, activeId) {
  const noFilter = () => !Object.values(filters).some(Boolean);
  if (!first.total && noFilter()) {
    return h('div', { class: 'card' }, empty('Bu projede henüz tablo ya da dosya yok. Doküman yükleyip okutun.',
      h('a', { class: 'btn primary', href: link('p', slug, 'dokumanlar') }, 'Dokümanlar')));
  }
  const order = Object.fromEntries(options('layers').map((o, i) => [o.value, i]));
  let last = first;
  const visible = () => last.items.filter((r) => !filters.system || fold(r.system || '').includes(fold(filters.system)));
  const table = dataTable({
    rows: visible(), filter: false, rowKey: (r) => r.id, activeKey: activeId || null,
    empty: 'Süzgeçlerle eşleşen tablo/dosya yok',
    onRowClick: (r) => { edit.keepScroll(); go('p', slug, 'tablolar', r.id); },
    columns: [
      { key: 'name', label: 'Ad', render: (r) => h('span', {},
        h('a', { href: link('p', slug, 'tablolar', r.id), text: r.name, onclick: () => edit.keepScroll() }),
        r.manual ? [' ', badge('elle', 'info')] : null) },
      { key: 'layer', label: 'Katman', render: (r) => layerPill(r.layer), sort: (r) => order[r.layer] ?? 99 },
      { key: 'kind', label: 'Tür', render: (r) => label.datasetKind(r.kind), sort: (r) => label.datasetKind(r.kind) },
      { key: 'system', label: 'Sistem', render: (r) => r.system || '—' },
      { key: 'status', label: 'Durum', render: (r) => statusBadge(r.status), sort: (r) => label.status(r.status) },
      { key: 'links', label: '↑ / ↓', num: true, sort: (r) => r.upstream + r.downstream,
        render: (r) => h('span', { class: 'nowrap', title: `${r.upstream} yukarı akış, ${r.downstream} aşağı akış bağı`, text: `↑${r.upstream} ↓${r.downstream}` }) },
      { key: 'sources', label: 'Kaynak', num: true },
      { key: 'findings', label: 'Bulgu', num: true, render: (r) => (r.findings ? badge(String(r.findings), 'warn') : '0') },
    ],
  });

  const info = h('span', { class: 'small muted' });
  const showInfo = () => {
    const shown = visible().length;
    info.textContent = last.total > last.items.length
      ? `İlk ${last.items.length} / ${last.total} nesne alındı; süzgeçle daraltın.`
      : `${shown} / ${last.total} nesne`;
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
  const system = h('input', { type: 'search', placeholder: 'Sistem…', 'aria-label': 'Sisteme göre süz', value: filters.system, style: { maxWidth: '160px' } });
  system.addEventListener('input', debounce(() => { filters.system = system.value.trim(); refilter(); }, 200));
  const select = (key, allLabel, items) => {
    const node = h('select', { 'aria-label': allLabel }, h('option', { value: '' }, allLabel), items.map((o) => h('option', { value: o.value }, o.label)));
    node.value = filters[key];
    node.addEventListener('change', () => { filters[key] = node.value; reload(); });
    return node;
  };
  const layer = select('layer', 'Tüm katmanlar', options('layers'));
  const kind = select('kind', 'Tüm türler', options('dataset_kinds'));
  const status = select('status', 'Tüm durumlar', STATUS_OPTIONS);
  const reset = button('Temizle', () => {
    Object.assign(filters, blankFilters());
    text.value = ''; system.value = ''; layer.value = ''; kind.value = ''; status.value = '';
    reload();
  }, { kind: 'ghost small', title: 'Süzgeçleri temizle' });
  showInfo();
  return h('div', {}, h('div', { class: 'toolbar' }, text, layer, kind, status, system, reset, h('span', { class: 'grow' }), info), table.el);
}

// ---- ayrıntı -------------------------------------------------------------- //

function neighbourList(slug, items) {
  if (!items.length) return edit.note('Bağ yok.');
  return h('ul', { class: 'list' }, items.map((n) => h('li', { style: { flexWrap: 'wrap' } },
    h('a', { href: link('p', slug, 'tablolar', n.id), text: n.name }),
    h('span', { class: 'small muted grow' }, n.processes.length
      ? ['süreç: ', n.processes.map((pr, i) => [i ? ', ' : '', h('a', { href: link('p', slug, 'surecler', pr.id), text: pr.name })])]
      : 'doğrudan'),
    n.draft ? badge('taslak', 'warn') : null)));
}

function columnsTable(columns) {
  if (!columns.length) return edit.note('Kolon bilgisi yok.');
  return h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
    h('thead', {}, h('tr', {}, h('th', { text: 'Kolon' }), h('th', { text: 'Veri tipi' }), h('th', { text: 'PK' }))),
    h('tbody', {}, columns.map((c) => h('tr', {},
      h('td', { class: 'mono', text: c.name }), h('td', { text: c.data_type || '—' }), h('td', { text: c.pk ? 'evet' : '' }))))));
}

function mappingList(title, rows) {
  if (!rows.length) return null;
  return h('div', { style: { marginBottom: '6px' } }, h('div', { class: 'small muted', text: title }),
    h('ul', { class: 'list' }, rows.map((m) => h('li', { style: { flexWrap: 'wrap' } },
      h('span', { class: 'grow mono', style: { overflowWrap: 'anywhere' }, text: `${m.source} → ${m.target}` }),
      m.expression ? h('span', { class: 'small muted', text: m.expression }) : null))));
}

function detailPane(ctx, d) {
  const { slug } = ctx;
  if (!d) return h('div', { class: 'card detail' }, empty('Ayrıntı ve düzenleme için listeden bir tablo ya da dosya seçin.'));
  if (d.missing) {
    return h('div', { class: 'card detail' }, empty('Bu nesne modelde yok: silinmiş, birleştirilmiş ya da yeniden okumada adı değişmiş olabilir.',
      h('a', { class: 'btn', href: link('p', slug, 'tablolar') }, 'Listeye dön')));
  }
  const known = edit.knownRefs([d, ...d.upstream, ...d.downstream,
    ...d.upstream.flatMap((n) => n.processes), ...d.downstream.flatMap((n) => n.processes)]);
  const isDirect = (source, target) => d.edges.some((e) => DIRECT_DATASET_KINDS.has(e.kind) && e.source === source && e.target === target);
  const direct = [
    ...d.upstream.filter((n) => isDirect(n.name, d.name)).map((n) => ({ source: n.name, target: d.name })),
    ...d.downstream.filter((n) => isDirect(d.name, n.name)).map((n) => ({ source: d.name, target: n.name })),
  ];
  const small = { kind: 'small' };
  const tools = canEdit() ? h('div', { class: 'row', style: { margin: '10px 0 4px' } },
    button('Yeniden adlandır', edit.act(() => edit.renameNode(slug, d)), small),
    button('Katman', edit.act(() => edit.setLayer(slug, d)), small),
    button('Tür', edit.act(() => edit.setKind(slug, d, 'dataset_kinds')), small),
    button('Sistem', edit.act(() => edit.setSystem(slug, d, ctx.datasets)), small),
    button('Hassasiyet', edit.act(() => edit.setSensitivity(slug, d)), small),
    button('Açıklama', edit.act(() => edit.setDescription(slug, d)), small),
    d.status === 'draft' ? button('Onayla', edit.act(() => edit.confirmNode(slug, d)), { kind: 'small ok' }) : null,
    button('Birleştir', edit.act(() => edit.mergeNodes(slug, d, { candidates: ctx.datasets, screen: 'tablolar', noun: 'tablo/dosya' })), small),
    button('Bağ ekle', edit.act(() => addLink(ctx, d, 'up', known)), small),
    direct.length ? button('Süreç ata', edit.act(() => assignProcess(ctx, d, direct)), small) : null,
    button('Sil', edit.act(() => edit.deleteNode(slug, d, 'tablolar')), { kind: 'small danger' })) : null;
  const plus = (text, fn) => (canEdit() ? button(text, edit.act(fn), { kind: 'small ghost' }) : null);

  return h('div', { class: 'card detail' },
    h('h2', { style: { overflowWrap: 'anywhere' }, text: d.name }),
    h('div', { class: 'row' }, statusBadge(d.status), layerPill(d.layer), badge(label.datasetKind(d.kind), 'muted'),
      d.manual ? badge('elle eklendi/düzeltildi', 'info') : null),
    tools,
    h('dl', { class: 'kv', style: { marginTop: '12px' } },
      h('dt', { text: 'Kimlik' }), h('dd', { class: 'mono', text: d.id }),
      h('dt', { text: 'Sistem' }), h('dd', { text: d.system || '—' }),
      h('dt', { text: 'Hassasiyet' }), h('dd', { text: label.sensitivity(d.sensitivity) }),
      h('dt', { text: 'Açıklama' }), h('dd', { text: d.description || '—' }),
      d.aliases.length ? [h('dt', { text: 'Diğer adlar' }), h('dd', { text: d.aliases.join(', ') })] : null,
      h('dt', { text: 'Dokümanlar' }), h('dd', {}, d.files.length ? d.files.map((f, i) => [i ? ', ' : '', edit.docLink(slug, f)]) : '—')),
    edit.sectionHead('Yukarı akış', { count: d.upstream.length, actions: [plus('+ Bağ', () => addLink(ctx, d, 'up', known))] }),
    neighbourList(slug, d.upstream),
    edit.sectionHead('Aşağı akış', { count: d.downstream.length, actions: [plus('+ Bağ', () => addLink(ctx, d, 'down', known))] }),
    neighbourList(slug, d.downstream),
    edit.sectionHead('Kolonlar', { count: d.columns.length, actions: [plus('+ Kolon ekle', () => addColumn(slug, d))] }),
    columnsTable(d.columns),
    edit.sectionHead('Kolon eşlemeleri', { count: d.column_in.length + d.column_out.length,
      actions: [h('a', { class: 'btn small ghost', href: link('p', slug, 'sttm') }, 'STTM')] }),
    d.column_in.length + d.column_out.length
      ? [mappingList('Gelen (bu nesneye yazılan kolonlar)', d.column_in), mappingList('Giden (bu nesneden okunan kolonlar)', d.column_out)]
      : edit.note('Kolon eşlemesi yok.'),
    edit.sectionHead('Kaynaklar', { count: d.sources.length }),
    edit.sourcesList(slug, d.sources),
    edit.sectionHead('Bulgular', { count: d.findings.length, actions: [h('a', { class: 'btn small ghost', href: link('p', slug, 'bulgular') }, 'Tüm bulgular')] }),
    edit.findingsList(d.findings),
    edit.sectionHead('Bu nesneye yapılan düzeltmeler', { count: d.corrections.length }),
    edit.correctionsList(slug, d.corrections),
    edit.sectionHead('Ham bağlar', { count: d.edges.length }),
    edit.edgesList(slug, d.edges, { known }));
}

// ---- düzenlemeler --------------------------------------------------------- //

async function addLink(ctx, d, direction, known) {
  const { slug } = ctx;
  const [datasets, processes] = await Promise.all([ctx.datasets(), ctx.processes()]);
  const values = await edit.formModal({
    title: `Bağ ekle: ${d.name}`, submitLabel: 'Ekle',
    intro: 'Süreç verilirse bağ süreç üzerinden kurulur (tablo → süreç okur, süreç → tablo yazar); süreç modelde yoksa eklenir. '
      + 'Listede olmayan tablo için önce "Yeni nesne" ile ekleyin.',
    fields: [
      { name: 'yon', label: 'Yön', type: 'select', value: direction, wide: true, options: [
        { value: 'up', label: `Yukarı akış: diğer → ${d.name}` }, { value: 'down', label: `Aşağı akış: ${d.name} → diğer` }] },
      { name: 'diger', label: 'Diğer uç (tablo/dosya)', required: true, suggestions: datasets.filter((x) => x.id !== d.id).map((x) => x.name) },
      { name: 'surec', label: 'Süreç (isteğe bağlı)', suggestions: processes.map((x) => x.name) },
      { name: 'tur', label: 'Süreçsiz bağın türü', type: 'select', value: 'derives_from',
        options: ['derives_from', 'transfers'].map((v) => ({ value: v, label: label.edgeKind(v) })), hint: 'Süreç verilirse kullanılmaz.' },
      edit.reasonField(),
    ],
    validate: (v) => (fold(v.diger.trim()) === fold(d.name) ? 'Nesne kendisine bağlanamaz' : null),
    onSubmit: async (v) => {
      // '#' içeren adlar sunucuda TABLO#KOLON sanılmasın diye kimliğe çevrilir.
      const [self, other] = await Promise.all([edit.nodeRef(slug, d.name, known), edit.nodeRef(slug, v.diger, known)]);
      const [kaynak, hedef] = v.yon === 'up' ? [other, self] : [self, other];
      const surec = v.surec.trim();
      const target = surec ? { kaynak, hedef } : { kaynak, hedef, tur: v.tur };
      await edit.sendOps(slug, [edit.op('bag_ekle', target, surec ? { surec } : null, v.neden)]);
      return v;
    },
  });
  if (values) edit.changed('Bağ eklendi');
}

async function assignProcess(ctx, d, direct) {
  const processes = await ctx.processes();
  await edit.editOps(ctx.slug, {
    title: `Süreç ata: ${d.name}`, submitLabel: 'Ata',
    intro: 'Seçilen doğrudan (süreçsiz) bağ "tablo → süreç → tablo" olarak süreç üzerinden kurulur; süreç modelde yoksa eklenir.',
    fields: [
      { name: 'bag', label: 'Doğrudan bağ', type: 'select', wide: true,
        options: direct.map((x, i) => ({ value: String(i), label: `${x.source} → ${x.target}` })) },
      { name: 'surec', label: 'Süreç', required: true, suggestions: processes.map((x) => x.name) },
    ],
    build: (v) => {
      const chosen = direct[Number(v.bag)];
      return [edit.op('surec_ata', { kaynak: chosen.source, hedef: chosen.target }, { surec: v.surec.trim() }, v.neden)];
    },
    message: 'Süreç atandı',
  });
}

function addColumn(slug, d) {
  return edit.editOps(slug, {
    title: `Kolon ekle: ${d.name}`, submitLabel: 'Ekle',
    intro: 'Kolon adı büyük harfe çevrilir. Var olan bir kolon adı verilirse veri tipi ve PK bilgisi güncellenir.',
    fields: [
      { name: 'ad', label: 'Kolon adı', required: true },
      { name: 'tip', label: 'Veri tipi', placeholder: 'ör. DECIMAL(18,2)' },
      { name: 'pk', label: 'Birincil anahtar (PK)', type: 'checkbox' },
    ],
    validate: (v) => (v.ad.includes('#') ? "Kolon adı '#' içeremez" : null),
    build: (v) => [edit.op('kolon_ekle', { nesne: d.name }, { ad: v.ad.trim(), tip: v.tip.trim(), pk: v.pk }, v.neden)],
    message: 'Kolon kaydedildi',
  });
}
