// Proje panosu: sağlık özeti, eski model uyarısı, sorunlu dokümanlar, sahipsiz düzeltmeler, son değişiklikler.

import { get, p, post } from '../core/api.js';
import { h } from '../core/dom.js';
import { link } from '../core/router.js';
import { canEdit, label } from '../core/store.js';
import { badge, button, docStatusBadge, empty, pageHeader, stat, toastError, trackJob, when } from '../core/ui.js';

export const refreshOnChange = true;

const ACTIONS = {
  proje_olustur: 'projeyi oluşturdu', belge_yukle: 'doküman yükledi', belge_sil: 'doküman sildi',
  yeniden_oku: 'dokümanları yeniden okuttu', duzeltme: 'düzeltme yaptı', geri_al: 'düzeltmeyi geri aldı',
  yinele: 'düzeltmeyi yineledi', ayar: 'ayar değiştirdi', ayar_ice_aktar: 'ayar profilini içe aktardı',
  diyagram_uret: 'diyagram üretti', calistirma_sil: 'çalıştırma sildi', proje_guncelle: 'projeyi güncelledi',
};

export function describeAudit(entry) {
  const data = entry.veri || {};
  let detail = '';
  if (entry.islem === 'duzeltme' || entry.islem === 'geri_al' || entry.islem === 'yinele') {
    const op = data.islem_kaydi || {};
    const target = op.hedef ? Object.values(op.hedef).flat().join(' → ') : '';
    detail = `${label.op(op.islem)}: ${target}`;
  } else if (entry.islem === 'belge_yukle') detail = `${data.belge} (sürüm ${data.surum})`;
  else if (entry.islem === 'belge_sil') detail = data.belge;
  else if (entry.islem === 'yeniden_oku') detail = data.llm ? 'LLM ile' : '';
  return { action: ACTIONS[entry.islem] || entry.islem, detail };
}

export async function render(root, { slug }) {
  const data = await get(p`/api/projects/${slug}/dashboard`);
  const s = data.summary;
  const base = (...parts) => link('p', slug, ...parts);

  const rebuild = async (llm = false) => {
    try {
      const job = await post(p`/api/projects/${slug}/build`, { llm });
      trackJob(job, { title: llm ? 'LLM ile yeniden okunuyor' : 'Dokümanlar yeniden okunuyor' });
    } catch (error) { toastError(error); }
  };

  root.append(pageHeader({
    title: data.ad, subtitle: data.aciklama || null,
    crumbs: [{ label: 'Projeler', href: '#/projeler' }, { label: 'Pano' }],
    actions: [
      h('a', { class: 'btn', href: base('dokumanlar') }, 'Doküman yükle'),
      canEdit() ? button('Yeniden oku', () => rebuild(false)) : null,
      h('a', { class: 'btn primary', href: base('diyagramlar') }, 'Diyagramlar'),
    ],
  }));

  if (data.jobs?.some((j) => j.state === 'queued' || j.state === 'running')) {
    root.append(h('div', { class: 'banner info' }, h('span', { class: 'spinner' }), h('span', { class: 'grow', text: 'Arka planda bir iş sürüyor; bitince pano yenilenir.' })));
  }
  if (s.stale) {
    root.append(h('div', { class: 'banner warn' },
      h('span', { class: 'grow', text: 'Dokümanlar ya da okuma ayarları son okumadan sonra değişti; gösterilen model eski olabilir.' }),
      canEdit() ? button('Şimdi yeniden oku', () => rebuild(false), { kind: 'primary' }) : null));
  }
  if (!s.documents.total) {
    root.append(h('div', { class: 'banner info' }, h('span', { class: 'grow', text: 'Bu projede henüz doküman yok. Başlamak için doküman yükleyin ya da örnek bir dosya ekleyin.' }),
      h('a', { class: 'btn primary', href: base('dokumanlar') }, 'Doküman yükle'), h('a', { class: 'btn', href: '#/ornekler' }, 'Örnekler')));
  }

  const review = s.review || {};
  root.append(h('div', { class: 'grid-3', style: { marginBottom: '14px' } },
    stat(s.documents.total, 'doküman', { href: base('dokumanlar') }),
    stat(s.stats.datasets, 'tablo / dosya', { href: base('tablolar') }),
    stat(s.stats.processes, 'süreç / job', { href: base('surecler') }),
    stat(s.stats.links, 'tablo düzeyi bağ', { href: base('tablolar') }),
    stat(review.toplam || 0, 'onay bekleyen', { href: base('onay'), kind: review.toplam ? 'warn' : 'ok' }),
    stat(s.findings.error, 'doğrulama hatası', { href: base('bulgular'), kind: s.findings.error ? 'bad' : 'ok' }),
    stat(s.findings.warning, 'uyarı', { href: base('bulgular'), kind: s.findings.warning ? 'warn' : '' }),
    stat(s.documents.problems, 'sorunlu doküman', { href: base('dokumanlar'), kind: s.documents.problems ? 'warn' : 'ok' }),
    stat(s.corrections.total, 'elle düzeltme', { href: base('gunluk') }),
    stat(s.corrections.orphans, 'sahipsiz düzeltme', { href: base('gunluk'), kind: s.corrections.orphans ? 'warn' : 'ok' }),
  ));

  const left = h('div', { class: 'stack' });
  const right = h('div', { class: 'stack' });

  // Onay kuyruğu dökümü
  const reviewRows = [['bag', 'taslak bağ'], ['nesne', 'bağsız taslak nesne'], ['birlestir', 'birleştirme önerisi'],
    ['paragraf', 'insan bakmalı paragraf'], ['tablo', 'tanınmayan tablo']].filter(([k]) => review[k]);
  left.append(h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'Onay kuyruğu' }),
    h('a', { class: 'btn small', href: base('onay') }, 'Kuyruğa git')),
  reviewRows.length
    ? h('ul', { class: 'list' }, reviewRows.map(([key, text]) => h('li', {}, h('b', { text: String(review[key]) }), text)))
    : h('p', { class: 'muted', text: 'Onay bekleyen öğe yok.' })));

  // Sorunlu dokümanlar
  left.append(h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'Sorunlu dokümanlar' }),
    h('a', { class: 'btn small', href: base('dokumanlar') }, 'Dokümanlar')),
  data.problems.length
    ? h('ul', { class: 'list' }, data.problems.map((d) => h('li', {}, docStatusBadge(d.status),
      h('a', { href: base('dokuman', d.container || d.file), text: d.file }),
      h('span', { class: 'small muted grow', text: (d.notes || [])[0] || '' }))))
    : h('p', { class: 'muted', text: 'Tüm dokümanlar okundu.' })));

  if (data.orphans.length) {
    left.append(h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'Sahipsiz düzeltmeler' }),
      h('a', { class: 'btn small', href: base('gunluk') }, 'Günlüğe git')),
    h('p', { class: 'small muted', text: 'Hedefi artık modelde bulunamayan elle düzeltmeler. Doküman değiştiyse yeniden bağlayın ya da silin.' }),
    h('ul', { class: 'list' }, data.orphans.map((o) => h('li', {}, badge(o.id, 'warn'), h('span', { text: `${label.op(o.islem)} — ${o.mesaj}` }))))));
  }

  // Son okuma
  const build = s.build || {};
  right.append(h('div', { class: 'card' }, h('h2', { text: 'Son okuma' }),
    h('dl', { class: 'kv' },
      h('dt', { text: 'Zaman' }), h('dd', {}, when(build.built_at)),
      h('dt', { text: 'Okutan' }), h('dd', { text: build.built_by || '—' }),
      h('dt', { text: 'LLM' }), h('dd', { text: build.llm ? 'kullanıldı' : 'kullanılmadı' }),
      h('dt', { text: 'Doküman' }), h('dd', { text: `${s.documents.read} okundu, ${s.documents.problems} sorunlu` }),
      data.last_run ? [h('dt', { text: 'Son diyagram' }), h('dd', {}, h('a', { href: base('diyagramlar', data.last_run.id), text: `${data.last_run.ready}/${data.last_run.total} hazır` }), ' · ', when(data.last_run.created_at))] : null)));

  // Son değişiklikler
  right.append(h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', { text: 'Son değişiklikler' }),
    h('a', { class: 'btn small', href: base('gunluk') }, 'Tümü')),
  data.recent.length
    ? h('ul', { class: 'list' }, data.recent.map((entry) => {
      const { action, detail } = describeAudit(entry);
      return h('li', { style: { display: 'block' } }, h('b', { text: entry.kim }), ` ${action}`,
        detail ? h('div', { class: 'small muted', text: detail }) : null, h('div', { class: 'small muted' }, when(entry.zaman)));
    }))
    : empty('Henüz değişiklik yok.')));

  root.append(h('div', { class: 'grid-2' }, left, right));
}
