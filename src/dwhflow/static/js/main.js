// Uygulama kabuğu: giriş/ilk kurulum, üst menü, yönlendirme, proje bağlamı, arama paleti.

import { ApiError, get, p, post } from './core/api.js';
import { $, append, clear, debounce, h } from './core/dom.js';
import { ROUTES, confirmLeave, link, parse } from './core/router.js';
import { can, emit, isAdmin, on, store } from './core/store.js';
import { dropdown, formDialog, loading, modal, toast, toastError } from './core/ui.js';

const app = $('#app');
const topbar = $('#topbar');
let currentModule = null;
let renderToken = 0;

// ---- giriş ve ilk kurulum ---------------------------------------------- //

function authScreen(state) {
  topbar.classList.add('hidden');
  clear(app);
  const setup = state.setup_required;
  const form = h('form', { class: 'stack' });
  const username = h('input', { type: 'text', name: 'username', autocomplete: 'username', required: true });
  const name = h('input', { type: 'text', name: 'name', autocomplete: 'name' });
  const password = h('input', { type: 'password', name: 'password', autocomplete: setup ? 'new-password' : 'current-password', required: true });
  const password2 = h('input', { type: 'password', name: 'password2', autocomplete: 'new-password' });
  const error = h('div', { class: 'small', style: { color: 'var(--bad)' } });
  append(form, [
    h('label', { class: 'field' }, h('span', { class: 'label', text: 'Kullanıcı adı' }), username),
    setup ? h('label', { class: 'field' }, h('span', { class: 'label', text: 'Ad soyad' }), name) : null,
    h('label', { class: 'field' }, h('span', { class: 'label', text: 'Parola' }), password),
    setup ? h('label', { class: 'field' }, h('span', { class: 'label', text: 'Parola (tekrar)' }), password2) : null,
    setup ? h('div', { class: 'small muted', text: 'En az 10 karakter; en az iki tür (harf, rakam, işaret).' }) : null,
    error,
    h('button', { class: 'btn primary', type: 'submit' }, setup ? 'Yöneticiyi oluştur' : 'Giriş yap'),
  ]);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    error.textContent = '';
    if (setup && password.value !== password2.value) { error.textContent = 'Parolalar eşleşmiyor.'; return; }
    try {
      const body = { username: username.value.trim(), password: password.value };
      if (setup) body.name = name.value.trim();
      await post(setup ? '/api/auth/setup' : '/api/auth/login', body);
      await boot();
    } catch (err) { error.textContent = err.detail || err.message; }
  });
  let note = null;
  if (setup && !state.setup_allowed) {
    form.querySelectorAll('input, button').forEach((el) => { el.disabled = true; });
    note = h('div', { class: 'banner warn' }, h('span', { text: 'İlk kurulum yalnız sunucu makinesindeki tarayıcıdan (http://127.0.0.1) ya da komut satırından yapılabilir: dwhflow kullanici ekle ADI --rol yonetici' }));
  }
  app.append(h('div', { class: 'auth-wrap' }, h('div', { class: 'card auth-card' },
    h('div', { class: 'brand' }, h('span', { class: 'mark', text: 'DWH' }), 'Flow'),
    h('p', { class: 'muted', text: setup ? 'İlk kurulum: yönetici hesabını oluşturun.' : 'Devam etmek için giriş yapın.' }),
    note, form)));
  username.focus();
}

// ---- üst menü ------------------------------------------------------------ //

async function projectsForMenu() {
  try { return await get('/api/projects'); } catch { return []; }
}

function navItem(text, href, { key, active, disabled, count } = {}) {
  return h('a', { class: ['nav-item', active === key ? 'active' : '', disabled ? 'disabled' : ''], href: disabled ? null : href },
    text, count ? h('span', { class: 'count', text: String(count) }) : null);
}

async function renderTopbar(route, params) {
  // Oturum bir istek sırasında düşerse (401) giriş ekranı açılmıştır; menü çizilmez.
  if (!store.user) return;
  const slug = params.slug || store.project;
  const info = store.projectInfo;
  const active = route?.nav || route?.screen;
  const has = Boolean(slug);
  const reviewCount = info?.summary?.review?.toplam || 0;
  const projects = await projectsForMenu();
  if (!store.user) return;
  topbar.classList.remove('hidden');
  clear(topbar);
  const projectName = info?.ad || projects.find((x) => x.slug === slug)?.ad;

  const projectMenu = dropdown(
    [h('span', { class: 'name', text: projectName || 'Proje seçin' }), ' ▾'],
    () => [
      { heading: 'Projeler' },
      ...projects.map((x) => ({ label: x.ad, href: link('p', x.slug) })),
      { separator: true },
      { label: 'Tüm projeler…', href: '#/projeler' },
    ],
    { className: 'nav-item project-switch' },
  );
  const modelMenu = dropdown(['Model', ' ▾'], [
    { label: 'Tablolar ve dosyalar', href: link('p', slug, 'tablolar') },
    { label: 'Süreçler ve job\'lar', href: link('p', slug, 'surecler') },
    { label: 'STTM (kolon eşlemeleri)', href: link('p', slug, 'sttm') },
  ], { className: `nav-item${active === 'model' ? ' active' : ''}${has ? '' : ' disabled'}` });
  const settingsMenu = dropdown(['Ayarlar', ' ▾'], () => [
    { label: 'Proje ayarları', href: link('p', slug, 'ayarlar'), hidden: !has },
    { label: 'Genel ayarlar', href: '#/ayarlar', hidden: !isAdmin() },
    { label: 'Kullanıcılar', href: '#/kullanicilar', hidden: !isAdmin() },
    { label: 'Yapay zekâ', href: '#/yz' },
    { label: 'Sistem durumu', href: '#/sistem' },
  ], { className: `nav-item${['ayarlar', 'settings', 'users', 'system', 'llm'].includes(active) ? ' active' : ''}` });
  const userMenu = dropdown([store.user.name || store.user.username, ' ▾'], [
    { heading: `${store.user.username} · ${({ izleyici: 'İzleyici', duzenleyici: 'Düzenleyici', yonetici: 'Yönetici' })[store.user.role]}` },
    { label: 'Parolamı değiştir', onClick: changePassword },
    { separator: true },
    { label: 'Çıkış', danger: true, onClick: logout },
  ], { alignRight: true });

  topbar.append(
    h('a', { class: 'brand', href: '#/projeler' }, h('span', { class: 'mark', text: 'DWH' }), 'Flow'),
    h('nav', { class: 'nav', 'aria-label': 'Ana menü' },
      projectMenu,
      h('span', { class: 'sep' }),
      navItem('Pano', link('p', slug), { key: 'pano', active, disabled: !has }),
      navItem('Dokümanlar', link('p', slug, 'dokumanlar'), { key: 'dokumanlar', active, disabled: !has }),
      modelMenu,
      navItem('Onay', link('p', slug, 'onay'), { key: 'onay', active, disabled: !has, count: has ? reviewCount : 0 }),
      navItem('Bulgular', link('p', slug, 'bulgular'), { key: 'bulgular', active, disabled: !has }),
      navItem('Diyagramlar', link('p', slug, 'diyagramlar'), { key: 'diyagramlar', active, disabled: !has }),
      navItem('Karşılaştır', link('p', slug, 'karsilastir'), { key: 'karsilastir', active, disabled: !has }),
      navItem('Günlük', link('p', slug, 'gunluk'), { key: 'gunluk', active, disabled: !has }),
    ),
    h('span', { class: 'grow' }),
    h('nav', { class: 'nav', 'aria-label': 'Araçlar' },
      h('button', { class: 'nav-item search-btn', type: 'button', onclick: openPalette, disabled: !has, title: 'Ara (Ctrl+K)' },
        'Ara', h('kbd', { text: 'Ctrl+K' })),
      navItem('Örnekler', '#/ornekler', { key: 'samples', active }),
      settingsMenu,
      userMenu,
    ),
  );
}

async function changePassword() {
  const values = await formDialog({
    title: 'Parolamı değiştir',
    fields: [
      { name: 'current', label: 'Mevcut parola', type: 'password', required: true },
      { name: 'new', label: 'Yeni parola', type: 'password', required: true, hint: 'En az 10 karakter, iki tür' },
      { name: 'again', label: 'Yeni parola (tekrar)', type: 'password', required: true },
    ],
    validate: (v) => (v.new !== v.again ? 'Yeni parolalar eşleşmiyor' : null),
  });
  if (!values) return;
  try {
    await post('/api/auth/password', { current: values.current, new: values.new });
    toast('Parola değişti; diğer oturumlarınız kapatıldı.', 'ok');
  } catch (error) { toastError(error); }
}

async function logout() {
  try { await post('/api/auth/logout', {}); } catch { /* oturum zaten düşmüş olabilir */ }
  store.user = null;
  await boot();
}

// ---- arama paleti (Ctrl+K) --------------------------------------------- //

function openPalette() {
  const slug = store.project;
  if (!slug) return;
  const input = h('input', { type: 'search', placeholder: 'Tablo, süreç ya da doküman adı…', 'aria-label': 'Ara' });
  const results = h('div', { class: 'palette-results' });
  let items = [];
  let index = 0;
  const target = (item) => ({
    dataset: link('p', slug, 'tablolar', item.id),
    process: link('p', slug, 'surecler', item.id),
    document: link('p', slug, 'dokuman', item.id),
  })[item.type];
  const kinds = { dataset: 'tablo', process: 'süreç', document: 'doküman' };
  const draw = () => {
    clear(results);
    items.forEach((item, i) => results.append(h('a', { href: target(item), class: i === index ? 'sel' : '' },
      h('span', { class: 'badge muted', text: kinds[item.type] }), h('span', { class: 'grow', text: item.name }),
      h('span', { class: 'small muted', text: item.detail || '' }))));
    if (!items.length && input.value.trim().length >= 2) results.append(h('div', { class: 'empty', text: 'Sonuç yok' }));
  };
  const search = debounce(async () => {
    const q = input.value.trim();
    if (q.length < 2) { items = []; draw(); return; }
    try { items = await get(p`/api/projects/${slug}/search?q=${q}`); index = 0; draw(); } catch (error) { toastError(error); }
  }, 180);
  input.addEventListener('input', search);
  const body = h('div', {}, input, results);
  const closer = modal({ title: 'Ara', body, actions: [{ label: 'Kapat', value: null }] });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown') { index = Math.min(index + 1, items.length - 1); draw(); event.preventDefault(); }
    if (event.key === 'ArrowUp') { index = Math.max(index - 1, 0); draw(); event.preventDefault(); }
    if (event.key === 'Enter' && items[index]) { location.hash = target(items[index]); document.querySelector('.modal-backdrop')?.remove(); }
  });
  results.addEventListener('click', () => setTimeout(() => document.querySelector('.modal-backdrop')?.remove(), 0));
  return closer;
}

document.addEventListener('keydown', (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k' && store.user) {
    event.preventDefault();
    openPalette();
  }
});

// ---- proje bağlamı ------------------------------------------------------- //

export async function refreshProject() {
  if (!store.project) { store.projectInfo = null; return null; }
  try {
    store.projectInfo = await get(p`/api/projects/${store.project}`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      store.project = null;
      store.projectInfo = null;
      toast('Proje bulunamadı', 'warn');
      location.hash = '#/projeler';
    } else toastError(error);
  }
  emit('project:info', store.projectInfo);
  return store.projectInfo;
}

// ---- yönlendirme -------------------------------------------------------- //

let lastHash = location.hash;

async function route() {
  if (!store.user) return;
  const parsed = parse(location.hash);
  if (!parsed) { location.hash = '#/projeler'; return; }
  const { route: current, params, query } = parsed;
  if (current.admin && !isAdmin()) { toast('Bu sayfa yöneticiler içindir', 'warn'); location.hash = '#/projeler'; return; }
  const token = ++renderToken;
  const projectChanged = current.project && params.slug !== store.project;
  if (current.project) {
    store.project = params.slug;
    if (projectChanged || !store.projectInfo) await refreshProject();
  }
  await renderTopbar(current, params);
  if (token !== renderToken || !store.user) return;
  if (currentModule?.dispose) { try { currentModule.dispose(); } catch (error) { console.error(error); } }
  clear(app);
  app.append(loading());
  document.title = `${current.title} · DWHFlow`;
  let module;
  try {
    module = await import(`./screens/${current.screen}.js`);
  } catch (error) {
    console.error(error);
    clear(app);
    app.append(h('div', { class: 'card' }, h('h2', { text: current.title }), h('p', { class: 'muted', text: 'Bu ekran yüklenemedi.' })));
    return;
  }
  if (token !== renderToken) return;
  currentModule = module;
  const root = h('div');
  try {
    await module.render(root, params, query);
    if (token !== renderToken) return;
    clear(app);
    app.append(root);
  } catch (error) {
    if (token !== renderToken) return;
    clear(app);
    if (error instanceof ApiError && error.status === 401) return;
    app.append(h('div', { class: 'card' }, h('h2', { text: current.title }), h('div', { class: 'banner bad' }, error.detail || error.message)));
  }
}

window.addEventListener('hashchange', () => {
  if (location.hash === lastHash) return;
  if (!confirmLeave()) { history.replaceState(null, '', lastHash); return; }
  lastHash = location.hash;
  route();
});

// İş bitince (okuma, diyagram) proje özeti ve açık ekran yenilenir.
on('project:changed', async () => {
  if (!store.user) return;
  await refreshProject();
  const parsed = parse(location.hash);
  if (parsed) await renderTopbar(parsed.route, parsed.params);
  if (currentModule?.refreshOnChange) route();
});

window.addEventListener('dwhflow:auth', () => { if (store.user) { store.user = null; boot(); } });

// ---- açılış ------------------------------------------------------------- //

async function boot() {
  renderToken += 1; // yarıda kalan ekran çizimleri (ör. oturum düşünce) giriş ekranını ezmesin
  clear(app);
  app.append(loading());
  let state;
  try {
    state = await get('/api/auth/state');
  } catch (error) {
    clear(app);
    app.append(h('div', { class: 'banner bad', text: `Sunucuya ulaşılamadı: ${error.message}` }));
    return;
  }
  if (!state.user) { store.user = null; authScreen(state); return; }
  store.user = state.user;
  try { store.meta = await get('/api/meta'); } catch (error) { toastError(error); }
  lastHash = location.hash;
  await route();
}

boot();

export { ROUTES, can };
