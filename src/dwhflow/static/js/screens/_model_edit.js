// Model düzenleme ortak parçaları: tablolar, süreçler, STTM, onay kuyruğu ve bulgular ekranları kullanır.
// Düzeltmeler POST /corrections {ops:[{islem, hedef, deger, neden}]} ile gider; hedefler ADLA verilir.
// Yıkıcı işlemler (sil, birleştir, bağ sil) önce /corrections/preview farkını gösterir, onayla uygulanır.

import { del, get, p, post } from '../core/api.js';
import { append, clear, fold, h } from '../core/dom.js';
import { go, link } from '../core/router.js';
import { canEdit, emit, label, options } from '../core/store.js';
import {
  badge, button, confirmDialog, empty, field, layerPill, modal, severityBadge, stat, statusBadge, toast, toastError, when,
} from '../core/ui.js';

/** Yönü çevrilebilen doğrudan bağ türleri (sunucudaki DIRECT_KINDS). */
export const DIRECT_KINDS = new Set(['derives_from', 'transfers', 'precedes']);
const REASON_MAX = 500;

// ---- küçük yardımcılar --------------------------------------------------- //

/** Düğme işleyicisi: hata bildirilir, yakalanmamış söz (promise) kalmaz. */
export const act = (fn) => () => { Promise.resolve().then(fn).catch(toastError); };

/** Tek seferlik yükleyici: ilk çağrıda yükler, sonra aynı sonucu verir (hata olursa yeniden dener). */
export function lazy(loader) {
  let promise = null;
  return () => {
    if (!promise) promise = loader().catch((error) => { promise = null; throw error; });
    return promise;
  };
}

/** Projedeki tüm veri nesneleri (kind='datasets') ya da süreçler (kind='processes'). */
export async function loadAll(slug, kind) {
  const data = await get(`${p`/api/projects/${slug}/${kind}`}?limit=2000`);
  return data.items;
}

let pendingScroll = null;

/** Ekran yeniden çizilince (gezinme, düzeltme) sayfa kaydırma konumu korunsun. */
export function keepScroll() {
  pendingScroll = { y: window.scrollY, at: Date.now() };
}

export function restoreScroll() {
  const saved = pendingScroll;
  pendingScroll = null;
  if (saved && Date.now() - saved.at < 10000) setTimeout(() => window.scrollTo(0, saved.y), 0);
}

let listSeq = 0;
export const datalistId = () => `dwf-oneri-${++listSeq}`;

/** Pencere içindeki bağlantıya tıklanınca o pencereyi kapatır (gezinme sürer). */
export function closeOwnModal(event) {
  event.currentTarget.closest('.modal')?.querySelector('.modal-head .icon-btn')?.click();
}

export function sectionHead(title, { count, actions = [] } = {}) {
  return h('div', { class: 'row', style: { marginTop: '18px', marginBottom: '6px' } },
    h('h3', { class: 'grow', style: { margin: '0' }, text: count == null ? title : `${title} (${count})` }), actions);
}

export const note = (text) => h('p', { class: 'small muted', style: { margin: '2px 0' }, text });

export function docLink(slug, file) {
  return h('a', { href: link('p', slug, 'dokuman', file), text: file });
}

export function percent(value) {
  return value == null ? null : `güven %${Math.round(Number(value) * 100)}`;
}

// ---- düzeltme gönderme ---------------------------------------------------- //

/** Düzeltme işlemi; boş değer ve boş neden gönderilmez. */
export function op(islem, hedef, deger, neden) {
  const out = { islem, hedef };
  if (deger && Object.keys(deger).length) out.deger = deger;
  const text = String(neden ?? '').trim();
  if (text) out.neden = text;
  return out;
}

export function reasonField(value = '') {
  return { name: 'neden', label: 'Neden (isteğe bağlı)', type: 'textarea', wide: true, value,
    placeholder: 'Değişiklik günlüğünde görünür; ör. "doküman eski", "ekip kararı"' };
}

export const sendOps = (slug, ops) => post(p`/api/projects/${slug}/corrections`, { ops });

/** Başarılı değişiklikten sonra: bildirim + 'project:changed' (kabuk menü sayaçlarını ve ekranı yeniler). */
export function changed(message) {
  if (message) toast(message, 'ok');
  keepScroll();
  emit('project:changed');
}

/** Ad → kimlik sözlüğü (bağ işlemlerinde '#' içeren adlar için). */
export function knownRefs(nodes) {
  const map = new Map();
  for (const node of nodes) if (node?.name && node.id) map.set(node.name, node.id);
  return map;
}

/**
 * Bağ işlemlerinde (bag_ekle/bag_sil/bag_yon_cevir/onayla) uç adı. Sunucu "AD#KOLON" biçimini son
 * '#'ten böler; adı '#' içeren nesne (ör. "yukleme #1") kolon sanılmasın diye kimliğiyle gönderilir
 * (sunucunun ad çözümleyicisi kimliği de tanır).
 */
export async function nodeRef(slug, name, known) {
  const text = String(name ?? '').trim();
  if (!text.includes('#')) return text;
  if (known?.has(text)) return known.get(text);
  for (const kind of ['datasets', 'processes']) {
    const data = await get(`${p`/api/projects/${slug}/${kind}`}?${new URLSearchParams({ q: text, limit: '50' })}`);
    const match = data.items.find((x) => x.name === text) || data.items.find((x) => fold(x.name) === fold(text));
    if (match) return match.id;
  }
  return text;
}

/**
 * Modelde bulunamayan veri nesnesi adları. Sunucunun kendi ad çözümlemesi kullanılır (şemasız ad,
 * eski ad, birleşen ad da eşleşir): nesne_ekle önizlemesi yeni nesne ekliyorsa ad modelde yoktur.
 */
export async function missingDatasets(slug, names) {
  const unique = [...new Set(names.map((n) => String(n ?? '').trim()).filter(Boolean))];
  const checks = await Promise.all(unique.map(async (name) => {
    const diff = await post(p`/api/projects/${slug}/corrections/preview`, { ops: [op('nesne_ekle', { ad: name, tip: 'dataset' })] });
    const ok = (diff.results || []).every((r) => r.durum === 'uygulandi');
    return ok && (diff.nodes_added || []).length ? name : null;
  }));
  return checks.filter(Boolean);
}

// ---- alanlı düzenleme penceresi ------------------------------------------ //

function buildField(spec) {
  if (spec.type === 'custom') return spec.el;
  const node = field(spec);
  if (spec.suggestions?.length) {
    const id = datalistId();
    node.querySelector('input')?.setAttribute('list', id);
    node.append(h('datalist', { id }, [...new Set(spec.suggestions)].map((value) => h('option', { value }))));
  }
  return node;
}

/**
 * formModal({title, intro, fields, submitLabel, danger, wide, validate, onSubmit, onBuild}) → Promise(sonuç | null)
 * fields: ui.field() alanları + {suggestions: [..]} (açılır öneri) ya da {type:'custom', name, el, read()}.
 * onSubmit(values) pencere açıkken çalışır: hata fırlatırsa bildirilir ve pencere açık kalır; false dönerse de açık kalır.
 */
export function formModal({ title, intro, fields, submitLabel = 'Kaydet', danger = false, wide = false, validate, onSubmit, onBuild }) {
  const specs = fields.filter(Boolean);
  const grid = h('div', { class: 'form-grid' }, specs.map(buildField));
  const read = () => {
    const out = {};
    for (const spec of specs) {
      if (spec.type === 'custom') { out[spec.name] = spec.read(); continue; }
      const input = grid.querySelector(`[name="${CSS.escape(spec.name)}"]`);
      if (!input) continue;
      if (spec.type === 'checkbox') out[spec.name] = input.checked;
      else if (spec.type === 'number') out[spec.name] = input.value === '' ? null : Number(input.value);
      else out[spec.name] = input.value;
    }
    return out;
  };
  // Enter ile gönder (öneri listesi olan alanlarda Enter öneri seçer, gönderme yapmaz).
  grid.addEventListener('keydown', (event) => {
    const target = event.target;
    if (event.key !== 'Enter' || target.tagName !== 'INPUT' || target.type === 'checkbox' || target.hasAttribute('list')) return;
    event.preventDefault();
    grid.closest('.modal')?.querySelector('.modal-foot button:last-child')?.click();
  });
  if (onBuild) onBuild(grid);
  const head = typeof intro === 'string' ? h('p', { class: 'muted', style: { margin: '0' }, text: intro }) : intro;
  let values = null;
  let result;
  let busy = false;
  return modal({
    title, wide, body: h('div', { class: 'stack' }, head || null, grid),
    actions: [
      { label: 'Vazgeç', value: null },
      {
        label: submitLabel, kind: danger ? 'danger solid' : 'primary', value: 'ok',
        onClick: async () => {
          if (busy) return false;
          const current = read();
          const missing = specs.filter((s) => s.required && !String(current[s.name] ?? '').trim());
          if (missing.length) { toast(`Zorunlu alan: ${missing.map((s) => s.label).join(', ')}`, 'warn'); return false; }
          if (String(current.neden ?? '').trim().length > REASON_MAX) { toast(`Neden en çok ${REASON_MAX} karakter olabilir`, 'warn'); return false; }
          const problem = validate ? validate(current) : null;
          if (problem) { toast(problem, 'warn'); return false; }
          busy = true;
          try {
            result = onSubmit ? await onSubmit(current) : current;
          } finally { busy = false; }
          if (result === false) return false;
          values = current;
          return true;
        },
      },
    ],
  }).then((choice) => (choice === 'ok' ? (result === undefined ? values : result) : null));
}

/** Alanları sorar, işlemleri gönderir; başarıda bildirir ve ekranı yeniler. */
export async function editOps(slug, { title, intro, fields = [], submitLabel, danger, validate, build, message, onBuild }) {
  const values = await formModal({
    title, intro, submitLabel, danger, validate, onBuild, fields: [...fields, reasonField()],
    onSubmit: async (v) => { await sendOps(slug, build(v)); return v; },
  });
  if (values) changed(message);
  return values;
}

// ---- önizleme (fark) ---------------------------------------------------- //

const FIELD_LABELS = { name: 'ad', layer: 'katman', kind: 'tür', system: 'sistem', status: 'durum', description: 'açıklama', columns: 'kolonlar' };
const RESULT_LABELS = { uygulandi: 'uygulandı', sahipsiz: 'hedef bulunamadı', hata: 'hata' };
const TYPE_LABELS = { dataset: 'tablo/dosya', process: 'süreç' };

function kindLabel(value) {
  const asDataset = label.datasetKind(value);
  return asDataset !== value ? asDataset : label.processKind(value);
}

function fieldValue(name, value) {
  if (value == null || value === '') return '—';
  if (name === 'layer') return label.layer(value);
  if (name === 'kind') return kindLabel(value);
  if (name === 'status') return label.status(value);
  if (Array.isArray(value)) return value.length ? value.join(', ') : '—';
  return String(value);
}

function linkText(row) {
  const via = row.processes?.length ? ` (süreç: ${row.processes.join(', ')})` : '';
  return `${row.source_name} → ${row.target_name}${via}`;
}

/** /corrections/preview yanıtını gösterir. */
export function diffView(diff) {
  const out = h('div', { class: 'stack' });
  const problems = (diff.results || []).filter((r) => r.durum !== 'uygulandi');
  if (problems.length) {
    out.append(h('div', { class: 'banner bad', style: { display: 'block' } },
      h('b', { text: 'Bu işlemler uygulanamaz:' }),
      h('ul', { style: { margin: '6px 0 0', paddingLeft: '18px' } },
        problems.map((r) => h('li', { text: `${label.op(r.islem)}: ${r.mesaj || RESULT_LABELS[r.durum] || r.durum}` })))));
  }
  if (diff.empty) {
    out.append(note('Modelde görünür bir değişiklik olmayacak.'));
    return out;
  }
  const group = (title, rows, render) => (rows?.length
    ? h('div', {}, h('h3', { style: { marginTop: '4px' }, text: `${title} (${rows.length})` }), h('ul', { class: 'list' }, rows.map(render)))
    : null);
  const nodeRow = (tag, kind) => (n) => h('li', {}, badge(tag, kind), h('b', { text: n.name }), layerPill(n.layer),
    h('span', { class: 'small muted', text: `${TYPE_LABELS[n.type] || n.type} · ${kindLabel(n.kind)}` }));
  append(out, [
    group('Silinecek nesneler', diff.nodes_removed, nodeRow('silinir', 'bad')),
    group('Eklenecek nesneler', diff.nodes_added, nodeRow('eklenir', 'ok')),
    group('Değişecek alanlar', diff.nodes_changed, (c) => h('li', { style: { flexWrap: 'wrap' } },
      h('b', { text: c.name }), badge(FIELD_LABELS[c.field] || c.field, 'muted'),
      c.field === 'columns'
        ? h('span', { text: `çıkan: ${fieldValue('', c.old)} · eklenen: ${fieldValue('', c.new)}` })
        : h('span', { text: `${fieldValue(c.field, c.old)} → ${fieldValue(c.field, c.new)}` }))),
    group('Kalkacak bağlar', diff.links_removed, (l) => h('li', {}, badge('kalkar', 'bad'),
      h('span', { class: 'grow', text: linkText(l) }), l.draft ? badge('taslak', 'warn') : null)),
    group('Eklenecek bağlar', diff.links_added, (l) => h('li', {}, badge('eklenir', 'ok'),
      h('span', { class: 'grow', text: linkText(l) }), l.draft ? badge('taslak', 'warn') : null)),
    group('Değişecek bağlar', diff.links_changed, (l) => h('li', { style: { display: 'block' } },
      h('div', { text: linkText(l) }),
      l.was_draft !== l.draft ? h('div', { class: 'small muted', text: `durum: ${l.was_draft ? 'taslak' : 'onaylı'} → ${l.draft ? 'taslak' : 'onaylı'}` }) : null,
      (l.old_processes || []).join('|') !== (l.processes || []).join('|')
        ? h('div', { class: 'small muted', text: `süreç: ${(l.old_processes || []).join(', ') || 'doğrudan'} → ${(l.processes || []).join(', ') || 'doğrudan'}` })
        : null)),
  ]);
  if (diff.edges_added || diff.edges_removed) {
    out.append(note(`Ham bağ: ${diff.edges_added} eklenir, ${diff.edges_removed} kalkar.`));
  }
  return out;
}

/**
 * İşlemleri önizler; farkı ve isteğe bağlı neden alanını gösterir, "Uygula" ile kaydeder.
 * Dönüş: {diff, response} ya da vazgeçilirse null.
 */
export async function previewThenApply(slug, ops, { title = 'Önizleme', intro, applyLabel = 'Uygula', danger = true } = {}) {
  let diff;
  try {
    diff = await post(p`/api/projects/${slug}/corrections/preview`, { ops });
  } catch (error) { toastError(error); return null; }
  const blocked = (diff.results || []).some((r) => r.durum !== 'uygulandi');
  const reason = blocked ? null : field(reasonField());
  let response = null;
  let busy = false;
  const actions = blocked
    ? [{ label: 'Kapat', value: null }]
    : [{ label: 'Vazgeç', value: null }, {
      label: applyLabel, kind: danger ? 'danger solid' : 'primary', value: 'ok',
      onClick: async () => {
        if (busy) return false;
        const text = reason.querySelector('textarea').value.trim();
        if (text.length > REASON_MAX) { toast(`Neden en çok ${REASON_MAX} karakter olabilir`, 'warn'); return false; }
        busy = true;
        try {
          response = await sendOps(slug, ops.map((o) => (text ? { ...o, neden: text } : o)));
        } finally { busy = false; }
        return true;
      },
    }];
  const body = h('div', { class: 'stack' }, intro ? h('p', { style: { margin: '0' }, text: intro }) : null, diffView(diff), reason);
  const choice = await modal({ title, wide: true, body, actions });
  return choice === 'ok' ? { diff, response } : null;
}

// ---- ayrıntı bölümleri ---------------------------------------------------- //

export function sourceBlock(slug, source) {
  const manual = String(source.extractor || '').startsWith('manual:');
  const where = source.file
    ? docLink(slug, source.file)
    : h('span', { text: manual ? `elle düzeltme (${source.extractor.slice(7)})` : 'kaynak yok' });
  const meta = [source.locator, !manual && source.extractor ? `(${source.extractor})` : null, percent(source.confidence)]
    .filter(Boolean).join(' · ');
  return h('div', { class: 'evidence' },
    h('span', { class: 'src' }, where, meta ? ` · ${meta}` : ''),
    source.evidence ? h('span', { text: source.evidence }) : null);
}

export function sourcesList(slug, sources) {
  if (!sources?.length) return note('Kaynak yok.');
  return h('div', {}, sources.map((s) => sourceBlock(slug, s)));
}

export function findingsList(findings) {
  if (!findings?.length) return note('Bulgu yok.');
  return h('ul', { class: 'list' }, findings.map((f) => h('li', {},
    severityBadge(f.severity),
    h('span', { class: 'grow' }, f.message, f.subject_type ? null : h('div', { class: 'small muted', text: f.subject_name })),
    h('code', { class: 'small muted', text: f.rule }))));
}

const CORRECTION_STATE = { uygulandi: ['uygulandı', 'ok'], sahipsiz: ['sahipsiz', 'warn'], hata: ['hata', 'bad'] };

/** Düzeltmenin hedef ve değerlerini tek satırda anlatır. */
export function describeOp(c) {
  const t = c.hedef || {};
  let target = '';
  if (Array.isArray(t.nesneler)) target = t.nesneler.join(' + ');
  else if (t.nesne) target = t.nesne;
  else if (t.kaynak || t.hedef) target = `${t.kaynak ?? '?'} → ${t.hedef ?? '?'}`;
  else if (t.ad) target = `${t.ad}${t.tip ? ` (${TYPE_LABELS[t.tip] || t.tip})` : ''}`;
  else if (t.kural) target = `${t.kural} · ${t.ozne || 'model'}`;
  else if (t.anahtar) target = t.anahtar;
  if (t.tur) target += ` (${label.edgeKind(t.tur)})`;
  const values = Object.entries(c.deger || {})
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : v}`).join(', ');
  return [target, values].filter(Boolean).join(' · ');
}

export async function undoCorrection(slug, correction) {
  const name = correction.label || label.op(correction.islem);
  if (!await confirmDialog(`"${name}" düzeltmesi (${correction.id}) geri alınsın mı? Model bu düzeltme olmadan yeniden hesaplanır; gerekirse günlükten yinelenebilir.`,
    { title: 'Düzeltmeyi geri al', confirmLabel: 'Geri al' })) return;
  try {
    await del(p`/api/projects/${slug}/corrections/${correction.id}`);
    changed('Düzeltme geri alındı');
  } catch (error) { toastError(error); }
}

export function correctionsList(slug, corrections) {
  if (!corrections?.length) return note('Bu nesneye elle düzeltme yapılmadı.');
  return h('ul', { class: 'list' }, corrections.map((c) => {
    const [text, kind] = CORRECTION_STATE[c.result?.durum] || ['bekliyor', 'muted'];
    const problem = c.result && c.result.durum !== 'uygulandi' ? c.result.mesaj : null;
    return h('li', { style: { display: 'block' } },
      h('div', { class: 'row' }, h('b', { text: c.label || label.op(c.islem) }), badge(text, kind),
        h('span', { class: 'small muted grow', text: c.id }),
        canEdit() ? button('Geri al', act(() => undoCorrection(slug, c)), { kind: 'small' }) : null),
      h('div', { class: 'small', style: { overflowWrap: 'anywhere' }, text: describeOp(c) }),
      problem ? h('div', { class: 'small', style: { color: 'var(--bad)' }, text: problem }) : null,
      c.neden ? h('div', { class: 'small muted', text: `Neden: ${c.neden}` }) : null,
      h('div', { class: 'small muted' }, `${c.kim || '—'} · `, when(c.zaman)));
  }));
}

function sourceSummary(sources) {
  const first = sources[0];
  const where = [first.file, first.locator].filter(Boolean).join(' · ') || first.extractor || '';
  return `kaynak: ${where}${sources.length > 1 ? ` (+${sources.length - 1})` : ''}`;
}

async function edgeTarget(slug, edge, known) {
  if (edge.kind === 'column_maps_to') return { kaynak: edge.source, hedef: edge.target, tur: edge.kind };
  const [kaynak, hedef] = await Promise.all([nodeRef(slug, edge.source, known), nodeRef(slug, edge.target, known)]);
  return { kaynak, hedef, tur: edge.kind };
}

async function deleteEdge(slug, edge, known) {
  const target = await edgeTarget(slug, edge, known);
  const done = await previewThenApply(slug, [op('bag_sil', target)], {
    title: 'Bağı sil', applyLabel: 'Sil',
    intro: `${label.edgeKind(edge.kind)}: ${edge.source} → ${edge.target} modelden kaldırılır. Düzeltme günlükten geri alınabilir.`,
  });
  if (done) changed('Bağ silindi');
}

async function flipEdge(slug, edge, known) {
  const target = await edgeTarget(slug, edge, known);
  await editOps(slug, {
    title: 'Bağın yönünü çevir', submitLabel: 'Çevir',
    intro: `${label.edgeKind(edge.kind)}: ${edge.source} → ${edge.target} bağı ${edge.target} → ${edge.source} olur (onaylı).`,
    build: (v) => [op('bag_yon_cevir', target, null, v.neden)], message: 'Bağın yönü çevrildi',
  });
}

async function confirmEdge(slug, edge, known) {
  const target = await edgeTarget(slug, edge, known);
  await editOps(slug, {
    title: 'Bağı onayla', submitLabel: 'Onayla',
    intro: `${label.edgeKind(edge.kind)}: ${edge.source} → ${edge.target} onaylı olarak işaretlenir.`,
    build: (v) => [op('onayla', target, null, v.neden)], message: 'Bağ onaylandı',
  });
}

/** Nesnenin ham bağları; düzenleyici için onayla / yönünü çevir / sil. */
export function edgesList(slug, edges, { known } = {}) {
  if (!edges?.length) return note('Ham bağ yok.');
  return h('ul', { class: 'list' }, edges.map((e) => h('li', { style: { display: 'block' } },
    h('div', { class: 'row' }, badge(label.edgeKind(e.kind), 'muted'),
      h('span', { class: 'grow', style: { overflowWrap: 'anywhere' }, text: `${e.source} → ${e.target}` }), statusBadge(e.status)),
    e.expression ? h('div', { class: 'small muted', text: `ifade: ${e.expression}` }) : null,
    e.sources?.length ? h('div', { class: 'small muted', style: { overflowWrap: 'anywhere' }, text: sourceSummary(e.sources) }) : null,
    canEdit() ? h('div', { class: 'row', style: { marginTop: '4px' } },
      e.status === 'draft' ? button('Onayla', act(() => confirmEdge(slug, e, known)), { kind: 'small ok' }) : null,
      DIRECT_KINDS.has(e.kind) ? button('Yönünü çevir', act(() => flipEdge(slug, e, known)), { kind: 'small' }) : null,
      button('Sil', act(() => deleteEdge(slug, e, known)), { kind: 'small danger' })) : null)));
}

// ---- analiz pencereleri --------------------------------------------------- //

export async function showImpact(slug, id, name) {
  let data;
  try {
    data = await get(p`/api/projects/${slug}/impact/${id}`);
  } catch (error) { toastError(error); return; }
  const group = (title, items, screen) => h('div', {},
    h('h3', { style: { marginTop: '4px' }, text: `${title} (${items.length})` }),
    items.length
      ? h('ul', { class: 'list' }, items.map((x) => h('li', {}, h('a', { href: link('p', slug, screen, x.id), text: x.name, onclick: closeOwnModal }))))
      : note('Yok.'));
  await modal({
    title: `Etki analizi: ${name}`, wide: true,
    body: h('div', { class: 'stack' },
      h('p', { class: 'muted', style: { margin: '0' }, text: `${name} bozulur ya da çalışmazsa aşağı akışta etkilenenler (veri akışı ve yürütme sırası birlikte).` }),
      h('div', { class: 'grid-3' },
        stat(data.datasets.length, 'tablo / dosya'), stat(data.processes.length, 'süreç'),
        stat(data.consumption.length, 'tüketim nesnesi (rapor, dış alıcı)', { kind: data.consumption.length ? 'warn' : '' })),
      group('Tüketim katmanı', data.consumption, 'tablolar'),
      group('Tablolar ve dosyalar', data.datasets, 'tablolar'),
      group('Süreçler', data.processes, 'surecler')),
  });
}

export async function findPaths(slug, loadDatasets, sourceName = '') {
  const datasets = await loadDatasets();
  const names = datasets.map((d) => d.name);
  let data = null;
  const values = await formModal({
    title: 'Yol bul', submitLabel: 'Bul',
    intro: 'İki tablo/dosya arasındaki veri akışı yolları (en kısa beş yol). Ad ya da adın bir parçası yazılabilir.',
    fields: [
      { name: 'kaynak', label: 'Başlangıç', required: true, value: sourceName, suggestions: names },
      { name: 'hedef', label: 'Bitiş', required: true, suggestions: names },
    ],
    onSubmit: async (v) => {
      const query = new URLSearchParams({ source: v.kaynak.trim(), target: v.hedef.trim(), limit: '5' });
      data = await get(`${p`/api/projects/${slug}/paths`}?${query}`);
      return v;
    },
  });
  if (!values || !data) return;
  const first = data.paths[0];
  const title = first ? `${first[0].name} → ${first[first.length - 1].name}` : `${values.kaynak} → ${values.hedef}`;
  const body = data.paths.length
    ? h('ol', { class: 'stack', style: { paddingLeft: '20px', margin: '0' } }, data.paths.map((route) => h('li', {},
      h('div', { class: 'small muted', text: `${route.length - 1} adım` }),
      h('div', { style: { overflowWrap: 'anywhere' } }, route.map((n, i) => [
        i ? h('span', { class: 'muted', text: ' → ' }) : null,
        h('a', { href: link('p', slug, n.type === 'process' ? 'surecler' : 'tablolar', n.id), text: n.name, onclick: closeOwnModal }),
        n.type === 'process' ? [' ', badge('süreç', 'muted')] : null,
      ])))))
    : empty('Bu iki nesne arasında veri akışı yolu yok.');
  await modal({ title: `Yollar: ${title}`, wide: true, body });
}

// ---- nesne düzenlemeleri (tablolar ve süreçler) -------------------------- //

const unchanged = (a, b) => String(a ?? '').trim() === String(b ?? '').trim();
const NAME_PROBLEM = "Ad '#' içeremez (kolon ayırıcısı olarak kullanılır)";

export function renameNode(slug, node) {
  return editOps(slug, {
    title: `Yeniden adlandır: ${node.name}`,
    intro: 'Eski ad saklanır; eski adla verilmiş düzeltmeler çözülmeye devam eder.',
    fields: [{ name: 'ad', label: 'Yeni ad', required: true, value: node.name }],
    validate: (v) => (unchanged(v.ad, node.name) ? 'Ad değişmedi' : v.ad.includes('#') ? NAME_PROBLEM : null),
    build: (v) => [op('yeniden_adlandir', { nesne: node.name }, { ad: v.ad.trim() }, v.neden)],
    message: 'Ad değişti',
  });
}

export function setLayer(slug, node) {
  return editOps(slug, {
    title: `Katman: ${node.name}`,
    fields: [{ name: 'katman', label: 'Katman', type: 'select', options: options('layers'), value: node.layer }],
    validate: (v) => (v.katman === node.layer ? 'Katman değişmedi' : null),
    build: (v) => [op('katman_degistir', { nesne: node.name }, { katman: v.katman }, v.neden)],
    message: 'Katman değişti',
  });
}

export function setKind(slug, node, list) {
  return editOps(slug, {
    title: `Tür: ${node.name}`,
    fields: [{ name: 'tur', label: 'Tür', type: 'select', options: options(list), value: node.kind }],
    validate: (v) => (v.tur === node.kind ? 'Tür değişmedi' : null),
    build: (v) => [op('tur_degistir', { nesne: node.name }, { tur: v.tur }, v.neden)],
    message: 'Tür değişti',
  });
}

export async function setSystem(slug, node, loadSiblings) {
  let systems = [];
  try {
    systems = [...new Set((await loadSiblings()).map((x) => x.system).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'tr'));
  } catch { /* öneriler olmadan da girilebilir */ }
  return editOps(slug, {
    title: `Sistem: ${node.name}`,
    fields: [{ name: 'sistem', label: 'Sistem', value: node.system || '', suggestions: systems, hint: 'Boş bırakılırsa sistem bilgisi kaldırılır.' }],
    validate: (v) => (unchanged(v.sistem, node.system) ? 'Sistem değişmedi' : null),
    build: (v) => [op('sistem_ata', { nesne: node.name }, { sistem: v.sistem.trim() }, v.neden)],
    message: 'Sistem değişti',
  });
}

export function setSensitivity(slug, node) {
  return editOps(slug, {
    title: `Hassasiyet: ${node.name}`,
    fields: [{ name: 'hassasiyet', label: 'Hassasiyet', type: 'select', options: options('sensitivity'), value: node.sensitivity }],
    validate: (v) => (v.hassasiyet === node.sensitivity ? 'Hassasiyet değişmedi' : null),
    build: (v) => [op('hassasiyet_ata', { nesne: node.name }, { hassasiyet: v.hassasiyet }, v.neden)],
    message: 'Hassasiyet değişti',
  });
}

export function setDescription(slug, node) {
  return editOps(slug, {
    title: `Açıklama: ${node.name}`,
    fields: [{ name: 'aciklama', label: 'Açıklama', type: 'textarea', wide: true, value: node.description || '', hint: 'Boş bırakılırsa açıklama kaldırılır.' }],
    validate: (v) => (unchanged(v.aciklama, node.description) ? 'Açıklama değişmedi' : null),
    build: (v) => [op('aciklama', { nesne: node.name }, { aciklama: v.aciklama.trim() }, v.neden)],
    message: 'Açıklama kaydedildi',
  });
}

export function confirmNode(slug, node) {
  return editOps(slug, {
    title: `Onayla: ${node.name}`, submitLabel: 'Onayla',
    intro: 'Nesne onaylı olarak işaretlenir (kaynağına elle onay eklenir). Bağları ayrıca onaylanmaz.',
    build: (v) => [op('onayla', { nesne: node.name }, null, v.neden)],
    message: 'Nesne onaylandı',
  });
}

export async function deleteNode(slug, node, screen) {
  const done = await previewThenApply(slug, [op('nesne_sil', { nesne: node.name })], {
    title: `Sil: ${node.name}`, applyLabel: 'Sil',
    intro: 'Nesne ve ona bağlı tüm bağlar modelden kaldırılır. Dokümanlar değişmez; düzeltme günlükten geri alınabilir.',
  });
  if (!done) return;
  go('p', slug, screen);
  changed('Nesne silindi');
}

/** Birleştir: aynı türden diğer nesneleri seçtirir, kalacak adı sorar, önizleyip uygular. */
export async function mergeNodes(slug, node, { candidates, screen, noun }) {
  const others = (await candidates()).filter((x) => x.id !== node.id);
  if (!others.length) { toast('Birleştirilecek başka nesne yok', 'warn'); return; }
  const chosen = new Map();
  const search = h('input', { type: 'search', placeholder: 'Ada göre süz…', 'aria-label': 'Adayları süz' });
  const box = h('div', { style: { maxHeight: '260px', overflow: 'auto', border: '1px solid var(--line)', borderRadius: '8px', padding: '4px 8px' } });
  const keep = h('select', { 'aria-label': 'Kalacak ad' });
  const custom = h('input', { type: 'text', placeholder: 'Boşsa seçilen ad kalır', 'aria-label': 'Yeni ad' });
  const count = h('div', { class: 'small muted' });
  const drawKeep = () => {
    const previous = keep.value;
    const names = [node.name, ...chosen.values()];
    clear(keep);
    for (const name of names) keep.append(h('option', { value: name }, name));
    keep.value = names.includes(previous) ? previous : node.name;
    count.textContent = chosen.size ? `${chosen.size} nesne seçildi` : 'Henüz seçim yok';
  };
  const drawBox = () => {
    clear(box);
    const needle = fold(search.value.trim());
    const shown = others.filter((x) => chosen.has(x.id) || !needle || fold(x.name).includes(needle));
    for (const x of shown.slice(0, 300)) {
      box.append(h('label', { class: 'field inline', style: { padding: '3px 0' } },
        h('input', {
          type: 'checkbox', checked: chosen.has(x.id),
          onchange: (event) => { if (event.target.checked) chosen.set(x.id, x.name); else chosen.delete(x.id); drawKeep(); },
        }),
        h('span', { text: x.name }), x.layer ? layerPill(x.layer) : null));
    }
    if (!shown.length) box.append(note('Eşleşen nesne yok.'));
    else if (shown.length > 300) box.append(note(`${shown.length - 300} nesne daha var; süzgeçle daraltın.`));
  };
  search.addEventListener('input', drawBox);
  drawBox();
  drawKeep();
  let ops = null;
  const choice = await modal({
    title: `Birleştir: ${node.name}`, wide: true,
    body: h('div', { class: 'stack' },
      h('p', { class: 'muted', style: { margin: '0' }, text: `${node.name} ile aynı ${noun} olan kayıtları seçin. Bağlar ve kaynaklar kalan nesnede toplanır; diğer adlar takma ad olarak saklanır.` }),
      search, box, count,
      h('div', { class: 'form-grid' },
        h('label', { class: 'field' }, h('span', { class: 'label', text: 'Kalacak ad' }), keep),
        h('label', { class: 'field' }, h('span', { class: 'label', text: 'Ya da yeni ad (isteğe bağlı)' }), custom))),
    actions: [
      { label: 'Vazgeç', value: null },
      {
        label: 'Önizle', kind: 'primary', value: 'ok',
        onClick: () => {
          if (!chosen.size) { toast('Birleştirilecek en az bir nesne seçin', 'warn'); return false; }
          const name = custom.value.trim() || keep.value;
          if (name.includes('#')) { toast(NAME_PROBLEM, 'warn'); return false; }
          ops = [op('birlestir', { nesneler: [node.name, ...chosen.values()] }, { ad: name })];
          return true;
        },
      },
    ],
  });
  if (choice !== 'ok' || !ops) return;
  const done = await previewThenApply(slug, ops, {
    title: 'Birleştirme önizlemesi', applyLabel: 'Birleştir',
    intro: `${[node.name, ...chosen.values()].join(' + ')} → "${ops[0].deger.ad}"`,
  });
  if (!done) return;
  const removed = new Set((done.diff.nodes_removed || []).map((n) => n.id));
  const survivor = [node.id, ...chosen.keys()].find((id) => !removed.has(id));
  if (survivor && survivor !== node.id) go('p', slug, screen, survivor);
  changed('Nesneler birleştirildi');
}

/** Yeni tablo/dosya (type='dataset') ya da süreç (type='process'). Var olan adla eşleşirse eklemez. */
export async function createNode(slug, type, screen) {
  const dataset = type === 'dataset';
  let addedId = null;
  const values = await formModal({
    title: dataset ? 'Yeni tablo / dosya' : 'Yeni süreç', submitLabel: 'Ekle',
    intro: 'Elle eklenen nesne onaylıdır; dokümanlar yeniden okunduğunda da korunur.',
    fields: [
      { name: 'ad', label: 'Ad', required: true, placeholder: dataset ? 'ör. DWH.F_KART_ISLEM' : 'ör. LOAD_F_KART_ISLEM' },
      { name: 'tur', label: 'Tür', type: 'select', options: [{ value: '', label: dataset ? 'Addan tahmin et' : 'ETL mapping (varsayılan)' }, ...options(dataset ? 'dataset_kinds' : 'process_kinds')] },
      { name: 'katman', label: 'Katman', type: 'select', options: [{ value: '', label: dataset ? 'Addan tahmin et' : 'Staging (varsayılan)' }, ...options('layers')] },
      { name: 'sistem', label: 'Sistem' },
      { name: 'aciklama', label: 'Açıklama', type: 'textarea', wide: true },
      reasonField(),
    ],
    validate: (v) => (v.ad.includes('#') ? NAME_PROBLEM : null),
    onSubmit: async (v) => {
      const deger = {};
      for (const key of ['tur', 'katman', 'sistem', 'aciklama']) if (v[key].trim()) deger[key] = v[key].trim();
      const ops = [op('nesne_ekle', { ad: v.ad.trim(), tip: type }, deger, v.neden)];
      // Sunucu var olan (ya da şemasız adı eşleşen) nesneyi sessizce günceller; önce önizleyip engelleriz.
      const diff = await post(p`/api/projects/${slug}/corrections/preview`, { ops });
      const problem = (diff.results || []).find((r) => r.durum !== 'uygulandi');
      if (problem) throw new Error(problem.mesaj);
      const added = (diff.nodes_added || []).find((n) => n.type === type);
      if (!added) {
        toast(`"${v.ad.trim()}" var olan bir nesneyle eşleşiyor; o nesneyi açıp düzenleyin.`, 'warn');
        return false;
      }
      await sendOps(slug, ops);
      addedId = added.id;
      return v;
    },
  });
  if (!values) return;
  if (addedId) go('p', slug, screen, addedId);
  changed(dataset ? 'Tablo/dosya eklendi' : 'Süreç eklendi');
}
