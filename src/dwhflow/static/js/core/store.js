// Uygulama durumu ve olay yayını. Ekranlar buradan kullanıcıyı, etiketleri ve seçili projeyi okur.

const listeners = new Map();

export const store = {
  user: null,        // {username, name, role}
  auth: true,        // false: sunucu girişsiz çalışıyor (herkes "ekip" kimliğiyle, yönetici)
  meta: null,        // /api/meta: katman/tür etiketleri, roller, işlemler
  project: null,     // seçili projenin kısa adı (slug)
  projectInfo: null, // /api/projects/{slug}: meta + summary + jobs
};

export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event).add(fn);
  return () => listeners.get(event)?.delete(fn);
}

export function emit(event, data) {
  for (const fn of listeners.get(event) || []) {
    try { fn(data); } catch (error) { console.error(error); }
  }
}

const ROLE_ORDER = ['izleyici', 'duzenleyici', 'yonetici'];

/** Kullanıcının rolü en az ``role`` mü? */
export function can(role) {
  if (!store.user) return false;
  return ROLE_ORDER.indexOf(store.user.role) >= ROLE_ORDER.indexOf(role);
}

export const canEdit = () => can('duzenleyici');
export const isAdmin = () => can('yonetici');

function lookup(list, value) {
  const item = (store.meta?.[list] || []).find((x) => x.value === value);
  return item ? item.label : (value ?? '—');
}

export const label = {
  layer: (v) => lookup('layers', v),
  datasetKind: (v) => lookup('dataset_kinds', v),
  processKind: (v) => lookup('process_kinds', v),
  edgeKind: (v) => lookup('edge_kinds', v),
  load: (v) => lookup('load_strategies', v),
  sensitivity: (v) => lookup('sensitivity', v),
  role: (v) => lookup('user_roles', v),
  op: (v) => lookup('operations', v),
  columnRole: (v) => lookup('column_roles', v),
  status: (v) => ({ draft: 'taslak', confirmed: 'onaylı', deprecated: 'kullanımdan kalktı' }[v] || v),
};

export function options(list) {
  return (store.meta?.[list] || []).map((x) => ({ value: x.value, label: x.label }));
}
