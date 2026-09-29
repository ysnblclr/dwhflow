// Projeler: liste, yeni proje (boş ya da örnek senaryoyla), silme.

import { del, get, p, post } from '../core/api.js';
import { h } from '../core/dom.js';
import { go, link } from '../core/router.js';
import { canEdit, isAdmin, store } from '../core/store.js';
import { button, confirmDialog, dataTable, empty, formDialog, pageHeader, toastError, trackJob, when } from '../core/ui.js';

export const refreshOnChange = true;

export async function render(root) {
  store.project = null;
  store.projectInfo = null;
  const projects = await get('/api/projects');

  const create = async () => {
    const values = await formDialog({
      title: 'Yeni proje',
      fields: [
        { name: 'name', label: 'Proje adı', required: true, placeholder: 'ör. Kredi kartı işlemleri' },
        { name: 'description', label: 'Açıklama', type: 'textarea', wide: true },
        { name: 'sample', label: 'Başlangıç', type: 'select', options: [
          { value: '', label: 'Boş proje' }, { value: 'kart', label: 'Örnek: kredi kartı senaryosu (6 doküman)' }] },
      ],
      submitLabel: 'Oluştur',
    });
    if (!values) return;
    try {
      const body = { name: values.name, description: values.description };
      if (values.sample) body.sample = values.sample;
      const project = await post('/api/projects', body);
      if (project.job) trackJob(project.job, { title: `${project.ad}: örnek dokümanlar okunuyor` });
      go('p', project.slug);
    } catch (error) { toastError(error); }
  };

  const remove = async (project) => {
    if (!await confirmDialog(`"${project.ad}" projesi dokümanları, düzeltmeleri ve diyagramlarıyla birlikte kalıcı olarak silinsin mi?`, { danger: true, confirmLabel: 'Sil' })) return;
    try { await del(p`/api/projects/${project.slug}`); go('projeler'); location.reload(); } catch (error) { toastError(error); }
  };

  root.append(pageHeader({
    title: 'Projeler',
    subtitle: 'Her proje bir iş alanının dokümanlarını, ayarlarını, düzeltmelerini ve diyagramlarını bir arada tutar.',
    actions: [canEdit() ? button('+ Yeni proje', create, { kind: 'primary' }) : null],
  }));

  if (!projects.length) {
    root.append(h('div', { class: 'card' }, empty('Henüz proje yok.', canEdit()
      ? h('div', { class: 'row', style: { justifyContent: 'center' } }, button('+ Yeni proje', create, { kind: 'primary' }),
        h('a', { class: 'btn', href: '#/ornekler' }, 'Örneklere göz at'))
      : 'Bir düzenleyicinin proje oluşturmasını bekleyin.')));
    return;
  }

  const table = dataTable({
    rows: projects,
    rowKey: (r) => r.slug,
    onRowClick: (r) => go('p', r.slug),
    columns: [
      { key: 'ad', label: 'Proje', render: (r) => h('div', {}, h('a', { href: link('p', r.slug), text: r.ad }),
        r.aciklama ? h('div', { class: 'small muted', text: r.aciklama }) : null) },
      { key: 'documents', label: 'Doküman', num: true },
      { key: 'runs', label: 'Diyagram çalıştırması', num: true },
      { key: 'built_at', label: 'Son okuma', render: (r) => when(r.built_at) },
      { key: 'guncelleme', label: 'Son değişiklik', render: (r) => when(r.guncelleme) },
      { key: 'olusturan', label: 'Oluşturan' },
      { key: 'jobs', label: '', sort: false, render: (r) => (r.jobs ? h('span', { class: 'badge info' }, h('span', { class: 'spinner' }), ' işleniyor') : null) },
      { key: 'actions', label: '', sort: false, render: (r) => (isAdmin()
        ? h('button', { class: 'icon-btn', title: 'Projeyi sil', 'aria-label': 'Projeyi sil', onclick: (e) => { e.stopPropagation(); remove(r); } }, '🗑')
        : null) },
    ],
  });
  root.append(table.el);
}
