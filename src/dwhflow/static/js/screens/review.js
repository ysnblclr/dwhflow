// Onay kuyruğu: taslak bağlar ve nesneler, birleştirme önerileri, insan bakmalı paragraflar, tanınmayan tablolar.
// Kararlar POST review/decide ile düzeltmeye (ya da tablo için okuma ayarına) çevrilir.

import { ApiError, get, p, post } from '../core/api.js';
import { append, clear, fold, h } from '../core/dom.js';
import { link } from '../core/router.js';
import { canEdit, label, options } from '../core/store.js';
import { badge, button, confirmDialog, empty, layerPill, modal, pageHeader, tabs, toast, toastError, trackJob } from '../core/ui.js';
import * as edit from './_model_edit.js';

export const refreshOnChange = true;

const TYPES = [
  { id: 'all', label: 'Tümü' }, { id: 'bag', label: 'Taslak bağlar' }, { id: 'nesne', label: 'Taslak nesneler' },
  { id: 'birlestir', label: 'Birleştirme' }, { id: 'paragraf', label: 'Paragraflar' }, { id: 'tablo', label: 'Tablolar' },
];
const TYPE_BADGE = { bag: 'taslak bağ', nesne: 'taslak nesne', birlestir: 'birleştirme', paragraf: 'paragraf', tablo: 'tablo' };
const ORIGIN = { metin: ['metin', 'muted'], llm: ['LLM', 'info'], kural: ['kural', 'ok'] };
const REJECT_LABEL = { bag: 'Reddet (bağı sil)', nesne: 'Reddet (nesneyi sil)', birlestir: 'Reddet (ayrı kalsın)' };
/** Toplu kabul ve önizleme yalnız bu türlerde: paragraf bağ formuyla, tablo sütun eşlemesiyle kabul edilir. */
const SIMPLE = new Set(['bag', 'nesne', 'birlestir']);
const PAGE = 50;

// Seçili sekme proje başına; yeniden okuması süren tablo eşlemeleri (anahtar: slug|öğe).
const memory = { slug: null, tab: 'all', pending: new Set() };

export async function render(root, { slug }) {
  if (memory.slug !== slug) { memory.slug = slug; memory.tab = 'all'; }
  const [data, datasets] = await Promise.all([
    get(p`/api/projects/${slug}/review`),
    edit.loadAll(slug, 'datasets').catch(() => []),
  ]);
  const processes = edit.lazy(() => edit.loadAll(slug, 'processes'));
  const ids = new Map(datasets.map((d) => [fold(d.name), d.id]));
  const counts = data.counts || {};
  const byKey = new Map(data.items.map((item) => [item.key, item]));
  const selected = new Set();
  const checks = new Map();
  let limit = PAGE;

  const itemsOf = () => (memory.tab === 'all' ? data.items : data.items.filter((item) => item.type === memory.tab));
  const shown = () => itemsOf().slice(0, limit);
  const chosen = () => [...selected].map((key) => byKey.get(key)).filter(Boolean);
  const nameLink = (name) => {
    const id = ids.get(fold(name));
    return id ? h('a', { href: link('p', slug, 'tablolar', id), text: name }) : h('span', { text: name });
  };

  // ---- kararlar ---------------------------------------------------------- //

  /** Dönüş: 'ok' | 'gone' (öğe artık kuyrukta değil; liste yenilenir) | null (hata bildirildi). */
  const decide = async (decisions, message) => {
    try {
      const result = await post(p`/api/projects/${slug}/review/decide`, { decisions });
      if (result.job) {
        const keys = decisions.map((d) => `${slug}|${d.key}`);
        keys.forEach((key) => memory.pending.add(key));
        trackJob(result.job, { title: 'Tablo eşlemesi: dokümanlar yeniden okunuyor' })
          .finally(() => keys.forEach((key) => memory.pending.delete(key)));
      }
      edit.changed(message);
      return 'ok';
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        toast(`${error.detail}. Liste yenileniyor.`, 'warn');
        edit.changed();
        return 'gone';
      }
      toastError(error);
      return null;
    }
  };

  /** Düğmeler iş sürerken kilitlenir (çift tıklamayla aynı karar iki kez gitmesin). */
  const locked = (container, fn) => edit.act(async () => {
    const buttons = [...container.querySelectorAll('button')];
    buttons.forEach((b) => { b.disabled = true; });
    try { await fn(); } finally { buttons.forEach((b) => { b.disabled = false; }); }
  });

  const preview = async (items, decision) => {
    const list = decision === 'accept' ? items.filter((item) => SIMPLE.has(item.type)) : items;
    if (!list.length) { toast('Önizlenecek öğe yok: paragraf ve tablo öğeleri kendi formlarıyla kabul edilir.', 'warn'); return; }
    const ops = list.flatMap((item) => (decision === 'accept' ? item.accept_ops : item.reject_ops) || []);
    let diff;
    try {
      diff = await post(p`/api/projects/${slug}/corrections/preview`, { ops });
    } catch (error) { toastError(error); return; }
    const blocked = (diff.results || []).some((r) => r.durum !== 'uygulandi');
    const accept = decision === 'accept';
    const skipped = items.length - list.length;
    const choice = await modal({
      title: `${accept ? 'Kabul' : 'Ret'} önizlemesi (${list.length} öğe)`, wide: true,
      body: h('div', { class: 'stack' },
        skipped ? edit.note(`${skipped} paragraf/tablo öğesi önizlemeye alınmadı.`) : null,
        edit.diffView(diff)),
      actions: blocked
        ? [{ label: 'Kapat', value: null }]
        : [{ label: 'Kapat', value: null },
          { label: accept ? `Kabul et (${list.length})` : `Reddet (${list.length})`, kind: accept ? 'primary' : 'danger solid', value: 'go' }],
    });
    if (choice === 'go') {
      await decide(list.map((item) => ({ key: item.key, decision })), `${list.length} öğe ${accept ? 'kabul edildi' : 'reddedildi'}`);
    }
  };

  const bulk = async (decision) => {
    let items = chosen();
    if (!items.length) return;
    const accept = decision === 'accept';
    if (accept) {
      const skipped = items.filter((item) => !SIMPLE.has(item.type));
      items = items.filter((item) => SIMPLE.has(item.type));
      if (skipped.length) toast(`${skipped.length} paragraf/tablo öğesi toplu kabulde atlandı; onları kartındaki formla işleyin.`, 'warn');
      if (!items.length) return;
    }
    if (items.length > 1) {
      const message = accept
        ? `${items.length} öğe kabul edilsin mi? Bağlar ve nesneler onaylanır, birleştirmeler uygulanır.`
        : `${items.length} öğe reddedilsin mi? Taslak bağlar ve bağsız taslak nesneler modelden silinir; öneriler bir daha sorulmaz. Düzeltmeler günlükten geri alınabilir.`;
      if (!await confirmDialog(message, { title: accept ? 'Toplu kabul' : 'Toplu ret', confirmLabel: accept ? 'Kabul et' : 'Reddet', danger: !accept })) return;
    }
    await decide(items.map((item) => ({ key: item.key, decision })), `${items.length} öğe ${accept ? 'kabul edildi' : 'reddedildi'}`);
  };

  const paragraphLink = async (item) => {
    const procs = await processes();
    const names = datasets.map((d) => d.name);
    const values = await edit.formModal({
      title: 'Paragraftan bağ ekle', submitLabel: 'Bağ ekle', wide: true,
      intro: h('div', { class: 'evidence' },
        h('span', { class: 'src', text: `${item.file} · ${item.locator}` }),
        h('span', { style: { whiteSpace: 'pre-wrap' }, text: item.text })),
      fields: [
        { name: 'kaynak', label: 'Kaynak (okunan tablo/dosya)', required: true, suggestions: names },
        { name: 'hedef', label: 'Hedef (yazılan tablo/dosya)', required: true, suggestions: names },
        { name: 'surec', label: 'Süreç (isteğe bağlı)', suggestions: procs.map((x) => x.name), hint: 'Modelde yoksa eklenir; boşsa doğrudan bağ kurulur.' },
        { name: 'olustur', label: 'Modelde olmayan kaynak/hedefi yeni tablo olarak ekle', type: 'checkbox', value: false, wide: true },
        { name: 'kapat', label: 'Paragrafı kuyruktan kaldır (başka akış yok)', type: 'checkbox', value: true, wide: true },
        edit.reasonField(`Paragraf: ${item.file} · ${item.locator}`),
      ],
      validate: (v) => (fold(v.kaynak.trim()) === fold(v.hedef.trim()) ? 'Kaynak ile hedef aynı olamaz' : null),
      onSubmit: async (v) => {
        const missing = await edit.missingDatasets(slug, [v.kaynak, v.hedef]);
        if (missing.length && !v.olustur) {
          throw new Error(`Modelde bulunamadı: ${missing.join(', ')}. Adı düzeltin ya da "yeni tablo olarak ekle" seçeneğini işaretleyin.`);
        }
        if (missing.some((name) => name.includes('#'))) throw new Error("Yeni tablo adı '#' içeremez (kolon ayırıcısı)");
        const [kaynak, hedef] = await Promise.all([edit.nodeRef(slug, v.kaynak), edit.nodeRef(slug, v.hedef)]);
        const surec = v.surec.trim();
        // Eksik uçlar ve bağ tek istekte: biri tutmazsa hiçbiri kaydedilmez.
        await edit.sendOps(slug, [
          ...missing.map((name) => edit.op('nesne_ekle', { ad: name, tip: 'dataset' }, null, v.neden)),
          edit.op('bag_ekle', { kaynak, hedef }, surec ? { surec } : null, v.neden),
        ]);
        return v;
      },
    });
    if (!values) return;
    if (!values.kapat) { edit.changed('Bağ eklendi; paragraf kuyrukta kalıyor'); return; }
    const outcome = await decide([{ key: item.key, decision: 'reject' }], 'Bağ eklendi; paragraf kuyruktan kaldırıldı');
    if (outcome === null) edit.changed('Bağ eklendi (paragraf kapatılamadı)');
  };

  const saveMapping = async (item, selects) => {
    const mapping = {};
    for (const [header, select] of selects) if (select.value) mapping[header] = select.value;
    const used = Object.values(mapping);
    if (!used.length) { toast('En az bir sütuna rol verin', 'warn'); return; }
    const duplicate = used.find((role, i) => used.indexOf(role) !== i);
    if (duplicate) { toast(`"${label.columnRole(duplicate)}" rolü birden çok sütuna verilmiş`, 'warn'); return; }
    const roles = new Set(used);
    const usable = (roles.has('source_table') && roles.has('target_table'))
      || (roles.has('job') && ['predecessor', 'source_table', 'target_table'].some((r) => roles.has(r)));
    if (!usable && !await confirmDialog(
      `Bu eşleme okuyucunun tabloyu tanımasına yetmeyebilir: kaynaktan hedefe eşleme için "${label.columnRole('source_table')}" ve "${label.columnRole('target_table')}", `
      + `job listesi için "${label.columnRole('job')}" ile birlikte "${label.columnRole('predecessor')}" ya da bir tablo rolü gerekir. Yine de kaydedilsin mi?`,
      { title: 'Eşleme eksik olabilir', confirmLabel: 'Kaydet' })) return;
    await decide([{ key: item.key, decision: 'accept', mapping }], 'Başlıklar proje okuma ayarlarına eklendi; dokümanlar yeniden okunuyor');
  };

  // ---- kartlar ----------------------------------------------------------- //

  const bagBody = (item) => h('div', { class: 'small', style: { marginTop: '6px' } },
    h('div', {}, 'Kaynak: ', nameLink(item.source), ' → hedef: ', nameLink(item.target)),
    h('div', { class: 'muted', text: item.processes?.length ? `Süreç: ${item.processes.join(', ')}` : 'Doğrudan (süreçsiz) bağ' }),
    item.edges?.length ? h('ul', { class: 'list', style: { marginTop: '4px' } }, item.edges.map((e) => h('li', {},
      badge(label.edgeKind(e.tur), 'muted'), h('span', { style: { overflowWrap: 'anywhere' }, text: `${e.kaynak} → ${e.hedef}` })))) : null);

  const nodeBody = (item) => {
    const [type, layer] = String(item.detail || '').split(' · ');
    return h('div', { class: 'small row', style: { marginTop: '6px' } },
      h('span', { text: type === 'process' ? 'süreç' : 'tablo/dosya' }), layer ? layerPill(layer) : null,
      type === 'process' ? null : nameLink(item.title),
      h('span', { class: 'muted', text: 'Hiçbir bağı yok. Kabul: onaylanır · ret: modelden silinir.' }));
  };

  const mergeBody = (item) => h('div', { class: 'small', style: { marginTop: '6px' } },
    h('div', { class: 'muted', text: `Neden: ${item.detail}` }),
    h('div', {}, 'Adlar: ', (item.names || []).map((name, i) => [i ? ' + ' : '', nameLink(name)])),
    h('div', {}, 'Kabulde kalacak ad: ', h('b', { text: item.preferred })),
    h('div', { class: 'muted', text: 'Başka bir ad için tablolar ekranındaki "Birleştir"i kullanın.' }));

  const paragraphBody = (item) => h('div', { style: { marginTop: '6px' } },
    h('div', { class: 'small muted', text: item.detail }),
    h('div', { class: 'evidence' },
      h('span', { class: 'src' }, item.file ? edit.docLink(slug, item.file) : '—', item.locator ? ` · ${item.locator}` : ''),
      h('span', { style: { whiteSpace: 'pre-wrap' }, text: item.text })));

  const tableBody = (item, editable) => {
    const roles = item.roles && Object.keys(item.roles).length
      ? Object.entries(item.roles).map(([value, text]) => ({ value, label: text }))
      : options('column_roles');
    const advice = item.suggestion || null;
    // Öneri biçimi: {suggestion: {table_kind, roles: {başlık: rol}}, usable, reasons, error, …}
    const suggested = advice?.suggestion?.roles || advice?.roles || {};
    const selects = [];
    const rows = (item.headers || []).map((raw, index) => {
      const header = String(raw ?? '');
      const hint = suggested[header] ?? suggested[header.trim()];
      const samples = (item.rows || []).slice(0, 3).map((row) => row?.[index])
        .filter((v) => v != null && String(v).trim() !== '').join(' · ');
      let control;
      if (editable && header.trim()) {
        control = h('select', { 'aria-label': `${header} sütununun rolü` }, h('option', { value: '' }, '— (yok say)'),
          roles.map((r) => h('option', { value: r.value }, r.label)));
        control.value = roles.some((r) => r.value === hint) ? hint : '';
        selects.push([header, control]);
      } else {
        control = h('span', { class: 'muted', text: hint ? label.columnRole(hint) : '—' });
      }
      return h('tr', {}, h('td', { class: 'mono', text: header || '(boş)' }), h('td', { class: 'wrap small', text: samples || '—' }), h('td', {}, control));
    });
    const bar = h('div', { class: 'row', style: { marginTop: '8px' } });
    if (editable) {
      append(bar, [
        button('Eşlemeyi kaydet ve yeniden oku', locked(bar, () => saveMapping(item, selects)), { kind: 'small primary' }),
        button('Reddet (yok say)', locked(bar, () => decide([{ key: item.key, decision: 'reject' }], 'Tablo yok sayıldı')), { kind: 'small danger' }),
      ]);
    }
    return h('div', { style: { marginTop: '6px' } },
      h('div', { class: 'small muted' }, item.file ? edit.docLink(slug, item.file) : null, item.locator ? ` · ${item.locator}` : '', ` — ${item.detail}`),
      advice ? h('div', { class: 'small', style: { marginTop: '4px' } },
        badge(advice.usable ? 'LLM önerisi' : 'LLM önerisi (yetersiz)', advice.usable ? 'info' : 'warn'),
        advice.suggestion?.table_kind ? ` tablo türü: ${advice.suggestion.table_kind}` : '',
        advice.error ? h('div', { style: { color: 'var(--bad)' }, text: advice.error }) : null,
        advice.reasons?.length ? h('ul', { class: 'muted', style: { margin: '2px 0', paddingLeft: '18px' } }, advice.reasons.map((r) => h('li', { text: r }))) : null) : null,
      h('div', { class: 'table-wrap', style: { marginTop: '6px' } }, h('table', { class: 'data' },
        h('thead', {}, h('tr', {}, h('th', { text: 'Sütun başlığı' }), h('th', { text: 'Örnek değerler' }), h('th', { text: 'Rol' }))),
        h('tbody', {}, rows))),
      editable ? edit.note('Kaydedilince başlıklar proje okuma ayarlarına eş anlamlı olarak eklenir ve dokümanlar yeniden okunur.') : null,
      editable ? bar : null);
  };

  const actionsFor = (item) => {
    const bar = h('div', { class: 'row', style: { marginTop: '8px' } });
    if (item.type === 'paragraf') {
      append(bar, [
        button('Bağ ekle', locked(bar, () => paragraphLink(item)), { kind: 'small primary' }),
        button('Akış yok (reddet)', locked(bar, () => decide([{ key: item.key, decision: 'reject' }], 'Paragraf kuyruktan kaldırıldı')), { kind: 'small' }),
      ]);
    } else {
      append(bar, [
        button('Kabul et', locked(bar, () => decide([{ key: item.key, decision: 'accept' }], 'Kabul edildi')), { kind: 'small ok' }),
        button(REJECT_LABEL[item.type] || 'Reddet', locked(bar, () => decide([{ key: item.key, decision: 'reject' }], 'Reddedildi')), { kind: 'small danger' }),
        button('Önizle', locked(bar, () => preview([item], 'accept')), { kind: 'small ghost', title: 'Kabul edilirse modelde ne değişir?' }),
      ]);
    }
    return bar;
  };

  const card = (item) => {
    const pending = memory.pending.has(`${slug}|${item.key}`);
    const editable = canEdit() && !pending;
    let check = null;
    if (canEdit()) {
      check = h('input', {
        type: 'checkbox', 'aria-label': 'Seç', checked: selected.has(item.key),
        onchange: (event) => { if (event.target.checked) selected.add(item.key); else selected.delete(item.key); updateToolbar(); },
      });
      checks.set(item.key, check);
    }
    const [originText, originKind] = ORIGIN[item.origin] || [item.origin || '—', 'muted'];
    const confidence = edit.percent(item.confidence);
    let body = null;
    if (item.type === 'bag') body = bagBody(item);
    else if (item.type === 'nesne') body = nodeBody(item);
    else if (item.type === 'birlestir') body = mergeBody(item);
    else if (item.type === 'paragraf') body = paragraphBody(item);
    else if (item.type === 'tablo') body = tableBody(item, editable);
    else if (item.detail) body = h('div', { class: 'small muted', text: item.detail });
    return h('div', { class: 'card', style: { padding: '12px 16px' } },
      h('div', { class: 'row' }, check, badge(TYPE_BADGE[item.type] || item.type, 'muted'),
        h('b', { class: 'grow', style: { overflowWrap: 'anywhere' }, text: item.title }),
        badge(originText, originKind), confidence ? badge(confidence, 'muted') : null,
        pending ? badge('yeniden okunuyor…', 'info') : null),
      body,
      item.evidence?.length ? h('div', { style: { marginTop: '6px' } }, item.evidence.map((s) => edit.sourceBlock(slug, s))) : null,
      editable && item.type !== 'tablo' ? actionsFor(item) : null);
  };

  // ---- sayfa ------------------------------------------------------------- //

  root.append(pageHeader({
    title: 'Onay kuyruğu',
    subtitle: 'Modele kendiliğinden girmemesi gerekenler: taslak bağlar ve nesneler, birleştirme önerileri, insan bakmalı paragraflar ve tanınmayan tablolar.',
    crumbs: [{ label: 'Pano', href: link('p', slug) }, { label: 'Onay kuyruğu' }],
  }));

  const tabBar = h('div');
  const listBox = h('div', { class: 'stack' });
  const selectAll = h('input', { type: 'checkbox', 'aria-label': 'Görünen öğelerin tümünü seç' });
  const selectionText = h('span', { class: 'small muted' });
  const toolbar = h('div', { class: 'toolbar' });
  const bulkButtons = [
    button('Seçilenleri kabul et', locked(toolbar, () => bulk('accept')), { kind: 'small ok' }),
    button('Seçilenleri reddet', locked(toolbar, () => bulk('reject')), { kind: 'small danger' }),
    button('Kabulü önizle', locked(toolbar, () => preview(chosen(), 'accept')), { kind: 'small' }),
    button('Reddi önizle', locked(toolbar, () => preview(chosen(), 'reject')), { kind: 'small' }),
  ];
  append(toolbar, [h('label', { class: 'field inline' }, selectAll, h('span', { text: 'Tümünü seç' })), selectionText, h('span', { class: 'grow' }), bulkButtons]);

  function updateToolbar() {
    const visible = shown();
    selectionText.textContent = selected.size ? `${selected.size} öğe seçili` : 'Seçim yok';
    for (const b of bulkButtons) b.disabled = !selected.size;
    selectAll.checked = visible.length > 0 && visible.every((item) => selected.has(item.key));
  }

  selectAll.addEventListener('change', () => {
    for (const item of shown()) {
      if (selectAll.checked) selected.add(item.key); else selected.delete(item.key);
      const box = checks.get(item.key);
      if (box) box.checked = selectAll.checked;
    }
    updateToolbar();
  });

  function drawList() {
    clear(listBox);
    checks.clear();
    const items = itemsOf();
    if (!items.length) {
      listBox.append(h('div', { class: 'card' }, empty(memory.tab === 'all' ? 'Onay bekleyen öğe yok.' : 'Bu türde onay bekleyen öğe yok.')));
      return;
    }
    for (const item of items.slice(0, limit)) listBox.append(card(item));
    if (items.length > limit) {
      listBox.append(h('div', { class: 'row', style: { justifyContent: 'center' } },
        button(`${Math.min(PAGE, items.length - limit)} öğe daha göster (${items.length - limit} kaldı)`, () => { limit += PAGE; drawList(); updateToolbar(); })));
    }
  }

  function drawTabs() {
    clear(tabBar);
    tabBar.append(tabs(TYPES.map((t) => ({ id: t.id, label: t.label, count: t.id === 'all' ? counts.toplam : counts[t.id] })), memory.tab, (next) => {
      memory.tab = next;
      selected.clear();
      limit = PAGE;
      drawTabs();
      drawList();
      updateToolbar();
    }));
  }

  drawTabs();
  root.append(tabBar);
  if (canEdit() && data.items.length) root.append(toolbar);
  root.append(listBox);
  drawList();
  updateToolbar();
  edit.restoreScroll();
}
