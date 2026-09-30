// Günlük: elle düzeltmeler (sonuçları, geri alma) ve değişiklik günlüğü (süzme, yineleme).

import { del, get, p, post } from '../core/api.js';
import { debounce, h } from '../core/dom.js';
import { link } from '../core/router.js';
import { canEdit, emit, label, store } from '../core/store.js';
import { badge, button, confirmDialog, dataTable, loading, pageHeader, tabs, toast, toastError, when } from '../core/ui.js';
import { describeAudit } from './dashboard.js';

export const refreshOnChange = true;

/** Günlükteki işlem türleri (project/store.py, project/pipeline.py, project/runs.py `log` çağrıları). */
const ACTIONS = [
  ['proje_olustur', 'Proje oluşturma'], ['proje_guncelle', 'Proje güncelleme'],
  ['belge_yukle', 'Doküman yükleme'], ['belge_sil', 'Doküman silme'], ['yeniden_oku', 'Yeniden okuma'],
  ['duzeltme', 'Düzeltme'], ['geri_al', 'Geri alma'], ['yinele', 'Yineleme'],
  ['ayar', 'Ayar değişikliği'], ['ayar_ice_aktar', 'Ayar içe aktarma'],
  ['diyagram_uret', 'Diyagram üretimi'], ['calistirma_sil', 'Çalıştırma silme'],
];
const LIMITS = [100, 300, 1000, 5000];
const RESULTS = {
  uygulandi: ['uygulandı', 'ok'],
  sahipsiz: ['sahipsiz', 'warn'],
  hata: ['hata', 'bad'],
};
const FIELD_LABELS = {
  nesne: 'nesne', nesneler: 'nesneler', kaynak: 'kaynak', hedef: 'hedef', tur: 'tür', ad: 'ad', tip: 'tip',
  kural: 'kural', ozne: 'özne', anahtar: 'anahtar', katman: 'katman', sistem: 'sistem', hassasiyet: 'hassasiyet',
  aciklama: 'açıklama', surec: 'süreç', ifade: 'ifade', pk: 'birincil anahtar',
};

/** Ekran durumu yeniden çizimlerde (iş bitince, geri almadan sonra) korunur. */
const state = { slug: null, tab: 'duzeltmeler', result: '', user: '', action: '', limit: 300 };

function kindLabel(value) {
  const dataset = label.datasetKind(value);
  return dataset !== value ? dataset : label.processKind(value);
}

function plain(value) {
  if (Array.isArray(value)) return value.join(', ');
  if (typeof value === 'boolean') return value ? 'evet' : 'hayır';
  return String(value);
}

export function targetText(op) {
  const t = op?.hedef || {};
  if (t.kaynak !== undefined || t.hedef !== undefined) {
    return `${t.kaynak ?? '?'} → ${t.hedef ?? '?'}${t.tur ? ` (${label.edgeKind(t.tur)})` : ''}`;
  }
  if (Array.isArray(t.nesneler)) return t.nesneler.join(' + ');
  if (t.nesne) return String(t.nesne);
  if (t.ad) return `${t.ad}${t.tip ? ` (${t.tip === 'process' ? 'süreç' : 'tablo/dosya'})` : ''}`;
  if (t.kural) return `${t.kural}${t.ozne ? `: ${t.ozne}` : ''}`;
  if (t.anahtar) return String(t.anahtar);
  return Object.entries(t).map(([k, v]) => `${FIELD_LABELS[k] || k}: ${plain(v)}`).join(' · ');
}

export function valueText(op) {
  const parts = [];
  for (const [key, value] of Object.entries(op?.deger || {})) {
    if (value === null || value === undefined || value === '') continue;
    let shown;
    if (key === 'katman') shown = label.layer(value);
    else if (key === 'tur') shown = op.hedef?.tip === 'process' ? label.processKind(value) : kindLabel(value);
    else if (key === 'hassasiyet') shown = label.sensitivity(value);
    else shown = plain(value);
    parts.push(`${FIELD_LABELS[key] || key}: ${shown}`);
  }
  return parts.join(' · ');
}

function resultCell(result) {
  if (!result) return badge('sonuç yok', 'muted');
  const [text, kind] = RESULTS[result.durum] || [result.durum || '?', 'muted'];
  return h('div', {}, badge(text, kind), result.mesaj ? h('div', { class: 'small muted', text: result.mesaj }) : null);
}

/** Ayrıntısı describeAudit'te olmayan kayıtlar için kısa özet. */
function auditDetail(entry) {
  const { detail } = describeAudit(entry);
  const data = entry.veri || {};
  switch (entry.islem) {
    case 'yeniden_oku': return [data.llm ? 'LLM ile' : 'LLM\'siz', data.nesne != null ? `${data.nesne} nesne` : null].filter(Boolean).join(' · ');
    case 'ayar': {
      const paths = [];
      const walk = (value, prefix) => {
        if (value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length) {
          for (const [k, v] of Object.entries(value)) walk(v, prefix ? `${prefix}.${k}` : k);
        } else paths.push(prefix);
      };
      walk(data.yama || {}, '');
      return paths.filter(Boolean).join(', ');
    }
    case 'ayar_ice_aktar': return (data.anahtarlar || []).join(', ');
    case 'diyagram_uret':
    case 'calistirma_sil': return data.calistirma || '';
    case 'proje_olustur':
    case 'proje_guncelle': return data.ad || '';
    default: return detail;
  }
}

export async function render(root, { slug }) {
  if (state.slug !== slug) Object.assign(state, { slug, tab: 'duzeltmeler', result: '', user: '', action: '', limit: 300 });
  const corrections = await get(p`/api/projects/${slug}/corrections`);
  const active = new Set(corrections.map((c) => c.id));
  const orphans = corrections.filter((c) => c.result?.durum === 'sahipsiz');
  const errors = corrections.filter((c) => c.result?.durum === 'hata');
  const editable = canEdit();

  const changed = () => emit('project:changed'); // menü sayaçları tazelenir, ekran yeniden çizilir

  const undo = async (op) => {
    if (!await confirmDialog(`${op.id} — ${op.label}: ${targetText(op)} geri alınsın mı? Düzeltme kaldırılır; değişiklik günlüğünden yinelenebilir.`,
      { title: 'Düzeltmeyi geri al', confirmLabel: 'Geri al', danger: true })) return;
    try {
      await del(p`/api/projects/${slug}/corrections/${op.id}`);
      toast(`${op.id} geri alındı`, 'ok');
      changed();
    } catch (error) { toastError(error); }
  };

  const redo = async (id) => {
    try {
      await post(p`/api/projects/${slug}/corrections/${id}/redo`, {});
      toast(`${id} yinelendi`, 'ok');
      changed();
    } catch (error) { toastError(error); }
  };

  root.append(pageHeader({
    title: 'Günlük',
    subtitle: 'Elle yapılan düzeltmeler ve projedeki tüm değişikliklerin kaydı.',
    crumbs: [{ label: 'Projeler', href: '#/projeler' }, { label: store.projectInfo?.ad || slug, href: link('p', slug) }, { label: 'Günlük' }],
  }));

  const bar = h('div');
  const content = h('div');
  root.append(bar, content);

  // ---- düzeltmeler ---------------------------------------------------- //

  const correctionsView = () => {
    const box = h('div', { class: 'stack' });
    box.append(h('p', { class: 'small muted', text: 'Düzeltmeler dokümanlardan ayrı saklanır ve her okumadan sonra sırayla modele uygulanır; hedefler adla verilir. Bir düzeltmeyi geri almak onu listeden kaldırır; kaydı günlükte kalır ve oradan yinelenebilir.' }));
    if (orphans.length) {
      box.append(h('div', { class: 'banner warn' }, h('div', { class: 'grow' },
        h('b', { text: `${orphans.length} sahipsiz düzeltme. ` }),
        h('span', { text: 'Sahipsiz düzeltmenin hedefi (tablo, süreç ya da bağ) güncel modelde bulunamadı, bu yüzden uygulanmadı. Genellikle doküman değişip nesne yeniden adlandırıldığında ya da kaldırıldığında olur. Kayıt silinmez: hedef yeniden ortaya çıkarsa sonraki okumada yine uygulanır. Artık gerekmiyorsa geri alın.' }))));
    }
    if (errors.length) {
      box.append(h('div', { class: 'banner bad' }, h('span', { class: 'grow', text: `${errors.length} düzeltme hatayla uygulanamadı; ayrıntı "Sonuç" sütununda.` })));
    }
    const filterSelect = h('select', { 'aria-label': 'Sonuca göre süz', style: { width: 'auto' } },
      [['', `Tümü (${corrections.length})`], ['uygulandi', 'Uygulananlar'], ['sahipsiz', `Sahipsizler (${orphans.length})`], ['hata', `Hatalılar (${errors.length})`]]
        .map(([value, text]) => h('option', { value, selected: value === state.result }, text)));
    const rowsFor = () => (state.result ? corrections.filter((c) => c.result?.durum === state.result) : corrections);
    const table = dataTable({
      rows: rowsFor(),
      rowKey: (c) => c.id,
      empty: corrections.length ? 'Bu sonuçta düzeltme yok' : 'Henüz elle düzeltme yapılmadı',
      columns: [
        { key: 'id', label: 'No', render: (c) => h('span', { class: 'mono nowrap', text: c.id }) },
        { key: 'label', label: 'İşlem', render: (c) => h('span', { class: 'nowrap', text: c.label || label.op(c.islem) }) },
        { key: 'target', label: 'Hedef', wrap: true, sort: (c) => targetText(c), render: (c) => h('span', { class: 'audit-summary', text: targetText(c) }) },
        { key: 'value', label: 'Değer', wrap: true, sort: (c) => valueText(c), render: (c) => h('span', { class: 'audit-summary', text: valueText(c) || '—' }) },
        { key: 'neden', label: 'Neden', wrap: true, render: (c) => h('span', { class: 'small', text: c.neden || '—' }) },
        { key: 'kim', label: 'Kim' },
        { key: 'zaman', label: 'Zaman', render: (c) => when(c.zaman) },
        { key: 'result', label: 'Sonuç', sort: (c) => c.result?.durum || '', render: (c) => resultCell(c.result) },
        { key: 'actions', label: '', sort: false, render: (c) => (editable ? button('Geri al', () => undo(c), { kind: 'small' }) : null) },
      ],
    });
    filterSelect.addEventListener('change', () => { state.result = filterSelect.value; table.setRows(rowsFor()); });
    box.append(h('div', { class: 'toolbar' }, h('span', { class: 'small muted', text: 'Sonuç:' }), filterSelect), table.el);
    return box;
  };

  // ---- değişiklik günlüğü --------------------------------------------- //

  const logView = () => {
    const box = h('div', { class: 'stack' });
    const users = new Set(corrections.map((c) => c.kim).filter(Boolean));
    if (store.user?.username) users.add(store.user.username);
    const userInput = h('input', { type: 'search', placeholder: 'Kullanıcı adı', 'aria-label': 'Kullanıcıya göre süz', list: 'audit-users', value: state.user, style: { maxWidth: '200px' } });
    const userList = h('datalist', { id: 'audit-users' });
    const actionSelect = h('select', { 'aria-label': 'İşleme göre süz', style: { width: 'auto' } },
      h('option', { value: '', selected: !state.action }, 'Tüm işlemler'),
      ACTIONS.map(([value, text]) => h('option', { value, selected: value === state.action }, text)));
    const limitSelect = h('select', { 'aria-label': 'Kayıt sayısı', style: { width: 'auto' } },
      LIMITS.map((n) => h('option', { value: String(n), selected: n === state.limit }, `son ${n}`)));
    const listBox = h('div');
    let token = 0;

    const fillUsers = () => userList.replaceChildren(...[...users].sort().map((u) => h('option', { value: u })));
    const load = async () => {
      const mine = ++token;
      listBox.replaceChildren(loading());
      const params = new URLSearchParams({ limit: String(state.limit) });
      if (state.user) params.set('user', state.user);
      if (state.action) params.set('action', state.action);
      let entries;
      try {
        entries = await get(`${p`/api/projects/${slug}/audit`}?${params}`);
      } catch (error) {
        if (mine === token) listBox.replaceChildren(h('div', { class: 'banner bad' }, h('span', { class: 'grow', text: error.detail || error.message })));
        return;
      }
      if (mine !== token) return;
      for (const entry of entries) if (entry.kim) users.add(entry.kim);
      fillUsers();
      // Aynı düzeltme birkaç kez geri alınmış olabilir: "Yinele" yalnız en yeni (ilk görülen) geri alma kaydında.
      const correctionId = (entry) => entry.veri?.islem_kaydi?.id || null;
      const offered = new Set();
      const rows = entries.map((entry, index) => {
        const id = correctionId(entry);
        const offerRedo = entry.islem === 'geri_al' && id && !active.has(id) && !offered.has(id);
        if (offerRedo) offered.add(id);
        return { ...entry, index, offerRedo };
      });
      const table = dataTable({
        rows,
        rowKey: (e) => e.index,
        empty: state.user || state.action ? 'Bu süzgeçle kayıt yok' : 'Günlükte kayıt yok',
        columns: [
          { key: 'zaman', label: 'Zaman', render: (e) => when(e.zaman) },
          { key: 'kim', label: 'Kim' },
          { key: 'islem', label: 'İşlem', sort: (e) => describeAudit(e).action, render: (e) => h('span', { text: describeAudit(e).action }) },
          { key: 'detail', label: 'Ayrıntı', wrap: true, sort: (e) => auditDetail(e), render: (e) => h('span', { class: 'audit-summary', text: auditDetail(e) || '—' }) },
          { key: 'state', label: '', sort: false, render: (e) => {
            const id = correctionId(e);
            if (!id || !['duzeltme', 'geri_al', 'yinele'].includes(e.islem)) return null;
            const idBadge = badge(id, 'muted');
            if (active.has(id)) return h('div', { class: 'row' }, idBadge, badge('etkin', 'ok'));
            if (e.offerRedo && editable) {
              return h('div', { class: 'row' }, idBadge, button('Yinele', () => redo(id), { kind: 'small', title: 'Geri alınan düzeltmeyi yeniden ekler' }));
            }
            return h('div', { class: 'row' }, idBadge, badge('geri alındı', 'muted'));
          } },
        ],
      });
      listBox.replaceChildren(table.el);
    };

    const applyUser = debounce(() => { state.user = userInput.value.trim(); load(); }, 400);
    userInput.addEventListener('input', applyUser);
    actionSelect.addEventListener('change', () => { state.action = actionSelect.value; load(); });
    limitSelect.addEventListener('change', () => { state.limit = Number(limitSelect.value); load(); });
    fillUsers();
    box.append(h('div', { class: 'toolbar' }, userInput, userList, actionSelect, limitSelect,
      h('span', { class: 'small muted', text: 'En yeni kayıt en üstte.' })), listBox);
    load();
    return box;
  };

  const draw = () => {
    bar.replaceChildren(tabs([
      { id: 'duzeltmeler', label: 'Düzeltmeler', count: corrections.length },
      { id: 'gunluk', label: 'Değişiklik günlüğü' },
    ], state.tab, (id) => { state.tab = id; draw(); }));
    content.replaceChildren(state.tab === 'gunluk' ? logView() : correctionsView());
  };
  draw();
}
