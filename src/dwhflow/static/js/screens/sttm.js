// Yer tutucu: bu ekran henüz yazılmadı.
import { h } from '../core/dom.js';
import { pageHeader } from '../core/ui.js';

export async function render(root) {
  root.append(pageHeader({ title: 'Hazırlanıyor' }), h('div', { class: 'card muted', text: 'Bu ekran yakında.' }));
}
