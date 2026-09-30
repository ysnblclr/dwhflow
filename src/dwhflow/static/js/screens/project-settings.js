// Proje ayarları: okuma kuralları, adlandırma, sistemler, okuyucular, diyagram, yapay zekâ, dışa/içe aktar.
// Proje katmanı genel ayarların üzerine yazar; düzenleyici ortak bileşendedir (_settings_editor.js).

import { link } from '../core/router.js';
import { store } from '../core/store.js';
import { pageHeader } from '../core/ui.js';
import { mountSettingsEditor } from './_settings_editor.js';

let editor = null;
let sequence = 0;

export async function render(root, { slug }, query = {}) {
  const mine = ++sequence;
  const name = (store.projectInfo && store.projectInfo.ad) || slug;
  root.append(pageHeader({
    title: 'Proje ayarları',
    subtitle: 'Bu projeye özel değerler genel ayarların üzerine yazılır; her değerin yanında hangi katmandan geldiği görünür.',
    crumbs: [{ label: 'Projeler', href: '#/projeler' }, { label: name, href: link('p', slug) }, { label: 'Ayarlar' }],
  }));
  const instance = await mountSettingsEditor(root, { scope: 'project', slug, tab: query && query.sekme });
  if (mine !== sequence) { instance.dispose(); return; }
  editor = instance;
}

export function dispose() {
  sequence += 1;
  if (editor) { editor.dispose(); editor = null; }
}
