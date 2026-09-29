// Hash yönlendirici: #/p/kart/tablolar/stg_musteri → {screen, params}.
// Her ekran js/screens/<screen>.js modülüdür ve `render(root, params)` dışa aktarır.

export const ROUTES = [
  { path: '/projeler', screen: 'projects', title: 'Projeler' },
  { path: '/ornekler', screen: 'samples', title: 'Örnekler ve şablonlar' },
  { path: '/ayarlar', screen: 'settings', title: 'Genel ayarlar', admin: true },
  { path: '/kullanicilar', screen: 'users', title: 'Kullanıcılar', admin: true },
  { path: '/sistem', screen: 'system', title: 'Sistem' },
  { path: '/yz', screen: 'llm', title: 'Yapay zekâ' },
  { path: '/p/:slug', screen: 'dashboard', title: 'Pano', project: true, nav: 'pano' },
  { path: '/p/:slug/dokumanlar', screen: 'documents', title: 'Dokümanlar', project: true, nav: 'dokumanlar' },
  { path: '/p/:slug/dokuman/:name', screen: 'document', title: 'Doküman inceleme', project: true, nav: 'dokumanlar' },
  { path: '/p/:slug/tablolar', screen: 'datasets', title: 'Tablolar ve dosyalar', project: true, nav: 'model' },
  { path: '/p/:slug/tablolar/:id', screen: 'datasets', title: 'Tablolar ve dosyalar', project: true, nav: 'model' },
  { path: '/p/:slug/surecler', screen: 'processes', title: 'Süreçler', project: true, nav: 'model' },
  { path: '/p/:slug/surecler/:id', screen: 'processes', title: 'Süreçler', project: true, nav: 'model' },
  { path: '/p/:slug/sttm', screen: 'sttm', title: 'STTM', project: true, nav: 'model' },
  { path: '/p/:slug/onay', screen: 'review', title: 'Onay kuyruğu', project: true, nav: 'onay' },
  { path: '/p/:slug/bulgular', screen: 'findings', title: 'Bulgular', project: true, nav: 'bulgular' },
  { path: '/p/:slug/diyagramlar', screen: 'diagrams', title: 'Diyagramlar', project: true, nav: 'diyagramlar' },
  { path: '/p/:slug/diyagramlar/:run', screen: 'diagrams', title: 'Diyagramlar', project: true, nav: 'diyagramlar' },
  { path: '/p/:slug/karsilastir', screen: 'compare', title: 'Karşılaştır', project: true, nav: 'karsilastir' },
  { path: '/p/:slug/gunluk', screen: 'audit', title: 'Günlük', project: true, nav: 'gunluk' },
  { path: '/p/:slug/ayarlar', screen: 'project-settings', title: 'Proje ayarları', project: true, nav: 'ayarlar' },
];

export function parse(hash) {
  const raw = (hash || '').replace(/^#/, '') || '/projeler';
  const [pathPart, queryPart = ''] = raw.split('?');
  const segments = pathPart.split('/').filter(Boolean);
  for (const route of ROUTES) {
    const pattern = route.path.split('/').filter(Boolean);
    if (pattern.length !== segments.length) continue;
    const params = {};
    const ok = pattern.every((part, i) => {
      if (part.startsWith(':')) { params[part.slice(1)] = decodeURIComponent(segments[i]); return true; }
      return part === segments[i];
    });
    if (ok) return { route, params, query: Object.fromEntries(new URLSearchParams(queryPart)) };
  }
  return null;
}

/** Yol parçalarını kodlayarak bağlantı üretir: link('p', slug, 'tablolar', id) → '#/p/kart/tablolar/x' */
export function link(...parts) {
  return '#/' + parts.filter((x) => x !== undefined && x !== null && x !== '').map(encodeURIComponent).join('/');
}

export function go(...parts) {
  location.hash = link(...parts);
}

let dirtyMessage = null;

/** Kaydedilmemiş değişiklik varsa sayfadan çıkmadan önce sorulur. */
export function setDirty(message) {
  dirtyMessage = message || null;
  window.onbeforeunload = dirtyMessage ? () => dirtyMessage : null;
}

export function confirmLeave() {
  if (!dirtyMessage) return true;
  const ok = window.confirm(`${dirtyMessage}\nYine de çıkılsın mı?`);
  if (ok) setDirty(null);
  return ok;
}
