// Genel ayarlar (yönetici): tüm projeler için varsayılanlar ve LLM bağlantı profilleri.
// Bir proje kendi ayarıyla bu değerlerin üzerine yazabilir; düzenleyici ortak bileşendedir (_settings_editor.js).

import { pageHeader } from '../core/ui.js';
import { mountSettingsEditor } from './_settings_editor.js';

let editor = null;
let sequence = 0;

export async function render(root, params, query = {}) {
  const mine = ++sequence;
  root.append(pageHeader({
    title: 'Genel ayarlar',
    subtitle: 'Tüm projeler için geçerli değerler; bir proje kendi ayarlarıyla bunların üzerine yazabilir.',
    crumbs: [{ label: 'Projeler', href: '#/projeler' }, { label: 'Genel ayarlar' }],
  }));
  const instance = await mountSettingsEditor(root, { scope: 'global', tab: query && query.sekme });
  if (mine !== sequence) { instance.dispose(); return; }
  editor = instance;
}

export function dispose() {
  sequence += 1;
  if (editor) { editor.dispose(); editor = null; }
}
