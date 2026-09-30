// Doküman inceleme: solda doküman blokları (okuyucularla aynı konum etiketleri), sağda bu dokümandan
// çıkarılan öğeler. Bir öğeye tıklanınca kaynağındaki blok bulunur ve kanıt metni vurgulanır.
// Kuralla çözülemeyen ("insan bakmalı") paragraflardan elle bağ eklenebilir.

import { get, p, post } from '../core/api.js';
import { fmtDate, fold, h } from '../core/dom.js';
import { link } from '../core/router.js';
import { canEdit, emit, label, store } from '../core/store.js';
import {
  badge, buildForm, button, confirmDialog, docStatusBadge, empty, evidence, modal, pageHeader, statusBadge, toast,
  toastError,
} from '../core/ui.js';
import { detectedBadge } from './documents.js';

export const refreshOnChange = true;

/** Onay kuyruğu türü başına en çok bu kadar öğe döner (project/review.py MAX_ITEMS_PER_TYPE). */
const REVIEW_LIMIT = 300;

const GROUPS = [
  { type: 'dataset', title: 'Tablolar ve dosyalar' },
  { type: 'process', title: 'Süreçler' },
  { type: 'edge', title: 'Bağlar' },
];

// ---- metin vurgulama ---------------------------------------------------- //

const foldCache = new Map();

/** Tek karakteri uzunluğunu koruyarak katlar (büyük/küçük harf ve Türkçe karakter duyarsız). */
function foldChar(c) {
  let value = foldCache.get(c);
  if (value === undefined) {
    const folded = fold(c);
    const lower = c.toLowerCase();
    value = folded.length === 1 ? folded : (lower.length === 1 ? lower : c);
    foldCache.set(c, value);
  }
  return value;
}

/** Boşluk dizilerini tek boşluğa indirir, katlar; her çıktı karakterinin girdideki yerini tutar. */
function normalize(text) {
  let out = '';
  const map = [];
  let space = true;
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i];
    if (/\s/.test(c)) {
      if (!space) { out += ' '; map.push(i); space = true; }
      continue;
    }
    out += foldChar(c);
    map.push(i);
    space = false;
  }
  return { text: out, map };
}

const isWordChar = (c) => c !== undefined && /[\p{L}\p{N}_]/u.test(c);

/** Kök altındaki metin düğümleri (konum etiketi, satır numarası gibi .document-skip parçaları hariç). */
function textNodes(root) {
  const nodes = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (node.parentElement && node.parentElement.closest('.document-skip')) continue;
    nodes.push(node);
  }
  return nodes;
}

/**
 * ``needle`` geçişlerinin özgün metindeki [başlangıç, bitiş) aralıkları (artan, çakışmasız).
 * ``normalized``: normalize(metin). ``word``: yalnız tam sözcük eşleşmeleri.
 */
function findRanges(normalized, needle, { word = false } = {}) {
  const target = normalize(String(needle || '')).text.trim();
  if (!target) return [];
  const { text: hay, map } = normalized;
  const ranges = [];
  let from = 0;
  for (;;) {
    const index = hay.indexOf(target, from);
    if (index === -1) break;
    const end = index + target.length;
    if (word && (isWordChar(hay[index - 1]) || isWordChar(hay[end]))) { from = index + 1; continue; }
    ranges.push([map[index], map[end - 1] + 1]);
    from = end;
  }
  return ranges;
}

/**
 * ``needle`` metninin kök altındaki tüm geçişlerini <mark> ile sarar (innerHTML yok; metin düğümleri bölünür).
 * Düğüm (ör. tablo hücresi) sınırları boşluk sayılır. Dönüş: eşleşme sayısı.
 */
function markAll(root, needle, options) {
  const nodes = textNodes(root);
  if (!nodes.length) return 0;
  let full = '';
  const spans = [];
  for (const node of nodes) {
    const start = full.length;
    full += node.data;
    spans.push({ node, start, end: full.length, segments: [] });
    full += '\n';
  }
  const ranges = findRanges(normalize(full), needle, options);
  for (const [a, b] of ranges) {
    for (const span of spans) {
      const s = Math.max(a, span.start);
      const e = Math.min(b, span.end);
      if (s < e) span.segments.push([s - span.start, e - span.start]);
    }
  }
  for (const { node, segments } of spans) {
    // Sondan başa: her bölmeden sonra ``node`` yalnız [0, s) kısmını tutar, önceki parçaların yeri değişmez.
    for (const [s, e] of segments.reverse()) {
      if (e < node.data.length) node.splitText(e);
      const middle = s > 0 ? node.splitText(s) : node;
      const mark = document.createElement('mark');
      middle.parentNode.replaceChild(mark, middle);
      mark.appendChild(middle);
    }
  }
  return ranges.length;
}

function clearMarks(container) {
  for (const mark of container.querySelectorAll('mark')) {
    const parent = mark.parentNode;
    while (mark.firstChild) parent.insertBefore(mark.firstChild, mark);
    parent.removeChild(mark);
    parent.normalize();
  }
}

/** Öğenin adları: bağlarda iki uç. Şemalı adlarda şemasız biçim de denenir (ör. DWH.F_X → F_X). */
function itemNames(item) {
  const names = item.type === 'edge' ? String(item.name || '').split(' → ') : [String(item.name || '')];
  return names.map((n) => n.trim()).filter(Boolean);
}

function bareName(name) {
  const index = name.lastIndexOf('.');
  const bare = index >= 0 ? name.slice(index + 1) : '';
  return bare.length >= 5 ? bare : null; // kısa sonekler dosya uzantısıdır (.dat, .csv)
}

function highlightItem(root, item) {
  if (item.evidence && markAll(root, item.evidence)) return;
  for (const name of itemNames(item)) {
    if (!markAll(root, name, { word: true })) {
      const bare = bareName(name);
      if (bare) markAll(root, bare, { word: true });
    }
  }
}

// ---- blok eşleme ----------------------------------------------------------- //

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** "satır {n}" → /^satır (\d+)(?:$| ·)/ */
function rowPattern(template) {
  const source = escapeRegExp(template || 'satır {n}').replace('\\{n\\}', '(\\d+)');
  return new RegExp(`^${source}(?:$| ·)`);
}

/**
 * Öğenin konumunu bloklardan birine bağlar: {entry, row?, line?} ya da null.
 * Sıra: tam eşleşme → kod bloğunun satır aralığı → "blok · …" öneki → "blok …" öneki (ör. "ifade 1 (insert)").
 */
function locate(entries, locator) {
  if (!locator) return null;
  const withDetail = (entry, suffix) => {
    if (entry.block.kind === 'table' && suffix) {
      const match = entry.rowRegex.exec(suffix);
      if (match) return { entry, row: Number(match[1]) };
    }
    if (entry.block.kind === 'code' && entry.lineStart !== null) {
      const match = /satır (\d+)$/.exec(suffix || locator);
      if (match) return { entry, line: Number(match[1]) };
    }
    return { entry };
  };
  const exact = entries.find((e) => e.block.locator === locator);
  if (exact) return withDetail(exact, '');
  const lineMatch = /^satır (\d+)$/.exec(locator);
  if (lineMatch) {
    const line = Number(lineMatch[1]);
    const code = entries.find((e) => e.lineStart !== null && line >= e.lineStart && line < e.lineStart + e.lineCount);
    if (code) return { entry: code, line };
  }
  for (const separator of [' · ', ' ']) {
    let best = null;
    for (const entry of entries) {
      const own = entry.block.locator;
      if (own && locator.startsWith(own + separator) && (!best || own.length > best.block.locator.length)) best = entry;
    }
    if (best) return withDetail(best, locator.slice(best.block.locator.length + separator.length));
  }
  return null;
}

// ---- blok çizimi -------------------------------------------------------- //

function renderTable(block, entry) {
  const tbody = h('tbody');
  (block.rows || []).forEach((row, index) => {
    const n = index + 1;
    const tr = h('tr', { dataset: { row: String(n) } },
      h('td', { class: 'num muted document-skip', text: String(n) }),
      row.map((cell) => h('td', { class: 'wrap', text: cell ?? '' })));
    entry.rows.set(n, tr);
    tbody.append(tr);
  });
  return h('div', {},
    h('div', { class: 'table-wrap' }, h('table', { class: 'data document-table' },
      h('thead', {}, h('tr', {}, h('th', { class: 'num', text: '#' }), (block.headers || []).map((t) => h('th', { text: t ?? '' })))),
      tbody)),
    block.truncated ? h('div', { class: 'small muted document-skip', text: 'Önizlemede ilk 2.000 satır gösteriliyor.' }) : null);
}

function renderCode(block, entry) {
  const lines = String(block.text ?? '').split(/\r\n|\r|\n/);
  const pre = h('pre', { class: 'document-code' });
  lines.forEach((line, index) => {
    const number = entry.lineStart !== null ? entry.lineStart + index : null;
    const node = h('span', { class: 'document-line' },
      number !== null ? h('span', { class: 'document-ln document-skip', text: String(number) }) : null,
      h('span', { class: 'document-lt', text: line }));
    if (number !== null) entry.lines.set(number, node);
    pre.append(node);
  });
  entry.lineCount = lines.length;
  return pre;
}

function renderContent(block, entry) {
  switch (block.kind) {
    case 'table': return renderTable(block, entry);
    case 'code': return renderCode(block, entry);
    default: return h('div', { class: 'document-text', text: block.text ?? '' });
  }
}

// ---- ekran -------------------------------------------------------------- //

export async function render(root, params, query = {}) {
  const { slug, name } = params;
  const requested = /^\d+$/.test(query.version || '') ? Number(query.version) : null;
  const previewUrl = p`/api/projects/${slug}/documents/${name}/preview` + (requested ? `?version=${requested}` : '');
  const [preview, detail] = await Promise.all([
    get(previewUrl),
    get(p`/api/projects/${slug}/documents/${name}`),
  ]);
  const versions = detail.versions || [];
  const latest = versions.length ? versions[versions.length - 1].n : null;
  const shown = requested ?? latest;
  const isLatest = shown === latest;
  const report = (detail.reports || []).find((r) => r.file === name) || (detail.reports || [])[0] || null;
  const editable = canEdit() && isLatest;
  const docLink = link('p', slug, 'dokuman', name);

  // Onay kuyruğundaki açık paragraflar (kapatılanlar kuyrukta görünmez).
  const leftovers = isLatest ? preview.leftovers || [] : [];
  let openParagraphs = new Map();
  let reviewComplete = false;
  if (leftovers.length) {
    try {
      const review = await get(p`/api/projects/${slug}/review?type=paragraf`);
      const items = review.items || [];
      reviewComplete = items.length < REVIEW_LIMIT;
      openParagraphs = new Map(items.filter((i) => i.file === name).map((i) => [i.locator, i]));
    } catch { /* kuyruk okunamazsa paragraflar açık sayılır */ }
  }
  const paragraphState = (locator) => {
    if (openParagraphs.has(locator)) return 'open';
    return reviewComplete ? 'closed' : 'open';
  };

  // ---- başlık ---------------------------------------------------------- //

  const actions = [];
  if (versions.length > 1) {
    const select = h('select', {
      'aria-label': 'Sürüm', style: { width: 'auto' },
      onchange: () => {
        const value = Number(select.value);
        location.hash = docLink + (value === latest ? '' : `?version=${value}`);
      },
    }, [...versions].reverse().map((v) => h('option', { value: String(v.n), selected: v.n === shown },
      `v${v.n}${v.n === latest ? ' (güncel)' : ''} · ${fmtDate(v.zaman)}`)));
    actions.push(select);
  }
  const downloadUrl = p`/api/projects/${slug}/documents/${name}/download` + (isLatest ? '' : `?version=${shown}`);
  actions.push(h('a', { class: 'btn', href: downloadUrl, download: '' }, 'İndir'));
  actions.push(h('a', { class: 'btn', href: link('p', slug, 'dokumanlar') }, 'Dokümanlar'));

  root.append(pageHeader({
    title: name,
    subtitle: [
      h('span', { text: `${preview.label || 'okuyucu yok'} · v${shown ?? '—'}${versions.length > 1 ? ` / ${versions.length}` : ''} ` }),
      report ? docStatusBadge(report.status) : badge('okunmadı', 'muted'), ' ',
      report ? detectedBadge(report.detected) : null,
    ],
    crumbs: [
      { label: 'Projeler', href: '#/projeler' },
      { label: store.projectInfo?.ad || slug, href: link('p', slug) },
      { label: 'Dokümanlar', href: link('p', slug, 'dokumanlar') },
      { label: name },
    ],
    actions,
  }));

  if (!isLatest) {
    root.append(h('div', { class: 'banner warn' }, h('span', { class: 'grow',
      text: `Eski bir sürümü (v${shown}) görüyorsunuz. Sağdaki öğeler güncel sürümün okumasından gelir; konumları bu sürümle örtüşmeyebilir.` }),
    h('a', { class: 'btn small', href: docLink }, 'Güncel sürüme dön')));
  }
  if (report?.notes?.length) {
    root.append(h('div', { class: 'banner info' }, h('ul', { class: 'grow document-notes' }, report.notes.map((n) => h('li', { text: n })))));
  }

  // ---- bloklar --------------------------------------------------------- //

  const blocksBox = h('div', { class: 'card doc-blocks' });
  const leftoverLocators = new Set(leftovers.map((l) => l.locator));
  const entries = [];
  const matchedLeftovers = new Set();

  // addLink/closeOnly aşağıda tanımlıdır; düğmeler yalnız tıklanınca çağırır.
  const paragraphTools = (leftover) => {
    const state = paragraphState(leftover.locator);
    return h('div', { class: 'row document-leftover-tools document-skip' },
      state === 'closed' ? badge('işlendi', 'ok') : badge('insan bakmalı', 'warn'),
      state === 'closed' ? h('span', { class: 'small muted', text: 'Onay kuyruğunda kapatıldı.' })
        : h('span', { class: 'small muted', text: 'Tablo adı geçiyor ama akış kuralla çıkarılamadı.' }),
      editable && state !== 'closed' ? [
        button('Bu paragraftan bağ ekle', () => addLink(leftover), { kind: 'small primary' }),
        button('İşlendi olarak kapat', () => closeOnly(leftover), { kind: 'small' }),
      ] : null);
  };

  (preview.blocks || []).forEach((block, index) => {
    const entry = {
      block, index, el: null, content: null, rows: new Map(), lines: new Map(), lineCount: 0,
      lineStart: block.kind === 'code' && Number.isInteger(block.line_start) ? block.line_start : null,
      rowRegex: rowPattern(block.row_locator),
    };
    entry.content = renderContent(block, entry);
    const leftover = block.locator && leftoverLocators.has(block.locator)
      ? leftovers.find((l) => l.locator === block.locator) : null;
    if (leftover) matchedLeftovers.add(leftover.locator);
    entry.el = h('div', { class: ['block', block.kind, leftover ? 'leftover' : ''], dataset: { index: String(index) } },
      h('span', { class: 'loc document-skip', text: block.locator || '—' }), entry.content,
      leftover ? paragraphTools(leftover) : null);
    entries.push(entry);
    blocksBox.append(entry.el);
  });
  if (!entries.length) blocksBox.append(empty('Bu doküman için gösterilecek içerik yok.'));

  // Bloklarla eşleşmeyen insan-bakmalı metinler (ör. uzun Excel hücreleri) ayrıca listelenir.
  const unmatched = leftovers.filter((l) => !matchedLeftovers.has(l.locator));
  if (unmatched.length) {
    blocksBox.append(h('h3', { text: 'Diğer insan bakmalı metinler' }));
    for (const leftover of unmatched) {
      blocksBox.append(h('div', { class: 'block paragraph leftover' },
        h('span', { class: 'loc document-skip', text: leftover.locator || '—' }),
        h('div', { class: 'document-text', text: leftover.text || '' }), paragraphTools(leftover)));
    }
  }

  // ---- öğeler ---------------------------------------------------------- //

  const seen = new Set();
  const items = [];
  for (const item of preview.items || []) {
    const key = `${item.type}|${item.id}|${item.locator || ''}|${item.evidence || ''}`;
    if (seen.has(key)) continue;
    seen.add(key);
    items.push({ ...item, target: locate(entries, item.locator) });
  }
  const order = (item) => [item.target ? item.target.entry.index : Number.MAX_SAFE_INTEGER,
    item.target?.row ?? item.target?.line ?? 0];
  items.sort((a, b) => {
    const [x1, x2] = order(a); const [y1, y2] = order(b);
    return x1 - y1 || x2 - y2 || String(a.name).localeCompare(String(b.name), 'tr');
  });

  let activeItem = null;
  const focus = (item, node) => {
    for (const el of blocksBox.querySelectorAll('.block.active')) el.classList.remove('active');
    for (const el of blocksBox.querySelectorAll('.document-hit')) el.classList.remove('document-hit');
    clearMarks(blocksBox);
    if (activeItem) activeItem.classList.remove('selected');
    activeItem = node;
    node.classList.add('selected');
    if (!item.target) { toast('Bu öğenin konumu önizlemede bulunamadı', 'warn'); return; }
    const { entry, row, line } = item.target;
    entry.el.classList.add('active');
    let scope = entry.content;
    let scrollTo = entry.el;
    const rowNode = row ? entry.rows.get(row) : null;
    const lineNode = line ? entry.lines.get(line) : null;
    if (rowNode) { rowNode.classList.add('document-hit'); scope = rowNode; scrollTo = rowNode; }
    if (lineNode) { lineNode.classList.add('document-hit'); scope = lineNode; scrollTo = lineNode; }
    highlightItem(scope, item);
    scrollTo.scrollIntoView({ block: 'center', behavior: 'smooth' });
  };

  const itemNode = (item) => {
    const target = item.type === 'dataset' ? link('p', slug, 'tablolar', item.id)
      : item.type === 'process' ? link('p', slug, 'surecler', item.id) : null;
    const where = [item.locator || 'konum yok', item.extractor, item.confidence != null ? `güven ${Math.round(item.confidence * 100)}%` : null]
      .filter(Boolean).join(' · ');
    const node = h('li', { class: 'document-item', tabindex: '0', role: 'button', 'aria-label': `${item.name} kaynağını göster` },
      h('div', { class: 'grow' },
        h('div', { class: 'row' }, h('b', { text: item.name }), statusBadge(item.status),
          item.type === 'edge' && item.kind ? badge(label.edgeKind(item.kind), 'muted') : null),
        h('div', { class: 'small muted', text: where }),
        item.target ? null : h('div', { class: 'small', style: { color: 'var(--warn)' }, text: 'konumu önizlemede bulunamadı' })),
      target ? h('a', { class: 'btn small', href: target, title: 'Ayrıntıya git', onclick: (e) => e.stopPropagation() }, '→') : null);
    node.addEventListener('click', () => focus(item, node));
    node.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); focus(item, node); }
    });
    return node;
  };

  const filter = h('input', { type: 'search', placeholder: 'Öğe süz…', 'aria-label': 'Öğeleri süz' });
  const groupsBox = h('div');
  const drawItems = () => {
    groupsBox.replaceChildren();
    activeItem = null;
    const needle = fold(filter.value.trim());
    const visible = needle ? items.filter((i) => fold(i.name).includes(needle)) : items;
    for (const group of GROUPS) {
      const list = visible.filter((i) => i.type === group.type);
      if (!list.length) continue;
      groupsBox.append(h('h3', { text: `${group.title} (${list.length})` }), h('ul', { class: 'list' }, list.map(itemNode)));
    }
    if (!visible.length) groupsBox.append(empty(needle ? 'Eşleşen öğe yok' : 'Bu dokümandan öğe çıkarılmadı.'));
  };
  filter.addEventListener('input', drawItems);
  drawItems();

  const side = h('div', { class: 'card document-side' },
    h('div', { class: 'card-head' }, h('h2', { text: 'Çıkarılan öğeler' }), badge(String(items.length), 'muted')),
    h('p', { class: 'small muted', text: 'Bir öğeye tıklayın: kaynağındaki blok bulunur ve kanıt metni vurgulanır.' }),
    items.length > 8 ? h('div', { class: 'toolbar' }, filter) : null,
    groupsBox);

  root.append(h('div', { class: 'doc-view' }, blocksBox, side));

  // ---- insan bakmalı paragraflar -------------------------------------- //

  let namesCache = null;
  const loadNames = async () => {
    if (!namesCache) {
      const [datasets, processes] = await Promise.all([
        get(p`/api/projects/${slug}/datasets?limit=2000`).catch(() => ({ items: [] })),
        get(p`/api/projects/${slug}/processes?limit=2000`).catch(() => ({ items: [] })),
      ]);
      namesCache = { datasets: (datasets.items || []).map((d) => d.name), processes: (processes.items || []).map((x) => x.name) };
    }
    return namesCache;
  };

  /** Paragrafı onay kuyruğunda "reddet" kararıyla kapatır. Dönüş: kapandı mı. */
  const closeParagraph = async (leftover) => {
    const review = await get(p`/api/projects/${slug}/review?type=paragraf`);
    const item = (review.items || []).find((i) => i.file === name && i.locator === leftover.locator);
    if (!item) { toast('Paragraf onay kuyruğunda bulunamadı; zaten kapatılmış olabilir.', 'warn'); return false; }
    await post(p`/api/projects/${slug}/review/decide`, {
      decisions: [{ key: item.key, decision: 'reject' }], reason: `Doküman incelemesinde işlendi: ${name} · ${leftover.locator}`.slice(0, 500),
    });
    toast('Paragraf işlendi olarak kapatıldı', 'ok');
    return true;
  };

  const closeOnly = async (leftover) => {
    if (!await confirmDialog('Bu paragraf onay kuyruğundan "işlendi" olarak kaldırılsın mı? Kapatılan paragraf bir daha sorulmaz; günlükten geri alınabilir.',
      { title: 'Paragrafı kapat', confirmLabel: 'Kapat' })) return;
    try {
      if (await closeParagraph(leftover)) emit('project:changed');
    } catch (error) { toastError(error); }
  };

  const addLink = async (leftover) => {
    let names;
    try { names = await loadNames(); } catch (error) { toastError(error); return; }
    const form = buildForm([
      { name: 'kaynak', label: 'Kaynak', required: true, placeholder: 'ör. STG_MUSTERI' },
      { name: 'hedef', label: 'Hedef', required: true, placeholder: 'ör. ODS_MUSTERI' },
      { name: 'surec', label: 'Süreç (isteğe bağlı)', placeholder: 'ör. SP_ODS_MUSTERI', wide: true,
        hint: 'Akışı yapan job, prosedür ya da mapping. Boş bırakılırsa bağ doğrudan (türetim) olur.' },
    ]);
    const input = (field) => form.el.querySelector(`[name="${field}"]`);
    input('kaynak').setAttribute('list', 'document-dataset-names');
    input('hedef').setAttribute('list', 'document-dataset-names');
    input('surec').setAttribute('list', 'document-process-names');
    const datalists = [
      h('datalist', { id: 'document-dataset-names' }, names.datasets.map((n) => h('option', { value: n }))),
      h('datalist', { id: 'document-process-names' }, names.processes.map((n) => h('option', { value: n }))),
    ];
    // Paragrafta geçen bilinen adlar: tıklanınca boş olan alana yazılır.
    const paragraph = normalize(leftover.text || '');
    const mentioned = names.datasets.filter((n) => findRanges(paragraph, n, { word: true }).length > 0).slice(0, 12);
    const fill = (value) => {
      const target = !input('kaynak').value.trim() ? input('kaynak') : input('hedef');
      target.value = value;
      target.focus();
    };
    const chips = mentioned.length
      ? h('div', { class: 'row' }, h('span', { class: 'small muted', text: 'Paragrafta geçen:' }),
        mentioned.map((n) => h('button', { type: 'button', class: 'btn small ghost', onclick: () => fill(n) }, n)))
      : null;
    const body = h('div', { class: 'stack' },
      evidence({ file: name, locator: leftover.locator, evidence: leftover.text }),
      chips, form.el, datalists);

    let submitting = false;
    let created = null;
    const result = await modal({
      title: 'Paragraftan bağ ekle', body, wide: true,
      actions: [
        { label: 'Vazgeç', value: null },
        {
          label: 'Bağı ekle', kind: 'primary', value: 'ok',
          onClick: async () => {
            if (submitting) return false;
            const values = form.values();
            const kaynak = String(values.kaynak || '').trim();
            const hedef = String(values.hedef || '').trim();
            const surec = String(values.surec || '').trim();
            if (!kaynak || !hedef) { toast('Kaynak ve hedef zorunlu', 'warn'); return false; }
            if (fold(kaynak) === fold(hedef)) { toast('Kaynak ve hedef aynı olamaz', 'warn'); return false; }
            submitting = true;
            try {
              const response = await post(p`/api/projects/${slug}/corrections`, {
                ops: [{ islem: 'bag_ekle', hedef: { kaynak, hedef }, deger: surec ? { surec } : {},
                  neden: `Doküman: ${name} · ${leftover.locator}`.slice(0, 500) }],
              });
              created = (response.created || [])[0] || null;
              return true;
            } finally { submitting = false; }
          },
        },
      ],
    });
    if (result !== 'ok') return;
    toast(`Bağ eklendi${created?.id ? ` (${created.id})` : ''}`, 'ok');
    if (paragraphState(leftover.locator) === 'open'
      && await confirmDialog('Paragraftaki akış girildi. Paragraf onay kuyruğunda "işlendi" olarak kapatılsın mı?',
        { title: 'Paragrafı kapat', confirmLabel: 'Kapat' })) {
      try { await closeParagraph(leftover); } catch (error) { toastError(error); }
    }
    emit('project:changed');
  };
}
