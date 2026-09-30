// Kullanıcılar (yönetici): ekleme, ad/rol düzenleme, parola sıfırlama, etkinleştirme, silme.

import { del, get, p, patch, post } from '../core/api.js';
import { h } from '../core/dom.js';
import { label, options, store } from '../core/store.js';
import { badge, button, confirmDialog, dataTable, formDialog, pageHeader, toast, toastError, when } from '../core/ui.js';

const FALLBACK_ROLES = [
  { value: 'izleyici', label: 'İzleyici' },
  { value: 'duzenleyici', label: 'Düzenleyici' },
  { value: 'yonetici', label: 'Yönetici' },
];
const ROLE_HINT = 'İzleyici yalnız görür; düzenleyici doküman yükler, düzeltme ve diyagram yapar; yönetici ayrıca kullanıcıları ve genel ayarları yönetir.';
const PASSWORD_HINT = 'En az 10 karakter; en az iki tür (küçük harf, büyük harf, rakam, işaret); kullanıcı adını içermemeli.';

function roles() {
  const list = options('user_roles');
  return list.length ? list : FALLBACK_ROLES;
}

function roleLabel(value) {
  const text = label.role(value);
  return text !== value ? text : (FALLBACK_ROLES.find((r) => r.value === value)?.label || value || '—');
}

/** Sunucudaki parola kuralının (auth.check_password_policy) istemci tarafı önizlemesi. */
function passwordProblem(password, again, username) {
  if (password !== again) return 'Parolalar eşleşmiyor';
  if (password.length < 10) return 'Parola en az 10 karakter olmalı';
  if (username && password.toLowerCase().includes(username.toLowerCase())) return 'Parola kullanıcı adını içermemeli';
  const classes = [/[a-zçğıöşü]/, /[A-ZÇĞİÖŞÜ]/, /\d/, /[^\p{L}\p{N}_\s]/u].filter((re) => re.test(password)).length;
  if (classes < 2) return 'Parola en az iki karakter türü içermeli (küçük/büyük harf, rakam, işaret)';
  return null;
}

export async function render(root) {
  const users = await get('/api/users');
  const me = store.user?.username;
  let table = null;

  const reload = async () => {
    try { table.setRows(await get('/api/users')); } catch (error) { toastError(error); }
  };

  const add = async () => {
    const values = await formDialog({
      title: 'Yeni kullanıcı',
      fields: [
        { name: 'username', label: 'Kullanıcı adı', required: true, hint: '3–32 karakter: küçük harf, rakam, nokta, tire, alt çizgi' },
        { name: 'name', label: 'Ad soyad' },
        { name: 'role', label: 'Rol', type: 'select', value: 'izleyici', options: roles(), hint: ROLE_HINT, wide: true },
        { name: 'password', label: 'Parola', type: 'password', required: true, hint: PASSWORD_HINT },
        { name: 'again', label: 'Parola (tekrar)', type: 'password', required: true },
      ],
      submitLabel: 'Ekle',
      validate: (v) => passwordProblem(v.password, v.again, v.username.trim().toLowerCase()),
    });
    if (!values) return;
    try {
      const created = await post('/api/users', {
        username: values.username.trim(), name: values.name.trim(), role: values.role, password: values.password,
      });
      toast(`${created.username} eklendi`, 'ok');
      await reload();
    } catch (error) { toastError(error); }
  };

  const edit = async (user) => {
    const self = user.username === me;
    const fields = [{ name: 'name', label: 'Ad soyad', value: user.name, wide: true }];
    if (!self) fields.push({ name: 'role', label: 'Rol', type: 'select', value: user.role, options: roles(), hint: ROLE_HINT, wide: true });
    const values = await formDialog({
      title: `Düzenle: ${user.username}`,
      intro: self ? 'Kendi rolünüzü buradan değiştiremezsiniz.' : 'Rol değişirse kullanıcının açık oturumları kapatılır.',
      fields,
      submitLabel: 'Kaydet',
    });
    if (!values) return;
    const body = {};
    if (values.name.trim() !== (user.name || '')) body.name = values.name.trim();
    if (!self && values.role && values.role !== user.role) body.role = values.role;
    if (!Object.keys(body).length) { toast('Değişiklik yok', 'info'); return; }
    try {
      await patch(p`/api/users/${user.username}`, body);
      toast(body.role ? `${user.username} güncellendi; oturumları kapatıldı` : `${user.username} güncellendi`, 'ok');
      await reload();
    } catch (error) { toastError(error); }
  };

  const resetPassword = async (user) => {
    const values = await formDialog({
      title: `Parola sıfırla: ${user.username}`,
      intro: 'Yeni parolayı kullanıcıya güvenli bir yoldan iletin. Kullanıcının açık oturumları kapatılır.',
      fields: [
        { name: 'password', label: 'Yeni parola', type: 'password', required: true, hint: PASSWORD_HINT },
        { name: 'again', label: 'Yeni parola (tekrar)', type: 'password', required: true },
      ],
      submitLabel: 'Parolayı değiştir',
      validate: (v) => passwordProblem(v.password, v.again, user.username),
    });
    if (!values) return;
    try {
      await patch(p`/api/users/${user.username}`, { password: values.password });
      toast(`${user.username} için parola değişti; açık oturumları kapatıldı`, 'ok');
    } catch (error) { toastError(error); }
  };

  const setActive = async (user, active) => {
    if (!active && !await confirmDialog(`${user.username} devre dışı bırakılsın mı? Giriş yapamaz ve açık oturumları kapatılır. Kayıtları ve geçmişi korunur.`,
      { title: 'Kullanıcıyı devre dışı bırak', confirmLabel: 'Devre dışı bırak', danger: true })) return;
    try {
      await patch(p`/api/users/${user.username}`, { active });
      toast(active ? `${user.username} etkinleştirildi` : `${user.username} devre dışı bırakıldı`, 'ok');
      await reload();
    } catch (error) { toastError(error); }
  };

  const remove = async (user) => {
    if (!await confirmDialog(`${user.username} kalıcı olarak silinsin mi? Günlükteki eski kayıtlarda adı kalır. Yalnız erişimini kesmek istiyorsanız devre dışı bırakmayı seçin.`,
      { title: 'Kullanıcıyı sil', confirmLabel: 'Sil', danger: true })) return;
    try {
      await del(p`/api/users/${user.username}`);
      toast(`${user.username} silindi`, 'ok');
      await reload();
    } catch (error) { toastError(error); }
  };

  root.append(pageHeader({
    title: 'Kullanıcılar',
    subtitle: 'Hesaplar ve roller. Rol, parola ya da etkinlik değişince kullanıcının açık oturumları kapatılır.',
    crumbs: [{ label: 'Projeler', href: '#/projeler' }, { label: 'Kullanıcılar' }],
    actions: [button('+ Yeni kullanıcı', add, { kind: 'primary' })],
  }));

  table = dataTable({
    rows: users,
    rowKey: (u) => u.username,
    empty: 'Kullanıcı yok',
    columns: [
      { key: 'username', label: 'Kullanıcı adı', render: (u) => h('span', { class: 'row' },
        h('span', { class: 'mono', text: u.username }), u.username === me ? badge('siz', 'info') : null) },
      { key: 'name', label: 'Ad' },
      { key: 'role', label: 'Rol', sort: (u) => roleLabel(u.role), render: (u) => roleLabel(u.role) },
      { key: 'active', label: 'Durum', sort: (u) => (u.active ? 1 : 0), render: (u) => (u.active ? badge('etkin', 'ok') : badge('devre dışı', 'muted')) },
      { key: 'created_at', label: 'Oluşturulma', render: (u) => when(u.created_at) },
      { key: 'last_login', label: 'Son giriş', render: (u) => (u.last_login ? when(u.last_login) : h('span', { class: 'muted', text: 'hiç' })) },
      { key: 'actions', label: '', sort: false, render: (u) => {
        const self = u.username === me;
        return h('div', { class: 'row', style: { flexWrap: 'nowrap' } },
          button('Düzenle', () => edit(u), { kind: 'small' }),
          self ? null : button('Parola', () => resetPassword(u), { kind: 'small', title: 'Parolayı sıfırla' }),
          self ? null : (u.active
            ? button('Devre dışı bırak', () => setActive(u, false), { kind: 'small' })
            : button('Etkinleştir', () => setActive(u, true), { kind: 'small ok' })),
          self ? null : button('Sil', () => remove(u), { kind: 'small danger' }),
          self ? h('span', { class: 'small muted', text: 'Parolanızı sağ üstteki menüden değiştirin' }) : null);
      } },
    ],
  });
  root.append(table.el);
}
