// Sistem durumu: sürüm, Node.js ve diyagram motoru, eklenti hataları, LLM profilleri, biçim kataloğu.

import { get } from '../core/api.js';
import { h } from '../core/dom.js';
import { isAdmin } from '../core/store.js';
import { badge, empty, pageHeader } from '../core/ui.js';

/** Biçim kataloğu (/api/formats `formats`, /api/guide `formats`): gruplara ayrılmış tablolar. */
export function formatCatalog(items) {
  const groups = new Map();
  for (const item of items || []) {
    const key = item.group || 'Diğer';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  if (!groups.size) return empty('Kayıtlı biçim yok.');
  const out = [];
  for (const [group, list] of groups) {
    out.push(h('h3', { text: group }), h('div', { class: 'card flush system-formats' }, h('div', { class: 'table-wrap' },
      h('table', { class: 'data' },
        h('thead', {}, h('tr', {}, ['Biçim', 'Uzantılar', 'Açıklama', 'Durum'].map((t) => h('th', { text: t })))),
        h('tbody', {}, list.map((item) => h('tr', {},
          h('td', {}, h('b', { text: item.label }), h('div', { class: 'small muted mono', text: item.key })),
          h('td', {},
            item.suffixes?.length ? h('span', { class: 'mono', text: item.suffixes.join(' ') }) : h('span', { class: 'muted', text: 'içerikten tanınır' }),
            item.sniffs ? h('div', {}, badge('içerik tespiti', 'info')) : null),
          h('td', { class: 'wrap' }, item.description || '—',
            item.preview === false ? h('div', { class: 'small muted', text: 'önizleme yok (metin olarak gösterilir)' }) : null),
          h('td', {},
            item.available ? badge('kullanılabilir', 'ok') : badge('ek paket gerekli', 'warn'),
            item.extra ? h('div', { class: 'small muted mono', title: 'Ek paket', text: `pip install "dwhflow[${item.extra}]"` }) : null))))))));
  }
  return out;
}

export function unsupportedList(items) {
  if (!items?.length) return h('p', { class: 'muted', text: 'Yok.' });
  return h('ul', { class: 'list' }, items.map((u) => h('li', {}, h('b', { class: 'mono', text: u.suffix }), h('span', { class: 'small', text: u.message }))));
}

function engineStatus(engine) {
  if (engine.ok) return badge('hazır', 'ok');
  if (engine.installed) return badge('kurulu, çalışmıyor', 'bad');
  return badge('kurulu değil', 'bad');
}

export async function render(root) {
  const [system, formats] = await Promise.all([get('/api/system'), get('/api/formats')]);
  const engine = system.engine || {};
  const node = system.node || {};

  root.append(pageHeader({
    title: 'Sistem durumu',
    subtitle: 'Sürüm, diyagram motoru, okunan biçimler ve eklenti durumu.',
  }));

  const app = h('div', { class: 'card' }, h('h2', { text: 'Uygulama' }),
    h('dl', { class: 'kv' },
      h('dt', { text: 'DWHFlow sürümü' }), h('dd', { class: 'mono', text: system.version || '—' }),
      h('dt', { text: 'Kayıtlı biçim' }), h('dd', { text: String(system.formats ?? '—') }),
      h('dt', { text: 'LLM profilleri' }), h('dd', {}, (system.llm_profiles || []).length
        ? (system.llm_profiles || []).map((name) => badge(name, 'muted'))
        : h('span', { class: 'muted', text: 'tanımlı değil' })),
      system.workspace !== undefined ? [h('dt', { text: 'Çalışma alanı' }), h('dd', { class: 'mono', text: system.workspace })] : null,
      system.users !== undefined ? [h('dt', { text: 'Kullanıcı sayısı' }), h('dd', {}, isAdmin() ? h('a', { href: '#/kullanicilar', text: String(system.users) }) : String(system.users))] : null));

  const diagram = h('div', { class: 'card' }, h('h2', { text: 'Diyagram motoru' }),
    h('dl', { class: 'kv' },
      h('dt', { text: 'Node.js' }), h('dd', {}, node.path
        ? [h('span', { class: 'mono', text: node.path }), node.major ? ` (sürüm ${node.major})` : '']
        : badge('bulunamadı', 'bad')),
      h('dt', { text: 'Motor' }), h('dd', {}, engineStatus(engine), engine.version ? h('span', { class: 'mono', text: ` ${engine.version}` }) : null)),
    engine.error ? h('div', { class: 'banner bad', style: { marginTop: '10px', marginBottom: '0' } }, h('span', { class: 'grow', text: engine.error })) : null,
    !engine.ok ? h('p', { class: 'small muted', text: 'Motor hazır değilken dokümanlar okunur ve model incelenebilir; yalnız diyagram üretilemez.' }) : null);

  const plugins = h('div', { class: 'card' }, h('h2', { text: 'Biçim eklentileri' }),
    (system.plugin_errors || []).length
      ? [h('p', { class: 'small muted', text: 'Aşağıdaki eklentiler yüklenemedi; diğer biçimler etkilenmez.' }),
        h('ul', { class: 'list' }, system.plugin_errors.map((text) => h('li', {}, badge('hata', 'bad'), h('span', { class: 'mono small', text }))))]
      : h('p', { class: 'muted', text: 'Tüm biçim eklentileri yüklendi.' }));

  root.append(h('div', { class: 'grid-2', style: { marginBottom: '14px' } }, app, diagram), plugins);

  const fallback = formats.fallback;
  root.append(h('div', { class: 'card', style: { marginTop: '14px' } },
    h('h2', { text: 'Okunan biçimler' }),
    h('p', { class: 'small muted', text: 'Okuyucu önce ayarlarda zorlanan biçime, sonra (genel uzantılarda) dosya içeriğine, sonra uzantıya bakılarak seçilir.' }),
    formatCatalog(formats.formats),
    h('h3', { text: 'Arşivler' }),
    (formats.archives || []).length
      ? h('div', { class: 'row' }, formats.archives.map((suffix) => badge(suffix, 'muted')), h('span', { class: 'small muted', text: 'açılır, içindeki dosyalar ayrı ayrı okunur' }))
      : h('p', { class: 'muted', text: 'Yok.' }),
    h('h3', { text: 'Genel dönüştürücü (yedek okuyucu)' }),
    fallback
      ? h('div', { class: 'row' }, h('b', { text: fallback.label }), fallback.available ? badge('kurulu', 'ok') : badge('kurulu değil', 'warn'),
        h('span', { class: 'small muted', text: fallback.description || '' }),
        !fallback.available && fallback.extra ? h('span', { class: 'small mono', text: `pip install "dwhflow[${fallback.extra}]"` }) : null)
      : h('p', { class: 'muted', text: 'Tanımlı değil: özel okuyucusu olmayan türler "desteklenmiyor" olarak raporlanır.' }),
    h('h3', { text: 'Tanınan ama okunamayan türler' }),
    unsupportedList(formats.unsupported)));
}
