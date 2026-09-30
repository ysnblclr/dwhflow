// Ortak ayar düzenleyicisi: proje ayarları (#/p/:slug/ayarlar) ve genel ayarlar (#/ayarlar) kullanır.
//
// Etkin değerler (values) bellekte bir taslağa kopyalanır; her sekme yalnız kendi anahtarlarını kaydeder ve
// yamaya yalnız değişen anahtarlar girer (böylece alt katmandan gelen değerler bu katmana sabitlenmez).
// Sunucu kuralları (project/settings.py): bölümler birleşir; okuma.es_anlamlilar, okuma.sistemler,
// okuma.okuyucu_zorla ve yz.profiller bütün olarak değişir; ``null`` bir anahtarı bu katmandan siler.

import { ApiError, download, get, p, post, put } from '../core/api.js';
import { append, clear, fold, h } from '../core/dom.js';
import { link, setDirty } from '../core/router.js';
import { canEdit, isAdmin, label, options, store } from '../core/store.js';
import {
  badge, button, confirmDialog, empty, formDialog, layerPill, modal, originBadge, tabs, toast, toastError, trackJob,
} from '../core/ui.js';

const SECTIONS = [
  { id: 'okuma', label: 'Okuma kuralları', reading: true, paths: ['okuma.es_anlamlilar'] },
  { id: 'ad', label: 'Adlandırma ve katman', reading: true, paths: ['okuma.ad_kurallari', 'okuma.varsayilan_ad_kurallari'] },
  { id: 'sistem', label: 'Sistemler', reading: true, paths: ['okuma.sistemler'] },
  { id: 'okuyucu', label: 'Okuyucular ve şablonlar', reading: true, paths: ['okuma.okuyucu_zorla', 'okuma.sablonlar'] },
  { id: 'diyagram', label: 'Diyagram', paths: ['diyagram.butce', 'diyagram.job_butcesi', 'diyagram.kalite', 'diyagram.paralellik',
    'diyagram.otomatik.genel', 'diyagram.otomatik.soy', 'diyagram.otomatik.batch'] },
  { id: 'yz', label: 'Yapay zekâ', paths: ['yz.etkin', 'yz.profil', 'yz.guven_esigi', 'yz.maske', 'yz.tablo_eslemesi'] },
  { id: 'aktar', label: 'Dışa/İçe aktar', projectOnly: true, paths: [] },
];
const SECTION = Object.fromEntries(SECTIONS.map((s) => [s.id, s]));

const RULE_TYPES = [
  { value: 'onek', label: 'önek (ad bununla başlar)' },
  { value: 'sonek', label: 'sonek (ad bununla biter)' },
  { value: 'tam', label: 'tam ad' },
  { value: 'regex', label: 'düzenli ifade' },
];
const RULE_SHORT = { onek: 'önek', sonek: 'sonek', tam: 'tam ad', regex: 'düzenli ifade' };
const QUALITIES = [{ value: 'showcase', label: 'Vitrin (showcase)' }, { value: 'standard', label: 'Standart' }];
const DIAGRAM_RANGES = [
  ['diyagram.butce', 'Diyagram başına en çok tablo', 8, 40],
  ['diyagram.job_butcesi', 'Batch diyagramında en çok job', 8, 60],
  ['diyagram.paralellik', 'Paralel üretim', 1, 8],
];
const PROFILE_DEFAULTS = {
  base_url: 'http://localhost:11434/v1', model: 'qwen3:8b', api_key_env: 'DWHFLOW_AI_KEY', timeout_s: 180,
  temperature: 0, allow_external_api: false, max_chars_per_call: 3500,
};
const DOC_FIELDS = { datasets: 'tablo', processes: 'süreç', flows: 'akış', status: 'durum', leftovers: 'çözülemeyen paragraf' };
const NODE_FIELDS = { name: 'ad', layer: 'katman', kind: 'tür', system: 'sistem', status: 'durum', description: 'açıklama', columns: 'kolonlar' };
const PREVIEW_LIMIT = 300;

// ---- yardımcılar ------------------------------------------------------- //

const clone = (value) => (value === undefined ? undefined : JSON.parse(JSON.stringify(value)));

/** Anahtar sırasından bağımsız karşılaştırma dizgesi (diziler sıralı kalır). */
function stable(value) {
  if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stable(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value === undefined ? null : value);
}
const same = (a, b) => stable(a) === stable(b);

function getPath(object, path) {
  return path.split('.').reduce((node, key) => (node == null ? undefined : node[key]), object);
}

function setPath(object, path, value) {
  const keys = path.split('.');
  let node = object;
  for (const key of keys.slice(0, -1)) {
    if (!node[key] || typeof node[key] !== 'object' || Array.isArray(node[key])) node[key] = {};
    node = node[key];
  }
  node[keys[keys.length - 1]] = value;
}

const inRange = (value, min, max) => typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max;

function kindLabel(value) {
  const dataset = label.datasetKind(value);
  return dataset !== value ? dataset : label.processKind(value);
}

function display(value) {
  if (value === null || value === undefined) return '— (yok)';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function chip(text, onRemove, kind = '') {
  return h('span', { class: ['set-chip', kind, onRemove ? '' : 'plain'] }, h('span', { text }),
    onRemove ? h('button', { type: 'button', title: `"${text}" kaldır`, 'aria-label': `"${text}" kaldır`, onclick: onRemove }, '×') : null);
}

function iconButton(text, title, disabled, onclick) {
  return h('button', { type: 'button', class: 'icon-btn', title, 'aria-label': title, disabled, onclick }, text);
}

function simpleTable(headers, rows, className = '') {
  return h('div', { class: 'card flush' }, h('div', { class: 'table-wrap' }, h('table', { class: `data ${className}` },
    h('thead', {}, h('tr', {}, headers.map((text) => h('th', { text })))),
    h('tbody', {}, rows.map((cells) => h('tr', {}, cells.map((cell) => h('td', { class: 'wrap' }, cell ?? '—'))))))));
}

function limited(list) {
  const rows = (list || []).slice(0, PREVIEW_LIMIT);
  const more = (list || []).length - rows.length;
  return { rows, more: more > 0 ? h('p', { class: 'small muted', text: `… ve ${more} tane daha` }) : null };
}

// ---- etki önizlemesi (settings/preview) ---------------------------------- //

function nodeChangeValue(change) {
  if (change.field === 'columns') {
    const removed = change.old || [];
    const added = change.new || [];
    return [removed.length ? `− ${removed.join(', ')}` : '', added.length ? `+ ${added.join(', ')}` : ''].filter(Boolean).join('  ');
  }
  const format = (v) => {
    if (v === null || v === undefined || v === '') return '—';
    if (change.field === 'layer') return label.layer(v);
    if (change.field === 'kind') return kindLabel(v);
    if (change.field === 'status') return label.status(v);
    return String(v);
  };
  return `${format(change.old)} → ${format(change.new)}`;
}

function linkText(row) {
  const processes = (row.processes || []).length ? ` (${row.processes.join(', ')})` : '';
  return `${row.source_name} → ${row.target_name}${processes}${row.draft ? ' · taslak' : ''}`;
}

function previewBody(result) {
  const model = result.model || {};
  const documents = result.documents || [];
  const before = result.before_unrecognized;
  const after = result.unrecognized_tables;
  if (!documents.length && model.empty && before === after) {
    return h('p', { text: 'Bu değişiklik okuma sonucunu değiştirmiyor.' });
  }
  const parts = [h('p', { class: 'small muted', text: 'Dokümanlar yeni ayarla deneme amaçlı okundu; henüz hiçbir şey kaydedilmedi.' })];
  if (before !== after) parts.push(h('p', {}, 'Tanınmayan tablo sayısı: ', h('b', { text: `${before} → ${after}` })));
  if (documents.length) {
    const { rows, more } = limited(documents);
    parts.push(h('h3', { text: `Etkilenen dokümanlar (${documents.length})` }), simpleTable(['Doküman', 'Değişen'],
      rows.map((doc) => [doc.file, Object.entries(doc.changes || {}).map(([key, pair]) => {
        const [old, now] = Array.isArray(pair) ? pair : [undefined, pair];
        return `${DOC_FIELDS[key] || key}: ${old ?? '—'} → ${now ?? '—'}`;
      }).join(' · ')])), more);
  }
  const nodeRow = (node) => [node.name, layerPill(node.layer), kindLabel(node.kind)];
  for (const [key, title] of [['nodes_added', 'Eklenecek nesneler'], ['nodes_removed', 'Kalkacak nesneler']]) {
    if (!(model[key] || []).length) continue;
    const { rows, more } = limited(model[key]);
    parts.push(h('h3', { text: `${title} (${model[key].length})` }), simpleTable(['Ad', 'Katman', 'Tür'], rows.map(nodeRow)), more);
  }
  if ((model.nodes_changed || []).length) {
    const { rows, more } = limited(model.nodes_changed);
    parts.push(h('h3', { text: `Değişecek nesne alanları (${model.nodes_changed.length})` }), simpleTable(['Nesne', 'Alan', 'Eski → yeni'],
      rows.map((change) => [change.name, NODE_FIELDS[change.field] || change.field, nodeChangeValue(change)])), more);
  }
  for (const [key, title] of [['links_added', 'Eklenecek bağlar'], ['links_removed', 'Kalkacak bağlar'], ['links_changed', 'Değişecek bağlar']]) {
    if (!(model[key] || []).length) continue;
    const { rows, more } = limited(model[key]);
    parts.push(h('h3', { text: `${title} (${model[key].length})` }), h('ul', { class: 'list' }, rows.map((row) => h('li', { text: linkText(row) }))), more);
  }
  if (model.edges_added || model.edges_removed) {
    parts.push(h('p', { class: 'small muted', text: `Kenar düzeyinde (kolon eşlemeleri dahil): +${model.edges_added || 0} / −${model.edges_removed || 0}` }));
  }
  return h('div', { class: 'stack' }, parts);
}

// ---- düzenleyici --------------------------------------------------------- //

/**
 * Ayar düzenleyicisini ``root``'a kurar.
 * scope: 'project' (slug gerekir) ya da 'global'. tab: açılışta seçili sekme (?sekme=…).
 * Dönüş: {dispose()} — ekrandan çıkarken çağrılır.
 */
export async function mountSettingsEditor(root, { scope, slug = null, tab = null }) {
  const isProject = scope === 'project';
  const level = isProject ? 'proje' : 'genel';
  const editable = isProject ? canEdit() : isAdmin();
  const url = isProject ? p`/api/projects/${slug}/settings` : '/api/settings';
  const [payload, formatsPayload, projects] = await Promise.all([
    get(url),
    get('/api/formats').catch(() => ({ formats: [] })),
    isProject ? null : get('/api/projects').catch(() => []),
  ]);
  const testProject = isProject ? { slug, ad: null } : ((projects || [])[0] || null);
  const tabIds = SECTIONS.filter((s) => isProject || !s.projectOnly).map((s) => s.id);

  let data = payload;
  let draft = clone(payload.values);
  let active = tabIds.includes(tab) ? tab : tabIds[0];
  let busy = false;
  let alive = true;
  let ownsDirty = false;
  let forcedRows = null; // "Okuyucular" sekmesi açıkken zorlanan okuyucu satırları
  let importState = { text: '', previewText: null, changes: null };
  let testSeq = 0;
  const dirty = new Set();

  const pathsOf = (id) => (id === 'yz' && !isProject ? [...SECTION.yz.paths, 'yz.profiller'] : SECTION[id].paths);
  const allPaths = () => tabIds.flatMap(pathsOf);
  const setHere = (path) => data.origins[path] === level;
  const resetWhere = isProject
    ? 'Bu projedeki değer silinir; genel ayar ya da paket varsayılanı geçerli olur.'
    : 'Genel ayardaki değer silinir; paket varsayılanı geçerli olur.';

  // -- kirli (kaydedilmemiş) durum -- //

  function sectionDirty(id) {
    if (id === 'aktar') return Boolean(importState.text.trim());
    return pathsOf(id).some((path) => !same(getPath(draft, path), getPath(data.values, path)));
  }

  function computeDirty() {
    dirty.clear();
    for (const id of tabIds) if (sectionDirty(id)) dirty.add(id);
    if (!alive) return;
    ownsDirty = dirty.size > 0;
    setDirty(ownsDirty ? 'Kaydedilmemiş ayar değişiklikleri var' : null);
  }

  function updateDirty() {
    computeDirty();
    drawTabs();
    drawFoot();
  }

  function buildPatch(id) {
    const patch = {};
    for (const path of pathsOf(id)) {
      const value = getPath(draft, path);
      if (!same(value, getPath(data.values, path))) setPath(patch, path, clone(value));
    }
    return patch;
  }

  /** Sunucudan gelen yeni durumu uygular. ``forcePaths`` taslağı her durumda yenilenir; kirli olmayan
   *  sekmelerin taslağı da yenilenir, kaydedilmemiş başka sekmelere dokunulmaz. */
  function applyPayload(next, forcePaths) {
    const force = new Set(forcePaths);
    const wasDirty = new Set(dirty);
    data = { values: next.values, origins: next.origins, global: next.global, project: next.project };
    for (const id of tabIds) {
      for (const path of pathsOf(id)) {
        if (force.has(path) || !wasDirty.has(id)) setPath(draft, path, clone(getPath(data.values, path)));
      }
    }
    computeDirty();
    drawTabs();
    drawBody();
  }

  // -- kaydetme -- //

  function validate(id) {
    if (id === 'ad') {
      const rules = draft.okuma.ad_kurallari || [];
      for (const [index, rule] of rules.entries()) {
        if (!String(rule.deger || '').trim()) return `${index + 1}. kuralın değeri boş`;
        if (rule.deger.length > 200) return `${index + 1}. kuralın değeri en çok 200 karakter olabilir`;
        if (!rule.katman) return `${index + 1}. kural için katman seçin`;
      }
    }
    if (id === 'okuyucu' && forcedRows) {
      const seen = new Set();
      for (const row of forcedRows) {
        const name = row.name.trim();
        if (!name && !row.reader) continue;
        if (!name) return 'Okuyucu seçilen bir satırda dosya adı boş';
        if (!row.reader) return `"${name}" için okuyucu seçin`;
        if (seen.has(name)) return `"${name}" dosya adı birden çok kez yazılmış`;
        seen.add(name);
      }
    }
    if (id === 'diyagram') {
      for (const [path, text, min, max] of DIAGRAM_RANGES) {
        const value = getPath(draft, path);
        if (!Number.isInteger(value) || value < min || value > max) return `${text} ${min}–${max} arasında bir tam sayı olmalı`;
      }
    }
    if (id === 'yz') {
      if (!inRange(draft.yz.guven_esigi, 0, 1)) return 'Güven eşiği 0 ile 1 arasında olmalı';
      if (!Object.prototype.hasOwnProperty.call(draft.yz.profiller || {}, draft.yz.profil)) return `"${draft.yz.profil}" adında bir LLM profili yok`;
    }
    return null;
  }

  async function submit(patch, forcePaths, message) {
    if (busy) { toast('Önceki kayıt sürüyor; biraz sonra yeniden deneyin.', 'warn'); return false; }
    busy = true;
    try {
      const response = await put(url, { patch });
      if (response.job) trackJob(response.job, { title: 'Okuma ayarları değişti: dokümanlar yeniden okunuyor' });
      if (alive) applyPayload(response, forcePaths);
      toast(message, 'ok');
      return true;
    } catch (error) {
      toastError(error);
      return false;
    } finally {
      busy = false;
    }
  }

  async function save(id) {
    const problem = validate(id);
    if (problem) { toast(problem, 'warn'); return; }
    const patch = buildPatch(id);
    if (!Object.keys(patch).length) { toast('Kaydedilecek değişiklik yok'); return; }
    await submit(patch, pathsOf(id), `${SECTION[id].label}: kaydedildi`);
  }

  function discard(id) {
    for (const path of pathsOf(id)) setPath(draft, path, clone(getPath(data.values, path)));
    computeDirty();
    drawTabs();
    drawBody();
  }

  async function resetPaths(paths, sectionLabel) {
    const targets = paths.filter(setHere);
    if (!targets.length) return;
    const question = sectionLabel
      ? `"${sectionLabel}" bölümü varsayılana dönsün mü? ${resetWhere} Bu bölümdeki kaydedilmemiş değişiklikler de atılır.`
      : `"${targets[0]}" varsayılana dönsün mü? ${resetWhere}`;
    if (!await confirmDialog(question, { title: 'Varsayılana dön', confirmLabel: 'Varsayılana dön' })) return;
    const patch = {};
    for (const path of targets) setPath(patch, path, null);
    await submit(patch, sectionLabel ? paths : targets, 'Varsayılana dönüldü');
  }

  async function previewEffect(id, trigger) {
    const problem = validate(id);
    if (problem) { toast(problem, 'warn'); return; }
    const patch = buildPatch(id);
    if (!Object.keys(patch).length) { toast('Önizlenecek değişiklik yok'); return; }
    const text = trigger.textContent;
    trigger.disabled = true;
    trigger.textContent = 'Dokümanlar okunuyor…';
    let result;
    try {
      result = await post(p`/api/projects/${slug}/settings/preview`, { patch });
    } catch (error) {
      toastError(error);
      return;
    } finally {
      trigger.disabled = false;
      trigger.textContent = text;
    }
    if (!alive) return;
    const choice = await modal({
      title: `Etki önizlemesi — ${SECTION[id].label}`, wide: true, body: previewBody(result),
      actions: [{ label: 'Kapat', value: null }, { label: 'Kaydet', kind: 'primary', value: 'save' }],
    });
    if (choice === 'save' && alive) await save(id);
  }

  // -- ortak alan parçaları -- //

  function origin(path) {
    const value = data.origins[path];
    return value ? originBadge(value) : null;
  }

  function resetButton(path) {
    if (!editable || !setHere(path)) return null;
    return h('button', { type: 'button', class: 'icon-btn set-reset', title: `Varsayılana dön: ${resetWhere}`,
      'aria-label': `${path} varsayılana dönsün`, onclick: () => resetPaths([path], null) }, '↺');
  }

  function sectionHead(title, paths = []) {
    return h('div', { class: 'card-head' }, h('h2', { text: title }), paths.map((path) => [origin(path), resetButton(path)]));
  }

  function subHead(title, path) {
    return h('div', { class: 'set-sub' }, h('h3', { text: title }), path ? [origin(path), resetButton(path)] : null);
  }

  function select(opts, value, onChange, ariaLabel) {
    const current = value ?? '';
    const list = opts.some((o) => o.value === current) ? opts : [...opts, { value: current, label: `${current} (tanınmayan)` }];
    return h('select', { disabled: !editable, 'aria-label': ariaLabel, onchange: (event) => { onChange(event.target.value); updateDirty(); } },
      list.map((o) => h('option', { value: o.value, selected: o.value === current }, o.label)));
  }

  function fieldWrap(path, text, control, hint) {
    return h('div', { class: 'field' },
      h('div', { class: 'set-field-head' }, h('span', { class: 'label', text }), origin(path), resetButton(path)),
      control, hint ? h('span', { class: 'hint', text: hint }) : null);
  }

  function numberField(path, text, min, max, step, hint) {
    const value = getPath(draft, path);
    const input = h('input', { type: 'number', min: String(min), max: String(max), step: String(step), disabled: !editable, 'aria-label': text });
    input.value = value == null ? '' : String(value);
    input.addEventListener('input', () => { setPath(draft, path, input.value === '' ? null : Number(input.value)); updateDirty(); });
    return fieldWrap(path, `${text} (${min}–${max})`, input, hint);
  }

  function selectField(path, text, opts, hint) {
    return fieldWrap(path, text, select(opts, getPath(draft, path), (value) => setPath(draft, path, value), text), hint);
  }

  function checkField(path, text, hint) {
    const input = h('input', { type: 'checkbox', checked: Boolean(getPath(draft, path)), disabled: !editable,
      onchange: (event) => { setPath(draft, path, event.target.checked); updateDirty(); } });
    return h('div', { class: 'set-check' }, h('label', {}, input, h('span', { text })), origin(path), resetButton(path),
      hint ? h('span', { class: 'hint', text: hint }) : null);
  }

  /** Düzenlenebilir etiket listesi. getItems/setItems taslağı okur/yazar; check(value) hata metni döner. */
  function chipEditor({ labelText, getItems, setItems, check, placeholder, ariaLabel }) {
    const list = h('div', { class: 'set-chips' });
    const draw = () => {
      clear(list);
      if (labelText) list.append(h('span', { class: 'set-chip-label', text: labelText }));
      const items = getItems();
      if (!items.length) list.append(h('span', { class: 'small muted', text: '—' }));
      items.forEach((item, index) => list.append(chip(item, editable ? () => {
        setItems(items.filter((_, i) => i !== index));
        updateDirty();
        draw();
      } : null)));
    };
    draw();
    if (!editable) return list;
    const input = h('input', { type: 'text', placeholder, 'aria-label': ariaLabel, maxlength: '200', autocomplete: 'off' });
    const add = () => {
      const value = input.value.trim();
      if (!value) { input.focus(); return; }
      const problem = check ? check(value, getItems()) : null;
      if (problem) { toast(problem, 'warn'); input.focus(); return; }
      setItems([...getItems(), value]);
      updateDirty();
      draw();
      input.value = '';
      input.focus();
    };
    input.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); add(); } });
    return h('div', { class: 'stack', style: { gap: '6px' } }, list, h('div', { class: 'set-add' }, input, button('Ekle', add, { kind: 'small' })));
  }

  // -- sekme: okuma kuralları (sütun başlığı eş anlamlıları) -- //

  function renderSynonyms() {
    const roles = (store.meta && store.meta.column_roles) || [];
    const mapping = () => draft.okuma.es_anlamlilar;
    const check = (role) => (value, items) => {
      const key = fold(value);
      if (items.some((s) => fold(s) === key)) return 'Bu eş anlamlı zaten ekli';
      for (const other of roles) {
        if ((other.builtin || []).some((s) => fold(s) === key)) {
          return other.value === role.value ? 'Bu ifade zaten yerleşik listede' : `"${value}" yerleşik olarak "${other.label}" rolünde tanımlı`;
        }
        if (other.value !== role.value && (mapping()[other.value] || []).some((s) => fold(s) === key)) {
          return `"${value}" zaten "${other.label}" rolüne eklenmiş`;
        }
      }
      return null;
    };
    return [
      sectionHead('Sütun başlığı eş anlamlıları', ['okuma.es_anlamlilar']),
      h('p', { class: 'set-intro', text: 'STTM, Excel ve Word tablolarındaki sütun başlıkları bu rollerle tanınır. Yerleşik eş anlamlılar '
        + 'her zaman geçerlidir; buraya eklenenler onlara eklenir. Karşılaştırma büyük/küçük harf ve Türkçe karakter duyarsızdır '
        + '(ör. "Hdf Tbl" → hedef tablo).' }),
      roles.length
        ? h('div', {}, roles.map((role) => h('div', { class: 'set-role' },
          h('div', {}, h('b', { text: role.label }), h('div', { class: 'small muted mono', text: role.value })),
          h('div', { class: 'stack', style: { gap: '6px' } },
            h('div', { class: 'set-chips' }, h('span', { class: 'set-chip-label', text: 'Yerleşik' }),
              (role.builtin || []).map((s) => chip(s, null, 'builtin'))),
            chipEditor({
              labelText: 'Özel', placeholder: 'ör. Hdf Tbl', ariaLabel: `${role.label} için eş anlamlı`,
              getItems: () => mapping()[role.value] || [],
              setItems: (list) => { if (list.length) mapping()[role.value] = list; else delete mapping()[role.value]; },
              check: check(role),
            })))))
        : empty('Sütun rolleri yüklenemedi.'),
    ];
  }

  // -- sekme: adlandırma ve katman -- //

  function nameTestResult(result) {
    const rule = result.rule;
    return h('dl', { class: 'kv' },
      h('dt', { text: 'Katman' }), h('dd', {}, result.layer ? layerPill(result.layer) : h('span', { class: 'muted', text: 'addan belirlenemedi' })),
      h('dt', { text: 'Nesne türü' }), h('dd', { text: result.kind ? label.datasetKind(result.kind) : '—' }),
      h('dt', { text: 'Neden' }), h('dd', { text: result.reason || '—' }),
      h('dt', { text: 'Eşleşen kural' }), h('dd', {}, rule
        ? [badge(rule.origin === 'ayar' ? 'özel kural' : 'yerleşik kural', rule.origin === 'ayar' ? 'info' : 'muted'), ' ',
          `${RULE_SHORT[rule.type] || rule.type} `, h('code', { text: rule.value })]
        : '—'));
  }

  function renderNaming() {
    const layers = options('layers');
    const kinds = options('dataset_kinds');
    const defaultLayer = layers.some((l) => l.value === 'staging') ? 'staging' : (layers[0] ? layers[0].value : '');
    const rulesWrap = h('div');

    const moveRule = (index, delta) => {
      const rules = draft.okuma.ad_kurallari;
      const target = index + delta;
      if (target < 0 || target >= rules.length) return;
      [rules[index], rules[target]] = [rules[target], rules[index]];
      updateDirty();
      drawRules();
    };

    const ruleRow = (rule, index, count) => {
      const valueInput = h('input', { type: 'text', value: rule.deger, disabled: !editable, maxlength: '200',
        placeholder: 'ör. STG_', 'aria-label': `${index + 1}. kuralın değeri` });
      valueInput.addEventListener('input', () => { rule.deger = valueInput.value; updateDirty(); });
      valueInput.addEventListener('change', () => {
        if (rule.tur === 'regex') return; // düzenli ifadede boşluk anlamlı olabilir
        const trimmed = valueInput.value.trim();
        if (trimmed !== valueInput.value) { valueInput.value = trimmed; rule.deger = trimmed; updateDirty(); }
      });
      return h('tr', {},
        h('td', { class: 'num', text: String(index + 1) }),
        h('td', {}, select(RULE_TYPES, rule.tur, (value) => { rule.tur = value; }, `${index + 1}. kuralın türü`)),
        h('td', {}, valueInput),
        h('td', {}, select(layers, rule.katman, (value) => { rule.katman = value; }, `${index + 1}. kuralın katmanı`)),
        h('td', {}, select([{ value: '', label: '— katmana göre —' }, ...kinds], rule.nesne_turu || '',
          (value) => { rule.nesne_turu = value || null; }, `${index + 1}. kuralın nesne türü`)),
        h('td', { class: 'nowrap' }, editable ? [
          iconButton('↑', 'Yukarı taşı', index === 0, () => moveRule(index, -1)),
          iconButton('↓', 'Aşağı taşı', index === count - 1, () => moveRule(index, 1)),
          iconButton('✕', 'Kuralı sil', false, () => { draft.okuma.ad_kurallari.splice(index, 1); updateDirty(); drawRules(); }),
        ] : null));
    };

    function drawRules() {
      clear(rulesWrap);
      const rules = draft.okuma.ad_kurallari;
      rulesWrap.append(rules.length
        ? h('div', { class: 'card flush' }, h('div', { class: 'table-wrap' }, h('table', { class: 'data set-table' },
          h('thead', {}, h('tr', {}, ['#', 'Tür', 'Değer', 'Katman', 'Nesne türü', ''].map((text) => h('th', { text })))),
          h('tbody', {}, rules.map((rule, index) => ruleRow(rule, index, rules.length))))))
        : h('p', { class: 'small muted', text: 'Özel kural yok; yalnız yerleşik kurallar uygulanır.' }));
      if (editable) {
        rulesWrap.append(h('div', { class: 'row', style: { marginTop: '8px' } }, button('+ Kural ekle', () => {
          draft.okuma.ad_kurallari.push({ tur: 'onek', deger: '', katman: defaultLayer, nesne_turu: null });
          updateDirty();
          drawRules();
          const inputs = rulesWrap.querySelectorAll('input[type=text]');
          if (inputs.length) inputs[inputs.length - 1].focus();
        }, { kind: 'small' })));
      }
    }
    drawRules();

    const defaults = (store.meta && store.meta.default_rules) || [];
    const builtin = h('details', { class: 'set-details' },
      h('summary', { text: `Yerleşik kurallar (${defaults.length}) — özel kurallardan sonra denenir` }),
      defaults.length
        ? simpleTable(['Tür', 'Değer', 'Katman', 'Nesne türü'], defaults.map((r) => [RULE_SHORT[r.type] || r.type,
          h('code', { text: r.value }), layerPill(r.layer), r.kind ? label.datasetKind(r.kind) : '—']))
        : h('p', { class: 'small muted', text: 'Yerleşik kurallar yüklenemedi.' }));

    // Ad testi: kaydedilmemiş kurallarla dener.
    const testInput = h('input', { type: 'text', placeholder: 'ör. DWH.STG_MUSTERI_HIST', 'aria-label': 'Denenecek ad', maxlength: '300' });
    const testResult = h('div', { class: 'set-result' });
    const runTest = async () => {
      const name = testInput.value.trim();
      if (!name) { toast('Denemek için bir ad yazın', 'warn'); testInput.focus(); return; }
      const mine = ++testSeq;
      clear(testResult);
      testResult.append(h('span', { class: 'muted' }, h('span', { class: 'spinner' }), ' Deneniyor…'));
      const rules = (draft.okuma.ad_kurallari || []).filter((r) => String(r.deger || '').trim()).map((r) => ({
        tur: r.tur, deger: r.tur === 'regex' ? r.deger : r.deger.trim(), katman: r.katman, nesne_turu: r.nesne_turu || null }));
      try {
        const result = await post(p`/api/projects/${testProject.slug}/settings/name-test`, {
          name, okuma: { ad_kurallari: rules, varsayilan_ad_kurallari: Boolean(draft.okuma.varsayilan_ad_kurallari) } });
        if (mine !== testSeq) return;
        clear(testResult);
        testResult.append(nameTestResult(result));
      } catch (error) {
        if (mine !== testSeq) return;
        clear(testResult);
        testResult.append(h('div', { class: 'banner bad' }, h('span', { class: 'grow', text: error.detail || error.message })));
      }
    };
    testInput.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); runTest(); } });
    const testBox = testProject
      ? h('div', { class: 'set-test' },
        h('div', { class: 'set-add', style: { maxWidth: '640px' } }, testInput, button('Dene', runTest, { kind: 'small' })),
        h('div', { class: 'small muted', text: isProject
          ? 'Bu sekmedeki (kaydedilmemiş olanlar dahil) kurallarla denenir; dosya uzantısı ve dosya sözcükleri de dikkate alınır.'
          : `Deneme "${testProject.ad || testProject.slug}" projesi üzerinden, bu sekmedeki (kaydedilmemiş olanlar dahil) genel kurallarla yapılır.` }),
        testResult)
      : h('p', { class: 'small muted', text: 'Ad testi için en az bir proje gerekir.' });

    return [
      sectionHead('Ad → katman ve tür kuralları'),
      h('p', { class: 'set-intro', text: 'Bir tablo/dosya adından katman ve nesne türü tahmin edilir. Özel kurallar yerleşik kurallardan '
        + 'önce, yukarıdan aşağı denenir; ilk eşleşen kural kazanır. Karşılaştırma büyük/küçük harf duyarsızdır ve şema öneki '
        + '(ör. DWH.) atılarak yapılır.' }),
      subHead('Özel kurallar', 'okuma.ad_kurallari'),
      rulesWrap,
      h('div', { style: { marginTop: '14px' } }, checkField('okuma.varsayilan_ad_kurallari', 'Yerleşik kuralları da uygula',
        'Kapatılırsa yalnız özel kurallar kullanılır.')),
      builtin,
      subHead('Ad testi'),
      testBox,
    ];
  }

  // -- sekme: sistemler -- //

  function renderSystems() {
    const map = () => draft.okuma.sistemler;
    const listWrap = h('div', { class: 'stack' });

    const nameProblem = (name, except) => {
      const key = fold(name);
      for (const [system, aliases] of Object.entries(map())) {
        if (system !== except && fold(system) === key) return 'Bu adla bir sistem zaten var';
        if (aliases.some((a) => fold(a) === key)) {
          return system === except ? 'Kanonik ad kendi eş anlamlılarından biri olamaz' : `"${name}" zaten "${system}" sisteminin eş anlamlısı`;
        }
      }
      return null;
    };
    const aliasProblem = (owner) => (value) => {
      const key = fold(value);
      for (const [system, aliases] of Object.entries(map())) {
        if (fold(system) === key) return system === owner ? 'Kanonik ad eş anlamlı olarak eklenemez' : `"${value}" zaten bir sistem adı`;
        if (aliases.some((a) => fold(a) === key)) {
          return system === owner ? 'Bu eş anlamlı zaten ekli' : `"${value}" zaten "${system}" sisteminin eş anlamlısı`;
        }
      }
      return null;
    };

    const rename = async (old) => {
      const values = await formDialog({
        title: 'Sistemi yeniden adlandır',
        fields: [{ name: 'ad', label: 'Kanonik ad', value: old, required: true }],
        validate: (v) => (v.ad.trim() === old ? null : nameProblem(v.ad.trim(), old)),
      });
      if (!values || !alive) return;
      const next = values.ad.trim();
      if (!next || next === old) return;
      const rebuilt = {};
      for (const [key, value] of Object.entries(map())) rebuilt[key === old ? next : key] = value;
      draft.okuma.sistemler = rebuilt;
      updateDirty();
      drawList();
    };

    function drawList() {
      clear(listWrap);
      const entries = Object.keys(map());
      if (!entries.length) listWrap.append(h('p', { class: 'small muted', text: 'Tanımlı sistem yok.' }));
      for (const name of entries) {
        listWrap.append(h('div', { class: 'set-system' },
          h('div', { class: 'row' }, h('b', { class: 'grow', text: name }),
            editable ? [button('Yeniden adlandır', () => rename(name), { kind: 'small' }),
              button('Sil', () => { delete map()[name]; updateDirty(); drawList(); }, { kind: 'small danger' })] : null),
          chipEditor({
            labelText: 'Eş anlamlılar', placeholder: 'ör. CB', ariaLabel: `${name} için eş anlamlı`,
            getItems: () => map()[name] || [],
            setItems: (list) => { map()[name] = list; },
            check: aliasProblem(name),
          })));
      }
    }
    drawList();

    const nameInput = h('input', { type: 'text', placeholder: 'Kanonik sistem adı (ör. Ana Bankacılık)', 'aria-label': 'Yeni sistem adı',
      maxlength: '200', autocomplete: 'off' });
    const addSystem = () => {
      const name = nameInput.value.trim();
      if (!name) { nameInput.focus(); return; }
      const problem = nameProblem(name, null);
      if (problem) { toast(problem, 'warn'); return; }
      map()[name] = [];
      updateDirty();
      drawList();
      nameInput.value = '';
      nameInput.focus();
    };
    nameInput.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); addSystem(); } });

    return [
      sectionHead('Kaynak sistem sözlüğü', ['okuma.sistemler']),
      h('p', { class: 'set-intro', text: 'Aynı sistemin farklı yazımları okuma sırasında tek ada bağlanır (ör. "CB", "Core Banking" → '
        + '"Ana Bankacılık").' }),
      listWrap,
      editable ? h('div', { class: 'set-add', style: { marginTop: '12px' } }, nameInput, button('Sistem ekle', addSystem, { kind: 'small' })) : null,
    ];
  }

  // -- sekme: okuyucular ve şablonlar -- //

  function renderReaders() {
    const formats = (formatsPayload.formats || []).map((f) => ({ value: f.key, label: `${f.label} (${f.key})` }));
    forcedRows = Object.entries(draft.okuma.okuyucu_zorla || {}).map(([name, reader]) => ({ name, reader }));
    const forcedWrap = h('div');
    const templatesWrap = h('div');

    const syncForced = () => {
      const mapping = {};
      for (const row of forcedRows) {
        const name = row.name.trim();
        if (name && row.reader) mapping[name] = row.reader;
      }
      draft.okuma.okuyucu_zorla = mapping;
      updateDirty();
    };

    function drawForced() {
      clear(forcedWrap);
      forcedWrap.append(forcedRows.length
        ? h('div', { class: 'card flush' }, h('div', { class: 'table-wrap' }, h('table', { class: 'data set-table' },
          h('thead', {}, h('tr', {}, ['Dosya adı', 'Okuyucu', ''].map((text) => h('th', { text })))),
          h('tbody', {}, forcedRows.map((row, index) => {
            const input = h('input', { type: 'text', value: row.name, disabled: !editable, maxlength: '255',
              placeholder: 'ör. gece_zinciri.txt', 'aria-label': 'Dosya adı' });
            input.addEventListener('input', () => { row.name = input.value; syncForced(); });
            return h('tr', {},
              h('td', {}, input),
              h('td', {}, select([{ value: '', label: '— okuyucu seçin —' }, ...formats], row.reader,
                (value) => { row.reader = value; syncForced(); }, 'Okuyucu')),
              h('td', { class: 'nowrap' }, editable
                ? iconButton('✕', 'Satırı sil', false, () => { forcedRows.splice(index, 1); syncForced(); drawForced(); })
                : null));
          })))))
        : h('p', { class: 'small muted', text: 'Zorlanan okuyucu yok; biçim dosya uzantısından ve içerikten anlaşılır.' }));
      if (editable) {
        forcedWrap.append(h('div', { class: 'row', style: { marginTop: '8px' } }, button('+ Satır ekle', () => {
          forcedRows.push({ name: '', reader: '' });
          drawForced();
          const inputs = forcedWrap.querySelectorAll('input[type=text]');
          if (inputs.length) inputs[inputs.length - 1].focus();
        }, { kind: 'small' })));
      }
    }

    const templateProblem = (v, index) => {
      const limits = [['ad', 'Ad', 80], ['dosya', 'Dosya deseni', 200], ['kayit', 'Kayıt yolu', 300], ['kaynak', 'Kaynak yolu', 300],
        ['hedef', 'Hedef yolu', 300], ['surec', 'Süreç yolu', 300], ['aciklama', 'Açıklama', 300]];
      for (const [key, text, max] of limits) if (String(v[key] || '').trim().length > max) return `${text} en çok ${max} karakter olabilir`;
      const name = fold(v.ad.trim());
      if ((draft.okuma.sablonlar || []).some((t, i) => i !== index && fold(t.ad) === name)) return 'Bu adla bir şablon zaten var';
      return null;
    };

    const editTemplate = async (index) => {
      const current = index == null
        ? { ad: '', dosya: '', tur: 'xml', kayit: '', kaynak: '', hedef: '', surec: null, aciklama: null }
        : draft.okuma.sablonlar[index];
      const values = await formDialog({
        title: index == null ? 'Yeni okuma şablonu' : `Şablon: ${current.ad}`, wide: true,
        intro: 'Kaynak, hedef ve süreç yolları kayıt öğesine göre yazılır; örnekler sekmedeki yardım kutusunda.',
        fields: [
          { name: 'ad', label: 'Ad', value: current.ad, required: true, placeholder: 'ör. Kurum ETL eşlemesi' },
          { name: 'dosya', label: 'Dosya deseni', value: current.dosya, required: true, placeholder: 'ör. *_mapping.xml', hint: 'Joker karakterler: * ve ?' },
          { name: 'tur', label: 'Tür', type: 'select', value: current.tur, options: [{ value: 'xml', label: 'XML' }, { value: 'json', label: 'JSON' }] },
          { name: 'kayit', label: 'Kayıt yolu', value: current.kayit, required: true, placeholder: 'XML: .//Mapping · JSON: mappings[*]' },
          { name: 'kaynak', label: 'Kaynak yolu', value: current.kaynak, required: true, placeholder: 'XML: Source/@name · JSON: source' },
          { name: 'hedef', label: 'Hedef yolu', value: current.hedef, required: true, placeholder: 'XML: Target/@name · JSON: target' },
          { name: 'surec', label: 'Süreç yolu (isteğe bağlı)', value: current.surec || '', placeholder: 'XML: @job · JSON: job' },
          { name: 'aciklama', label: 'Açıklama (isteğe bağlı)', type: 'textarea', value: current.aciklama || '', wide: true },
        ],
        validate: (v) => templateProblem(v, index),
      });
      if (!values || !alive) return;
      const template = {
        ad: values.ad.trim(), dosya: values.dosya.trim(), tur: values.tur, kayit: values.kayit.trim(),
        kaynak: values.kaynak.trim(), hedef: values.hedef.trim(),
        surec: values.surec.trim() || null, aciklama: values.aciklama.trim() || null,
      };
      if (index == null) draft.okuma.sablonlar.push(template);
      else draft.okuma.sablonlar[index] = template;
      updateDirty();
      drawTemplates();
    };

    function drawTemplates() {
      clear(templatesWrap);
      const list = draft.okuma.sablonlar || [];
      templatesWrap.append(list.length
        ? simpleTable(['Ad', 'Dosya deseni', 'Tür', 'Kayıt', 'Kaynak', 'Hedef', 'Süreç', ''], list.map((t, index) => [
          h('span', { title: t.aciklama || '', text: t.ad }), h('code', { text: t.dosya }), t.tur.toUpperCase(),
          h('code', { text: t.kayit }), h('code', { text: t.kaynak }), h('code', { text: t.hedef }), t.surec ? h('code', { text: t.surec }) : '—',
          editable ? h('span', { class: 'nowrap' }, iconButton('✎', 'Şablonu düzenle', false, () => editTemplate(index)),
            iconButton('✕', 'Şablonu sil', false, () => { draft.okuma.sablonlar.splice(index, 1); updateDirty(); drawTemplates(); })) : '',
        ]))
        : h('p', { class: 'small muted', text: 'Tanımlı şablon yok.' }));
      if (editable) {
        templatesWrap.append(h('div', { class: 'row', style: { marginTop: '8px' } }, button('+ Şablon ekle', () => editTemplate(null), { kind: 'small' })));
      }
    }

    drawForced();
    drawTemplates();
    return [
      sectionHead('Okuyucular ve şablonlar'),
      subHead('Okuyucu zorlama', 'okuma.okuyucu_zorla'),
      h('p', { class: 'set-intro', text: 'Belirli bir dosyayı, uzantısından bağımsız olarak seçilen okuyucuyla okutur (ör. .txt uzantılı '
        + 'bir JIL dosyası). Dosya adı tam olarak yazılır.' }),
      forcedWrap,
      subHead('Kurum okuma şablonları (XML/JSON)', 'okuma.sablonlar'),
      h('p', { class: 'set-intro', text: 'Kuruma özel XML ya da JSON eşleme dosyalarından kaynak → hedef akışını çıkarmak için yol tanımları.' }),
      templatesWrap,
      h('div', { class: 'set-help', style: { marginTop: '12px' } },
        h('p', {}, h('b', { text: 'Yol yazımı. ' }), 'Kayıt yolu dosyadaki her eşleme kaydını seçer; kaynak, hedef ve süreç yolları o kayda göre yazılır.'),
        h('p', {}, 'XML (XPath benzeri): ', h('code', { text: './/Mapping' }), ' belgedeki tüm Mapping öğeleri; ',
          h('code', { text: 'Source/@name' }), ' kaydın Source alt öğesinin name özniteliği; ', h('code', { text: 'Target' }),
          ' Target alt öğesinin metni.'),
        h('p', {}, 'JSON: ', h('code', { text: 'mappings[*]' }), ' mappings dizisinin her öğesi; ', h('code', { text: 'source.table' }),
          ' kayıttaki source nesnesinin table alanı.'),
        h('p', {}, 'Dosya deseni joker karakter kullanır: ', h('code', { text: '*_mapping.xml' }), ', ', h('code', { text: 'ETL_??.json' }), '.'),
        h('p', { class: 'muted', text: 'Şablon yalnız adı dosya desenine uyan .xml/.json dosyalarına uygulanır; Control-M, DataStage, draw.io '
          + 'gibi tanınan dosyaları almaz. Geçersiz bir şablon okumayı durdurmaz, nedeni dokümanın raporunda görünür. '
          + 'Örnek tanımlar ve dosyalar: src/dwhflow/examples/ornek_sablon (sablonlar.yaml).' })),
    ];
  }

  // -- sekme: diyagram -- //

  function renderDiagram() {
    return [
      sectionHead('Diyagram ayarları'),
      h('p', { class: 'set-intro', text: 'Değişiklikler bir sonraki diyagram üretiminde geçerli olur.' }),
      h('div', { class: 'form-grid' },
        numberField('diyagram.butce', 'Diyagram başına en çok tablo', 8, 40, 1, 'Daha büyük diyagramlar bu sınıra göre kırpılır.'),
        numberField('diyagram.job_butcesi', 'Batch diyagramında en çok job', 8, 60, 1),
        selectField('diyagram.kalite', 'Kalite', QUALITIES, 'Vitrin kalitesi geçemezse standart kaliteyle yeniden denenir.'),
        numberField('diyagram.paralellik', 'Paralel üretim', 1, 8, 1, 'Aynı anda üretilecek diyagram sayısı.')),
      h('h3', { text: 'Otomatik diyagramlar' }),
      h('div', { class: 'stack', style: { gap: '6px' } },
        checkField('diyagram.otomatik.genel', 'Genel akış diyagramı'),
        checkField('diyagram.otomatik.soy', 'Soy ağacı diyagramları'),
        checkField('diyagram.otomatik.batch', 'Batch (job zinciri) diyagramı')),
      h('p', { class: 'small muted', style: { marginTop: '14px' } }, 'Özel yollar, çevre diyagramları ve gizlenen nesneler ',
        isProject ? h('a', { href: link('p', slug, 'diyagramlar'), text: 'Diyagramlar' }) : 'her projenin Diyagramlar',
        ' ekranından yönetilir.'),
    ];
  }

  // -- sekme: yapay zekâ -- //

  function showProbe(name, result) {
    const found = result.model_found;
    modal({
      title: `Bağlantı testi: ${name}`,
      body: h('div', { class: 'stack' },
        h('div', {}, result.ok ? badge('bağlantı başarılı', 'ok') : badge('bağlantı kurulamadı', 'bad'),
          result.status ? ` HTTP ${result.status}` : ''),
        result.error ? h('p', { class: 'small', text: result.error }) : null,
        found === true ? h('p', { text: 'Yapılandırılan model sunucuda var.' }) : null,
        found === false ? h('p', { class: 'set-warn-text', text: result.hint || 'Yapılandırılan model sunucunun listesinde yok.' }) : null,
        found == null && result.ok ? h('p', { class: 'small muted', text: 'Sunucu model listesi döndürmedi.' }) : null,
        (result.models || []).length
          ? h('div', {}, h('div', { class: 'small muted', text: 'Sunucudaki modeller:' }),
            h('div', { class: 'set-chips' }, result.models.map((m) => chip(String(m), null))))
          : null),
    });
  }

  async function testProfile(name, trigger) {
    const saved = (data.values.yz.profiller || {})[name];
    if (!saved) { toast('Bu profil henüz kaydedilmedi; test kayıtlı ayarlarla yapılır.', 'warn'); return; }
    if (!same(saved, (draft.yz.profiller || {})[name])) {
      toast('Profilde kaydedilmemiş değişiklik var; test kayıtlı ayarlarla yapılır. Önce kaydedin.', 'warn');
      return;
    }
    trigger.disabled = true;
    try {
      const result = await post('/api/llm/test', isProject ? { profile: name, project: slug } : { profile: name });
      if (alive) showProbe(name, result);
    } catch (error) {
      if (error instanceof ApiError && (error.status === 405 || (error.status === 404 && error.detail === 'Not Found'))) {
        toast('LLM test ucu bu sürümde yok', 'warn');
      } else toastError(error);
    } finally {
      trigger.disabled = false;
    }
  }

  const profileProblem = (v, oldName) => {
    const name = v.ad.trim();
    if (!/^[\p{L}\p{N}_.-]{1,40}$/u.test(name)) return 'Profil adı 1–40 karakter olmalı; harf, rakam, _ . - kullanılabilir';
    if (name !== oldName && Object.prototype.hasOwnProperty.call(draft.yz.profiller || {}, name)) return 'Bu adla bir profil zaten var';
    if (!/^https?:\/\/\S+$/.test(v.base_url.trim())) return 'Sunucu adresi http ya da https şemasıyla başlamalı (ör. http://localhost:11434/v1)';
    if (!v.model.trim()) return 'Model adı gerekli';
    const env = v.api_key_env.trim();
    if (env && !/^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(env)) {
      return 'Ortam değişkeni ADI yalnız harf, rakam ve _ içerebilir (ör. DWHFLOW_AI_KEY); anahtarın kendisini yazmayın';
    }
    if (!inRange(v.timeout_s, 0, 1800) || v.timeout_s === 0) return 'Zaman aşımı 0’dan büyük ve en çok 1800 sn olmalı';
    if (!inRange(v.temperature, 0, 2)) return 'Sıcaklık 0 ile 2 arasında olmalı';
    if (!Number.isInteger(v.max_chars_per_call) || !inRange(v.max_chars_per_call, 500, 60000)) {
      return 'Çağrı başına karakter 500–60000 arasında bir tam sayı olmalı';
    }
    return null;
  };

  async function editProfile(name) {
    const profiles = draft.yz.profiller || {};
    const current = name == null ? { ...PROFILE_DEFAULTS } : profiles[name];
    const values = await formDialog({
      title: name == null ? 'Yeni LLM profili' : `LLM profili: ${name}`, wide: true,
      intro: 'Profil yalnız bağlantı bilgisini tutar; API anahtarının kendisi hiçbir ayar dosyasına yazılmaz. '
        + 'Profilin adını değiştirirseniz bu profili seçmiş projelerin ayarlarını da güncelleyin.',
      fields: [
        { name: 'ad', label: 'Profil adı', value: name || '', required: true, placeholder: 'ör. kurum-ici' },
        { name: 'base_url', label: 'Sunucu adresi', value: current.base_url, required: true, placeholder: 'http://localhost:11434/v1',
          hint: 'OpenAI uyumlu uç; http ya da https şemasıyla, ör. http://localhost:11434/v1' },
        { name: 'model', label: 'Model', value: current.model, required: true, placeholder: 'ör. qwen3:8b' },
        { name: 'api_key_env', label: 'API anahtarının ortam değişkeni ADI', value: current.api_key_env || '', placeholder: 'DWHFLOW_AI_KEY',
          hint: 'Anahtarın KENDİSİNİ değil, sunucuda anahtarı tutan ortam değişkeninin ADINI yazın. Anahtar gerekmiyorsa boş bırakın.' },
        { name: 'timeout_s', label: 'Zaman aşımı (sn)', type: 'number', value: current.timeout_s, hint: '0’dan büyük, en çok 1800' },
        { name: 'temperature', label: 'Sıcaklık (temperature)', type: 'number', value: current.temperature, hint: '0–2; tutarlı çıkarım için 0 önerilir' },
        { name: 'max_chars_per_call', label: 'Çağrı başına en çok karakter', type: 'number', value: current.max_chars_per_call, hint: '500–60000' },
        { name: 'allow_external_api', label: 'Dış (yerel olmayan) adrese izin ver', type: 'checkbox', value: current.allow_external_api, wide: true,
          hint: 'UYARI: açıkken doküman metni kurum dışındaki bu sunucuya gönderilir.' },
      ],
      validate: (v) => profileProblem(v, name),
    });
    if (!values || !alive) return;
    if (values.allow_external_api && !current.allow_external_api) {
      const ok = await confirmDialog('Dış adrese izin verirseniz doküman metinleri (kişisel veriler maskelenmiş olsa da) kurum dışındaki '
        + 'bu sunucuya gönderilir. Yalnız kurumunuzun veri politikası izin veriyorsa açın. Devam edilsin mi?',
      { title: 'Doküman metni kurum dışına çıkacak', confirmLabel: 'İzin ver', danger: true });
      if (!ok || !alive) return;
    }
    const newName = values.ad.trim();
    const profile = {
      base_url: values.base_url.trim().replace(/\/+$/, ''), model: values.model.trim(), api_key_env: values.api_key_env.trim() || null,
      timeout_s: values.timeout_s, temperature: values.temperature, allow_external_api: Boolean(values.allow_external_api),
      max_chars_per_call: values.max_chars_per_call,
    };
    const rebuilt = {};
    for (const [key, value] of Object.entries(draft.yz.profiller || {})) rebuilt[key === name ? newName : key] = key === name ? profile : value;
    if (name == null) rebuilt[newName] = profile;
    draft.yz.profiller = rebuilt;
    if (name != null && name !== newName && draft.yz.profil === name) draft.yz.profil = newName;
    computeDirty();
    drawTabs();
    drawBody();
  }

  async function deleteProfile(name) {
    if (name === draft.yz.profil) { toast('Seçili profil silinemez; önce başka bir profil seçin.', 'warn'); return; }
    if (!await confirmDialog(`"${name}" profili silinsin mi? Değişiklik "Kaydet" ile kalıcı olur. Bu profili seçmiş projeler varsa `
      + 'onların ayarları geçersiz olur.', { danger: true, confirmLabel: 'Sil' })) return;
    if (!alive) return;
    delete draft.yz.profiller[name];
    computeDirty();
    drawTabs();
    drawBody();
  }

  function renderLLM() {
    const profiles = draft.yz.profiller || {};
    const names = Object.keys(profiles);
    const manage = !isProject && editable;
    const canTest = canEdit();
    const profileTable = names.length
      ? simpleTable(['Profil', 'Sunucu adresi', 'Model', 'Anahtar değişkeni', 'Dış adres', ''], names.map((name) => {
        const profile = profiles[name];
        return [
          h('span', {}, h('b', { text: name }), name === draft.yz.profil ? [' ', badge('seçili', 'info')] : null),
          h('code', { text: profile.base_url }), profile.model, profile.api_key_env ? h('code', { text: profile.api_key_env }) : '—',
          profile.allow_external_api ? badge('izinli — metin dışarı çıkar', 'warn') : badge('kapalı', 'muted'),
          h('span', { class: 'nowrap' },
            canTest ? button('Bağlantıyı test et', (event) => testProfile(name, event.currentTarget), { kind: 'small' }) : null,
            manage ? iconButton('✎', 'Profili düzenle', false, () => editProfile(name)) : null,
            manage ? iconButton('✕', 'Profili sil', false, () => deleteProfile(name)) : null),
        ];
      }))
      : h('p', { class: 'small muted', text: 'Tanımlı profil yok.' });
    return [
      sectionHead('Yapay zekâ (LLM)'),
      h('p', { class: 'set-intro', text: 'LLM yalnız kuralların çözemediği paragraflar ve tanınmayan tablolar için kullanılır; '
        + 'yanıtı şema ve kanıt denetiminden geçer.' }),
      h('div', { class: 'stack', style: { gap: '8px' } },
        checkField('yz.etkin', 'Yapay zekâ (LLM) desteğini etkinleştir', 'Kapalıyken doküman metni hiçbir modele gönderilmez; okuma yalnız kurallarla yapılır.'),
        checkField('yz.maske', 'Kişisel verileri maskele', 'Metin LLM’e gönderilmeden önce kişisel veriler maskelenir. Dış adresli profillerde maskeleme her zaman uygulanır.'),
        checkField('yz.tablo_eslemesi', 'Tanınmayan tablo başlıkları için LLM önerisi', 'Kurallarla tanınamayan tablo başlıkları için sütun rolü önerilir.')),
      h('div', { class: 'form-grid', style: { marginTop: '12px' } },
        selectField('yz.profil', 'Kullanılacak profil', names.map((n) => ({ value: n, label: n })), 'Profil, LLM sunucusunun adresini ve modelini tanımlar.'),
        numberField('yz.guven_esigi', 'Güven eşiği', 0, 1, 0.05, 'Yalnız LLM’in tek başına bulduğu ve güveni bu eşiğin altındaki bağlar modele alınmaz (0: hepsi alınır).')),
      subHead('Bağlantı profilleri', 'yz.profiller'),
      isProject
        ? h('p', { class: 'small muted' }, 'LLM profillerini yalnız yöneticiler genel ayarlarda tanımlar.',
          isAdmin() ? [' ', h('a', { href: '#/ayarlar?sekme=yz', text: 'Genel ayarlar › Yapay zekâ' })] : null)
        : h('p', { class: 'small muted', text: '"Anahtar değişkeni", API anahtarını tutan ortam değişkeninin ADIDIR; anahtarın kendisi hiçbir yerde saklanmaz. '
          + 'Profil değişiklikleri bu sekmedeki "Kaydet" ile kalıcı olur; bağlantı testi kayıtlı profille yapılır.' }),
      profileTable,
      manage ? h('div', { class: 'row', style: { marginTop: '8px' } }, button('+ Profil ekle', () => editProfile(null), { kind: 'small' })) : null,
    ];
  }

  // -- sekme: dışa/içe aktar (yalnız proje) -- //

  function renderTransfer() {
    const overridden = Object.keys(data.origins).filter((path) => data.origins[path] === 'proje');
    const textarea = h('textarea', { class: 'set-yaml', rows: '14', disabled: !editable, 'aria-label': 'Ayar profili (YAML)',
      placeholder: 'diyagram:\n  butce: 18\nokuma:\n  sistemler:\n    Ana Bankacılık: [CB, Core Banking]' });
    textarea.value = importState.text;
    const previewBox = h('div', { class: 'stack' });

    const drawPreview = () => {
      clear(previewBox);
      if (importState.changes == null || importState.previewText !== textarea.value) return;
      const changes = importState.changes;
      if (!changes.length) {
        previewBox.append(h('div', { class: 'banner info' }, h('span', { class: 'grow', text: 'Fark yok: dosya bu projenin ayar katmanıyla aynı.' })));
        return;
      }
      append(previewBox, [
        h('div', { class: 'banner warn' }, h('span', { class: 'grow', text: 'İçe aktarma bu projenin ayar katmanını bütünüyle değiştirir: dosyada '
          + 'olmayan proje ayarları silinir (genel ayar ya da varsayılan geçerli olur). Ardından dokümanlar yeniden okunur.' })),
        simpleTable(['Anahtar', 'Projede şu an', 'Dosyada'], changes.map((c) => [h('code', { text: c.path }),
          h('code', { text: display(c.old) }), h('code', { text: display(c.new) })]), 'set-diff'),
        editable ? h('div', { class: 'row' }, button('Uygula', (event) => applyImport(event.currentTarget), { kind: 'primary' })) : null,
      ]);
    };

    const onText = () => {
      importState.text = textarea.value;
      drawPreview();
      updateDirty();
    };
    textarea.addEventListener('input', onText);

    const fileInput = h('input', { type: 'file', accept: '.yaml,.yml,.txt', 'aria-label': 'YAML dosyası seç', disabled: !editable });
    fileInput.addEventListener('change', async () => {
      const file = fileInput.files && fileInput.files[0];
      fileInput.value = '';
      if (!file) return;
      if (file.size > 1024 * 1024) { toast('Dosya çok büyük (en çok 1 MB)', 'warn'); return; }
      try {
        textarea.value = await file.text();
        onText();
      } catch (error) { toastError(error); }
    });

    const showDiff = async (trigger) => {
      const text = textarea.value;
      if (!text.trim()) { toast('Önce YAML yapıştırın ya da bir dosya seçin', 'warn'); return; }
      trigger.disabled = true;
      try {
        const result = await post(p`/api/projects/${slug}/settings/import`, { yaml: text });
        importState.previewText = text;
        importState.changes = result.changes || [];
        drawPreview();
      } catch (error) {
        toastError(error);
      } finally {
        trigger.disabled = false;
      }
    };

    async function applyImport(trigger) {
      const text = textarea.value;
      if (text !== importState.previewText) { toast('Metin değişti; önce farkı yeniden gösterin', 'warn'); return; }
      const others = [...dirty].filter((id) => id !== 'aktar');
      if (!await confirmDialog('Bu projenin ayar katmanı dosyadakiyle değiştirilsin mi? Dokümanlar yeniden okunacak.'
        + (others.length ? ' Diğer sekmelerdeki kaydedilmemiş değişiklikler atılacak.' : ''), { title: 'Ayar profilini uygula', confirmLabel: 'Uygula' })) return;
      if (busy || !alive) return;
      busy = true;
      trigger.disabled = true;
      let applied = false;
      try {
        const result = await post(p`/api/projects/${slug}/settings/import`, { yaml: text, apply: true });
        applied = true;
        if (result.job) trackJob(result.job, { title: 'Ayar profili içe aktarıldı: dokümanlar yeniden okunuyor' });
        importState = { text: '', previewText: null, changes: null };
        const fresh = await get(url);
        if (alive) applyPayload(fresh, allPaths());
        toast('Ayar profili uygulandı', 'ok');
      } catch (error) {
        if (applied) toast('Ayar profili uygulandı ancak ekran yenilenemedi; sayfayı yenileyin.', 'warn');
        else toastError(error);
        if (trigger.isConnected) trigger.disabled = false;
      } finally {
        busy = false;
      }
    }

    drawPreview();
    return [
      sectionHead('Dışa aktar'),
      h('p', { class: 'set-intro', text: 'Bu projenin ayar katmanını (yalnız projede değiştirilen anahtarlar) YAML olarak indirir. '
        + 'Parola ve API anahtarı dosyaya girmez; ekip aynı kuralları bu dosyayla paylaşabilir.' }),
      overridden.length
        ? h('div', { class: 'set-chips' }, h('span', { class: 'set-chip-label', text: 'Projede değişen' }), overridden.map((path) => chip(path, null)))
        : h('p', { class: 'small muted', text: 'Bu projede özel ayar yok; tüm değerler genel ayarlardan ya da varsayılandan gelir.' }),
      h('div', { class: 'row', style: { marginTop: '10px' } },
        button('Ayarları indir (YAML)', () => download(p`/api/projects/${slug}/settings/export`))),
      h('div', { class: 'card-head', style: { marginTop: '22px' } }, h('h2', { text: 'İçe aktar' })),
      editable
        ? h('div', { class: 'stack' },
          h('p', { class: 'set-intro', text: 'YAML metnini yapıştırın ya da dosya seçin; önce fark gösterilir, "Uygula" ile kaydedilir.' }),
          h('div', { class: 'row' }, fileInput),
          textarea,
          h('div', { class: 'row' },
            button('Farkı göster', (event) => showDiff(event.currentTarget)),
            button('Temizle', () => { textarea.value = ''; onText(); }, { kind: 'ghost' })),
          previewBox)
        : h('p', { class: 'small muted', text: 'Ayar profilini yalnız düzenleyiciler içe aktarabilir.' }),
    ];
  }

  // -- çizim -- //

  const RENDER = {
    okuma: renderSynonyms, ad: renderNaming, sistem: renderSystems, okuyucu: renderReaders,
    diyagram: renderDiagram, yz: renderLLM, aktar: renderTransfer,
  };

  let bar = h('div');
  const body = h('div');
  let foot = null;

  function drawTabs() {
    const next = tabs(tabIds.map((id) => ({ id, label: dirty.has(id) ? `${SECTION[id].label} •` : SECTION[id].label })), active, (id) => {
      active = id;
      drawTabs();
      drawBody();
    });
    bar.replaceWith(next);
    bar = next;
  }

  function drawFoot() {
    if (!foot) return;
    clear(foot);
    const section = SECTION[active];
    if (!editable || !section.paths.length) return;
    const id = active;
    const isDirty = dirty.has(id);
    const resettable = pathsOf(id).filter(setHere);
    append(foot, [
      h('span', { class: 'small muted grow', text: isDirty ? 'Bu sekmede kaydedilmemiş değişiklikler var.' : 'Kaydedilmemiş değişiklik yok.' }),
      isProject && section.reading
        ? button('Etkiyi önizle', (event) => previewEffect(id, event.currentTarget), { disabled: !isDirty,
          title: 'Dokümanları kaydetmeden yeni ayarla okur ve modeldeki farkı gösterir' })
        : null,
      button('Değişiklikleri geri al', () => discard(id), { disabled: !isDirty }),
      button('Varsayılana dön', () => resetPaths(pathsOf(id), section.label), { disabled: !resettable.length,
        title: resettable.length ? resetWhere : 'Bu düzeyde değiştirilmiş ayar yok' }),
      button('Kaydet', () => save(id), { kind: 'primary', disabled: !isDirty }),
    ]);
  }

  function drawBody() {
    clear(body);
    forcedRows = null;
    foot = h('div', { class: 'set-foot' });
    body.append(h('div', { class: 'card' }, RENDER[active](), SECTION[active].paths.length ? foot : null));
    drawFoot();
  }

  append(root, [
    h('div', { class: 'set-legend' }, 'Değerin geldiği katman:', originBadge('varsayilan'), 'paket varsayılanı ·', originBadge('genel'),
      'genel ayar (tüm projeler)', isProject ? [' ·', originBadge('proje'), 'yalnız bu proje'] : null),
    editable ? null : h('div', { class: 'banner info' }, h('span', { class: 'grow', text: isProject
      ? 'Ayarları görüntülüyorsunuz; değiştirmek için düzenleyici rolü gerekir.'
      : 'Genel ayarları yalnız yöneticiler değiştirebilir.' })),
    bar,
    body,
  ]);
  computeDirty();
  drawTabs();
  drawBody();

  return {
    dispose() {
      alive = false;
      if (ownsDirty) setDirty(null);
      ownsDirty = false;
    },
  };
}
