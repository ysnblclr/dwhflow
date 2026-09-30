// Dokümanlar: yükleme, örnek ekleme, yeniden okuma (LLM'li ya da LLM'siz), liste, sürümler,
// okuyucu zorlama, silme.

import { del, download, get, p, post, put, upload } from '../core/api.js';
import { fmtSize, h } from '../core/dom.js';
import { link } from '../core/router.js';
import { canEdit, store } from '../core/store.js';
import {
  badge, button, confirmDialog, dataTable, docStatusBadge, dropzone, empty, formDialog, modal, pageHeader,
  toast, toastError, trackJob, when,
} from '../core/ui.js';

export const refreshOnChange = true;

const MAX_UPLOAD_FILES = 100;

/** Okuyucunun nasıl seçildiği (DocReport.detected). */
const DETECTED = {
  uzanti: ['uzantıdan', 'muted', 'Okuyucu dosya uzantısından seçildi.'],
  icerik: ['içerikten', 'info', 'Uzantı genel ya da bilinmiyordu; biçim dosya içeriğinden tanındı.'],
  zorla: ['zorlandı', 'warn', 'Okuyucu proje ayarlarında elle seçildi.'],
  yedek: ['genel dönüştürücü', 'warn', 'Özel okuyucu yok; genel dönüştürücüyle metin olarak okundu.'],
  yok: ['tanınmadı', 'bad', 'Bu dosya türü için okuyucu yok.'],
};

export function detectedBadge(value) {
  if (!value) return null;
  const [text, kind, title] = DETECTED[value] || [value, 'muted', ''];
  const node = badge(text, kind);
  if (title) node.title = title;
  return node;
}

/** Açık pencereyi kendi Esc davranışıyla kapatır (söz çözülür, dinleyici kalkar). */
function closeModal() {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
}

/** Okuma raporundaki sayılar: "9 tablo · 6 akış" (sıfırlar yazılmaz). */
function countsText(report) {
  if (!report) return '';
  const parts = [];
  if (report.datasets) parts.push(`${report.datasets} tablo/dosya`);
  if (report.processes) parts.push(`${report.processes} süreç`);
  if (report.flows) parts.push(`${report.flows} akış`);
  return parts.join(' · ') || 'hiçbir şey çıkarılmadı';
}

function countsCell(report) {
  if (!report) return h('span', { class: 'small muted', text: 'okunmadı' });
  return h('div', {},
    h('div', { class: 'small', text: countsText(report) }),
    report.leftovers ? badge(`${report.leftovers} paragraf bakılmalı`, 'warn') : null);
}

function notesCell(report) {
  const notes = report?.notes || [];
  if (!notes.length) return null;
  return h('div', { class: 'small muted', title: notes.join('\n') }, notes[0],
    notes.length > 1 ? h('span', { class: 'nowrap', text: ` (+${notes.length - 1} not)` }) : null);
}

/** Arşivden çıkan dosyalar arşivin altında listelenir. */
function innerList(doc) {
  if (!doc.inner?.length) return null;
  return h('ul', { class: 'list documents-inner' }, doc.inner.map((r) => h('li', {},
    h('span', { class: 'muted', text: '↳' }),
    h('span', { class: 'grow' }, h('span', { text: r.file }), h('div', { class: 'small muted', text: `${r.reader || '—'} · ${countsText(r)}` }),
      (r.notes || []).length ? h('div', { class: 'small muted', title: r.notes.join('\n'), text: r.notes[0] }) : null),
    detectedBadge(r.detected), docStatusBadge(r.status),
    r.leftovers ? badge(`${r.leftovers} paragraf`, 'warn') : null)));
}

function acceptedHint(formats) {
  if (!formats) return null;
  const suffixes = new Set();
  for (const item of formats.formats || []) for (const suffix of item.suffixes || []) suffixes.add(suffix);
  for (const suffix of formats.archives || []) suffixes.add(suffix);
  const list = [...suffixes].sort().join(' ');
  const archives = (formats.archives || []).length ? ' (arşivler açılır)' : '';
  const fallback = formats.fallback?.available
    ? ' Genel dönüştürücü kurulu: diğer türler de metin olarak okunur.'
    : '';
  return `Okunan türler: ${list || '—'}${archives}.${fallback} Aynı adla yüklenen dosya yeni sürüm olur.`;
}

// ---- pencereler --------------------------------------------------------- //

async function openSamples(slug) {
  let catalog;
  try { catalog = await get('/api/samples'); } catch (error) { toastError(error); return; }

  const addButton = (group, file) => {
    const btn = button('Ekle', async () => {
      btn.disabled = true;
      try {
        const result = await post(p`/api/projects/${slug}/documents/sample`, { group, name: file.name });
        btn.textContent = 'Eklendi ✓';
        toast(`${file.name} projeye eklendi`, 'ok');
        if (result.job) trackJob(result.job, { title: `${file.name} okunuyor` });
      } catch (error) {
        btn.disabled = false;
        toastError(error);
      }
    }, { kind: 'small' });
    return btn;
  };

  const sampleRow = (group, file, detail) => h('li', {},
    h('div', { class: 'grow' }, h('b', { text: file.name }),
      h('div', { class: 'small muted', text: [file.label, detail, fmtSize(file.size)].filter(Boolean).join(' · ') })),
    h('a', { class: 'btn small', href: p`/api/samples/${group}/${file.name}`, download: '' }, 'İndir'),
    addButton(group, file));

  const sections = [];
  if (catalog.formats?.length) {
    sections.push(h('h3', { text: 'Biçim örnekleri' }),
      h('p', { class: 'small muted', text: 'Her desteklenen biçimden küçük bir dosya; okuyucunun nasıl çalıştığını görmek için.' }),
      h('ul', { class: 'list' }, catalog.formats.map((file) => sampleRow('bicim', file, file.description))));
  }
  for (const scenario of catalog.scenarios || []) {
    sections.push(h('h3', { text: `Senaryo: ${scenario.title}` }),
      scenario.description ? h('p', { class: 'small muted', text: scenario.description }) : null,
      h('ul', { class: 'list' }, (scenario.files || []).map((file) => sampleRow(scenario.id, file, null))));
  }
  const body = sections.length ? h('div', {}, sections) : empty('Örnek dosya bulunamadı.');
  await modal({ title: 'Örnek doküman ekle', body, wide: true, actions: [{ label: 'Kapat', value: null }] });
}

async function showVersions(slug, doc) {
  let detail;
  try { detail = await get(p`/api/projects/${slug}/documents/${doc.name}`); } catch (error) { toastError(error); return; }
  const versions = [...(detail.versions || [])].reverse();
  const downloadUrl = p`/api/projects/${slug}/documents/${doc.name}/download`;
  const view = (href) => (event) => { event.preventDefault(); closeModal(); location.hash = href; };
  const rows = versions.map((v, index) => {
    const href = link('p', slug, 'dokuman', doc.name) + (index === 0 ? '' : `?version=${encodeURIComponent(v.n)}`);
    return h('tr', {},
      h('td', { class: 'nowrap' }, `v${v.n} `, index === 0 ? badge('güncel', 'ok') : null),
      h('td', {}, when(v.zaman)),
      h('td', { text: v.yukleyen || '—' }),
      h('td', { class: 'num', text: fmtSize(v.boyut) }),
      h('td', { class: 'mono small', title: v.sha256 || '', text: (v.sha256 || '').slice(0, 12) || '—' }),
      h('td', { class: 'nowrap' },
        h('a', { href: `${downloadUrl}?version=${encodeURIComponent(v.n)}`, download: '' }, 'İndir'), ' · ',
        h('a', { href, onclick: view(href) }, 'İncele')));
  });
  const body = h('div', { class: 'stack' },
    h('p', { class: 'small muted', text: 'Model her zaman güncel (son) sürümden kurulur. Eski sürümler indirilebilir ve incelenebilir.' }),
    h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
      h('thead', {}, h('tr', {}, ['Sürüm', 'Yüklenme', 'Yükleyen', 'Boyut', 'SHA-256', ''].map((t, i) => h('th', { class: i === 3 ? 'num' : '', text: t })))),
      h('tbody', {}, rows))));
  await modal({ title: `Sürümler: ${doc.name}`, body, wide: true, actions: [{ label: 'Kapat', value: null }] });
}

async function forceReader(slug, doc, formats, reload) {
  const current = doc.forced_reader || '';
  const options = [{ value: '', label: 'Otomatik (uzantı ve içerikten)' },
    ...(formats?.formats || []).map((f) => ({
      value: f.key, label: `${f.label} — ${f.group}${f.available ? '' : ' (ek paket gerekli)'}`,
    }))];
  const detected = doc.report ? `Şu an: ${doc.report.reader || '—'} (${DETECTED[doc.report.detected]?.[0] || doc.report.detected || '—'}).` : '';
  const values = await formDialog({
    title: `Okuyucu: ${doc.name}`,
    intro: `${detected} Yanlış okuyucu seçildiyse (ör. .txt uzantılı bir JIL dosyası) burada zorlayabilirsiniz. Değişiklik proje ayarlarına yazılır ve dokümanlar yeniden okunur.`,
    fields: [{ name: 'reader', label: 'Okuyucu', type: 'select', value: current, options, wide: true }],
    submitLabel: 'Uygula',
  });
  if (!values) return;
  const reader = values.reader || null;
  if ((reader || '') === current) { toast('Okuyucu değişmedi', 'info'); return; }
  try {
    const result = await put(p`/api/projects/${slug}/documents/${doc.name}/reader`, { reader });
    toast(reader ? `${doc.name}: okuyucu zorlandı` : `${doc.name}: okuyucu otomatiğe döndü`, 'ok');
    reload();
    if (result.job) trackJob(result.job, { title: 'Dokümanlar yeniden okunuyor' });
  } catch (error) { toastError(error); }
}

async function removeDocument(slug, doc, reload) {
  const versions = doc.versions > 1 ? ` ve ${doc.versions} sürümü` : '';
  if (!await confirmDialog(`"${doc.name}" dokümanı${versions} silinsin mi? Dokümanlar yeniden okunur; bu dokümandan gelen nesne ve bağlar modelden kalkar. Elle düzeltmeler silinmez.`,
    { title: 'Dokümanı sil', danger: true, confirmLabel: 'Sil' })) return;
  try {
    const result = await del(p`/api/projects/${slug}/documents/${doc.name}`);
    toast(`${doc.name} silindi`, 'ok');
    reload();
    if (result.job) trackJob(result.job, { title: 'Dokümanlar yeniden okunuyor' });
  } catch (error) { toastError(error); }
}

function showUploadErrors(errors) {
  return modal({
    title: 'Bazı dosyalar kaydedilmedi',
    body: h('div', { class: 'stack' },
      h('p', { class: 'muted', text: 'Aşağıdaki dosyalar reddedildi; diğerleri kaydedildi.' }),
      h('ul', { class: 'list' }, errors.map((text) => h('li', { text })))),
    actions: [{ label: 'Tamam', value: null, kind: 'primary' }],
  });
}

// ---- ekran -------------------------------------------------------------- //

export async function render(root, { slug }) {
  const [documents, formats, settings] = await Promise.all([
    get(p`/api/projects/${slug}/documents`),
    get('/api/formats').catch(() => null),
    get(p`/api/projects/${slug}/settings`).catch(() => null),
  ]);
  const info = store.projectInfo;
  const yz = settings?.values?.yz || null;
  const llmEnabled = Boolean(yz?.etkin);
  const editable = canEdit();

  let table = null;
  let emptyCard = null;
  const reload = async () => {
    try {
      const rows = await get(p`/api/projects/${slug}/documents`);
      if (!table) return;
      table.setRows(rows);
      if (rows.length && emptyCard) {
        emptyCard.remove();
        emptyCard = null;
        table.el.classList.remove('hidden');
      }
    } catch (error) { toastError(error); }
  };

  const rebuild = async (llm) => {
    if (llm) {
      const profile = yz?.profiller?.[yz.profil] || {};
      const message = `Doküman metinleri yapılandırılmış dil modeline gönderilecek (profil: ${yz?.profil || '—'}, `
        + `model: ${profile.model || '—'}, adres: ${profile.base_url || '—'}). `
        + `Hassas değer maskeleme ${yz?.maske ? 'açık' : 'KAPALI'}. Devam edilsin mi?`;
      if (!await confirmDialog(message, { title: 'LLM ile oku', confirmLabel: 'Gönder ve oku' })) return;
    }
    try {
      const job = await post(p`/api/projects/${slug}/build`, { llm: Boolean(llm) });
      trackJob(job, { title: llm ? 'LLM ile yeniden okunuyor' : 'Dokümanlar yeniden okunuyor' });
    } catch (error) { toastError(error); }
  };

  root.append(pageHeader({
    title: 'Dokümanlar',
    subtitle: 'Yüklenen dokümanlar okunur; model her okumada dokümanlardan yeniden kurulur, elle düzeltmeler üstüne uygulanır.',
    crumbs: [{ label: 'Projeler', href: '#/projeler' }, { label: info?.ad || slug, href: link('p', slug) }, { label: 'Dokümanlar' }],
    actions: [
      editable ? button('Örnek ekle', () => openSamples(slug)) : null,
      editable ? button('Yeniden oku', () => rebuild(false), { title: 'Dokümanları LLM kullanmadan, kurallarla yeniden okur' }) : null,
      editable && llmEnabled ? button('LLM ile oku', () => rebuild(true), { kind: 'primary', title: 'Kuralla çözülemeyen paragraf ve tabloları dil modeline de gönderir' }) : null,
    ],
  }));

  if (info?.jobs?.some((j) => j.state === 'queued' || j.state === 'running')) {
    root.append(h('div', { class: 'banner info' }, h('span', { class: 'spinner' }),
      h('span', { class: 'grow', text: 'Arka planda bir okuma işi sürüyor; bitince liste yenilenir.' })));
  }
  if (info?.summary?.stale) {
    root.append(h('div', { class: 'banner warn' },
      h('span', { class: 'grow', text: 'Dokümanlar ya da okuma ayarları son okumadan sonra değişti; gösterilen sonuçlar eski olabilir.' }),
      editable ? button('Şimdi yeniden oku', () => rebuild(false), { kind: 'primary' }) : null));
  }

  if (editable) {
    let busy = false;
    const status = h('div', { class: 'small muted', role: 'status', 'aria-live': 'polite' });
    const onFiles = async (files) => {
      if (busy) { toast('Önceki yükleme sürüyor', 'warn'); return; }
      if (files.length > MAX_UPLOAD_FILES) { toast(`Tek seferde en çok ${MAX_UPLOAD_FILES} dosya yüklenebilir`, 'warn'); return; }
      busy = true;
      status.textContent = `${files.length} dosya yükleniyor…`;
      const form = new FormData();
      for (const file of files) form.append('files', file, file.name);
      try {
        const result = await upload(p`/api/projects/${slug}/documents`, form);
        const stored = result.stored || [];
        const changed = stored.filter((s) => !s.unchanged);
        const same = stored.filter((s) => s.unchanged);
        if (changed.length) toast(`${changed.length} doküman kaydedildi${changed.some((s) => s.version > 1) ? ' (aynı adlılar yeni sürüm oldu)' : ''}`, 'ok');
        if (same.length) toast(`${same.map((s) => s.name).join(', ')}: içerik aynı, yeni sürüm oluşmadı`, 'info', 7000);
        await reload();
        if (result.job) trackJob(result.job, { title: 'Yüklenen dokümanlar okunuyor' });
        if (result.errors?.length) await showUploadErrors(result.errors);
      } catch (error) {
        toastError(error);
      } finally {
        busy = false;
        status.textContent = '';
      }
    };
    root.append(h('div', { class: 'card', style: { marginBottom: '14px' } },
      dropzone({ onFiles, hint: acceptedHint(formats) }), status));
  }

  const columns = [
    { key: 'name', label: 'Doküman', render: (d) => h('div', { class: 'documents-name' },
      h('a', { href: link('p', slug, 'dokuman', d.name), text: d.name }),
      d.forced_reader ? h('div', { class: 'small muted', text: `okuyucu zorlandı: ${d.forced_reader}` }) : null,
      innerList(d)) },
    { key: 'reader', label: 'Biçim', sort: (d) => d.report?.reader || '', render: (d) => h('span', { class: 'nowrap', text: d.report?.reader || '—' }) },
    { key: 'status', label: 'Durum', sort: (d) => d.report?.status || 'okunmadı', render: (d) => docStatusBadge(d.report?.status) },
    { key: 'detected', label: 'Tespit', sort: (d) => DETECTED[d.report?.detected]?.[0] || '', render: (d) => detectedBadge(d.report?.detected) },
    { key: 'counts', label: 'Çıkarılan', sort: (d) => (d.report ? d.report.datasets + d.report.processes + d.report.flows : -1), render: (d) => countsCell(d.report) },
    { key: 'version', label: 'Sürüm', num: true, sort: (d) => d.version, render: (d) => h('span', { class: 'nowrap', text: d.versions > 1 ? `v${d.version} / ${d.versions}` : `v${d.version}` }) },
    { key: 'uploaded_at', label: 'Yükleyen', render: (d) => h('div', {}, h('div', { text: d.uploaded_by || '—' }), h('div', { class: 'small muted' }, when(d.uploaded_at))) },
    { key: 'size', label: 'Boyut', num: true, render: (d) => h('span', { class: 'nowrap', text: fmtSize(d.size) }) },
    { key: 'notes', label: 'Not', wrap: true, sort: (d) => (d.report?.notes || [])[0] || '', render: (d) => notesCell(d.report) },
    { key: 'actions', label: '', sort: false, render: (d) => h('div', { class: 'row documents-actions' },
      h('button', { class: 'icon-btn', type: 'button', title: 'İndir', 'aria-label': `${d.name} indir`,
        onclick: () => download(p`/api/projects/${slug}/documents/${d.name}/download`) }, '⤓'),
      h('button', { class: 'icon-btn', type: 'button', title: 'Sürümler', 'aria-label': `${d.name} sürümleri`,
        onclick: () => showVersions(slug, d) }, '🕘'),
      editable ? h('button', { class: 'icon-btn', type: 'button', title: 'Okuyucuyu seç', 'aria-label': `${d.name} okuyucusunu seç`,
        onclick: () => forceReader(slug, d, formats, reload) }, '⚙') : null,
      editable ? h('button', { class: 'icon-btn', type: 'button', title: 'Sil', 'aria-label': `${d.name} sil`,
        onclick: () => removeDocument(slug, d, reload) }, '🗑') : null) },
  ];

  table = dataTable({ rows: documents, rowKey: (d) => d.name, columns, empty: 'Doküman yok' });
  if (!documents.length) {
    // Yükleme sonrası liste dolunca boş kartın yerini tablo alır (bkz. reload).
    emptyCard = h('div', { class: 'card' }, empty('Bu projede henüz doküman yok.', editable
      ? h('div', { class: 'row', style: { justifyContent: 'center' } },
        button('Örnek ekle', () => openSamples(slug), { kind: 'primary' }),
        h('a', { class: 'btn', href: '#/ornekler' }, 'Örnekler ve şablonlar'))
      : 'Bir düzenleyicinin doküman yüklemesini bekleyin.'));
    root.append(emptyCard);
    table.el.classList.add('hidden');
  }
  root.append(table.el);
}
