// Örnekler ve şablonlar: örnek senaryolar, biçim örnekleri, boş şablonlar ve biçim rehberi.

import { download, get, p, post } from '../core/api.js';
import { fmtSize, h } from '../core/dom.js';
import { go, link } from '../core/router.js';
import { canEdit, label, store } from '../core/store.js';
import {
  badge, button, dataTable, empty, formDialog, layerPill, loading, pageHeader, tabs, toast, toastError, trackJob,
} from '../core/ui.js';
import { formatCatalog, unsupportedList } from './system.js';

const TABS = [
  { id: 'senaryolar', label: 'Senaryolar' },
  { id: 'bicimler', label: 'Biçim örnekleri' },
  { id: 'sablonlar', label: 'Boş şablonlar' },
  { id: 'rehber', label: 'Biçim rehberi' },
];
let activeTab = 'senaryolar';

function expectedText(expected) {
  if (!expected) return '—';
  const parts = [];
  if (expected.datasets) parts.push(`${expected.datasets} tablo/dosya`);
  if (expected.processes) parts.push(`${expected.processes} süreç`);
  if (expected.links) parts.push(`${expected.links} bağ`);
  if (expected.precedes) parts.push(`${expected.precedes} sıra bağı`);
  return parts.join(' · ') || '—';
}

/** Kural türü dataset ya da süreç türü olabilir; hangisinin etiketi varsa o. */
function kindLabel(value) {
  if (!value) return '—';
  const dataset = label.datasetKind(value);
  return dataset !== value ? dataset : label.processKind(value);
}

// ---- eylemler ----------------------------------------------------------- //

async function createFromScenario(scenario) {
  const values = await formDialog({
    title: 'Senaryoyla proje oluştur',
    intro: `"${scenario.title}" senaryosunun ${scenario.files?.length || 0} dokümanı yeni projeye yüklenir ve okunur.`,
    fields: [
      { name: 'name', label: 'Proje adı', required: true, value: scenario.title, wide: true },
      { name: 'description', label: 'Açıklama', type: 'textarea', value: scenario.description || '', wide: true },
    ],
    submitLabel: 'Oluştur',
  });
  if (!values) return;
  try {
    const project = await post('/api/projects', { name: values.name, description: values.description, sample: scenario.id });
    if (project.job) trackJob(project.job, { title: `${project.ad}: örnek dokümanlar okunuyor` });
    go('p', project.slug);
  } catch (error) { toastError(error); }
}

async function addToProject(file) {
  let projects;
  try { projects = await get('/api/projects'); } catch (error) { toastError(error); return; }
  if (!projects.length) { toast('Önce bir proje oluşturun', 'warn'); return; }
  const current = projects.some((x) => x.slug === store.project) ? store.project : projects[0].slug;
  const values = await formDialog({
    title: 'Projeye ekle',
    intro: `${file.name} seçilen projeye doküman olarak eklenir ve dokümanlar yeniden okunur.`,
    fields: [{ name: 'project', label: 'Proje', type: 'select', value: current, wide: true,
      options: projects.map((x) => ({ value: x.slug, label: x.ad })) }],
    submitLabel: 'Ekle',
  });
  if (!values?.project) return;
  const project = projects.find((x) => x.slug === values.project);
  try {
    const result = await post(p`/api/projects/${values.project}/documents/sample`, { group: 'bicim', name: file.name });
    toast(h('span', {}, `${file.name}, ${project?.ad || values.project} projesine eklendi. `,
      h('a', { href: link('p', values.project, 'dokumanlar') }, 'Dokümanlara git')), 'ok', 8000);
    if (result.job) trackJob(result.job, { title: `${file.name} okunuyor` });
  } catch (error) { toastError(error); }
}

// ---- sekmeler ----------------------------------------------------------- //

function scenariosTab(catalog) {
  const scenarios = catalog.scenarios || [];
  if (!scenarios.length) return h('div', { class: 'card' }, empty('Örnek senaryo yok.'));
  return h('div', { class: 'stack' }, scenarios.map((scenario) => h('div', { class: 'card' },
    h('div', { class: 'card-head' }, h('h2', { text: scenario.title }),
      canEdit() ? button('Bu senaryoyla proje oluştur', () => createFromScenario(scenario), { kind: 'primary' }) : null),
    scenario.description ? h('p', { class: 'muted', text: scenario.description }) : null,
    h('ul', { class: 'list' }, (scenario.files || []).map((file) => h('li', {},
      h('span', { class: 'grow' }, h('a', { href: p`/api/samples/${scenario.id}/${file.name}`, download: '', text: file.name })),
      badge(file.label || 'desteklenmiyor', file.format ? 'muted' : 'warn'),
      h('span', { class: 'small muted nowrap', text: fmtSize(file.size) })))))));
}

function formatsTab(catalog) {
  const table = dataTable({
    rows: catalog.formats || [],
    rowKey: (r) => r.name,
    empty: 'Biçim örneği yok',
    columns: [
      { key: 'name', label: 'Dosya', wrap: true, render: (r) => h('a', { href: p`/api/samples/bicim/${r.name}`, download: '', text: r.name }) },
      { key: 'label', label: 'Biçim', render: (r) => badge(r.label || 'desteklenmiyor', r.format ? 'muted' : 'warn') },
      { key: 'description', label: 'Açıklama', wrap: true },
      { key: 'expected', label: 'Beklenen sonuç', sort: (r) => expectedText(r.expected), render: (r) => h('span', { class: 'small', text: expectedText(r.expected) }) },
      { key: 'size', label: 'Boyut', num: true, render: (r) => fmtSize(r.size) },
      { key: 'actions', label: '', sort: false, render: (r) => (canEdit() ? button('Projeye ekle', () => addToProject(r), { kind: 'small' }) : null) },
    ],
  });
  return h('div', { class: 'stack' },
    h('p', { class: 'muted', text: 'Her desteklenen biçimden küçük bir dosya ve okunduğunda çıkması beklenen sonuç. Bir okuyucunun dokümanınızı neden farklı okuduğunu anlamak için karşılaştırın.' }),
    table.el);
}

function templatesTab(catalog) {
  const templates = catalog.templates || [];
  return h('div', { class: 'stack' },
    h('p', { class: 'muted', text: 'Boş şablonlarla yazılan dokümanlar LLM gerekmeden, kurallarla eksiksiz okunur.' }),
    templates.length
      ? h('div', { class: 'grid-3' }, templates.map((t) => h('div', { class: 'card' },
        h('h2', { text: t.title }),
        h('p', { class: 'small muted', text: t.description || '' }),
        h('div', { class: 'row' }, h('a', { class: 'btn', href: p`/api/templates/${t.id}`, download: '' }, 'İndir'),
          h('span', { class: 'small muted mono', text: t.name || '' })))))
      : h('div', { class: 'card' }, empty('Şablon yok.')));
}

async function loadGuide() {
  const slug = store.project;
  if (slug) {
    try {
      const guide = await get(p`/api/projects/${slug}/guide`);
      const name = store.projectInfo?.slug === slug ? store.projectInfo.ad : slug;
      return { guide, slug, name };
    } catch { /* proje silinmiş ya da ayarı bozuk olabilir: varsayılan rehber */ }
  }
  return { guide: await get('/api/guide'), slug: null, name: null };
}

function guideTab({ guide, slug, name }) {
  const markdown = slug ? `${p`/api/projects/${slug}/guide`}?format=md` : '/api/guide?format=md';
  const custom = (guide.roles || []).some((r) => r.synonyms.some((s) => s.custom));
  const rules = dataTable({
    rows: (guide.rules || []).map((r, index) => ({ ...r, order: index + 1 })),
    rowKey: (r) => r.order,
    pageSize: 500,
    empty: 'Ad kuralı yok',
    columns: [
      { key: 'order', label: '#', num: true },
      { key: 'label', label: 'Kural', render: (r) => h('span', { class: 'mono', text: r.label }) },
      { key: 'layer', label: 'Katman', sort: (r) => label.layer(r.layer), render: (r) => layerPill(r.layer) },
      { key: 'kind', label: 'Tür', sort: (r) => kindLabel(r.kind), render: (r) => kindLabel(r.kind) },
      { key: 'origin', label: 'Kaynak', render: (r) => (r.origin === 'ayar' ? badge('ayar', 'info') : badge('varsayılan', 'muted')) },
    ],
  });
  const synonym = (s) => (s.custom
    ? h('span', { class: 'badge info', title: 'Proje ayarlarından', text: s.text })
    : h('span', { class: 'badge muted', text: s.text }));
  const roleRow = (role) => h('tr', {},
    h('td', { class: 'nowrap' }, h('b', { text: role.label })),
    h('td', {}, h('div', { class: 'row samples-synonyms' }, (role.synonyms || []).map(synonym))));
  const rolesTable = h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
    h('thead', {}, h('tr', {}, h('th', { text: 'Rol' }), h('th', { text: 'Tanınan başlıklar' }))),
    h('tbody', {}, (guide.roles || []).map(roleRow))));

  return h('div', { class: 'stack' },
    h('div', { class: 'banner info' },
      h('span', { class: 'grow', text: slug
        ? `Bu rehber "${name}" projesinin etkin ayarlarından üretildi; ayarlara eklenen başlık ve kurallar burada görünür.`
        : 'Bu rehber varsayılan ayarlardan üretildi. Bir proje seçiliyken o projenin ayarlarıyla gösterilir.' }),
      button('Markdown indir', () => download(markdown))),

    h('div', { class: 'card' }, h('h2', { text: 'Tablo başlıkları (STTM, job listeleri)' }),
      h('p', { class: 'small muted', text: 'Bir tablonun satırları, başlıkları aşağıdaki rollerden birine eşleşirse okunur. Eşleşme büyük/küçük harf ve Türkçe karakterden bağımsızdır.' }),
      custom ? h('p', { class: 'small' }, badge('proje', 'info'), ' işaretli başlıklar bu projenin ayarlarından gelir.') : null,
      rolesTable),

    h('div', { class: 'card' }, h('h2', { text: 'Ad → katman kuralları' }),
      h('p', { class: 'small muted', text: 'Sırayla denenir; ilk eşleşen kazanır. Hiçbiri eşleşmezse ad bağlamdan yorumlanır.' }),
      rules.el),

    h('div', { class: 'card' }, h('h2', { text: 'Cümle kalıpları' }),
      h('p', { class: 'small muted', text: 'Word, PDF ve metin dokümanlarındaki bu kalıplarla yazılmış cümleler akış olarak okunur.' }),
      h('ul', { class: 'list' }, (guide.patterns || []).map((pattern) => h('li', { style: { display: 'block' } },
        h('div', { text: pattern.pattern }), h('div', { class: 'small muted' }, 'Örnek: ', h('i', { text: pattern.example })))))),

    h('div', { class: 'card' }, h('h2', { text: 'İpuçları' }),
      h('ul', {}, (guide.tips || []).map((tip) => h('li', { text: tip })))),

    (guide.systems || []).length ? h('div', { class: 'card' }, h('h2', { text: 'Tanımlı sistemler' }),
      h('div', { class: 'row' }, guide.systems.map((s) => badge(s, 'muted')))) : null,

    h('div', { class: 'card' }, h('h2', { text: 'Okunan biçimler' }), formatCatalog(guide.formats),
      h('h3', { text: 'Tanınan ama okunamayan türler' }), unsupportedList(guide.unsupported)));
}

// ---- ekran -------------------------------------------------------------- //

export async function render(root, params, query = {}) {
  if (TABS.some((t) => t.id === query.sekme)) activeTab = query.sekme;
  const catalog = await get('/api/samples');
  let guideData = null;

  const content = h('div');
  const bar = h('div');
  const draw = async () => {
    bar.replaceChildren(tabs(TABS.map((t) => ({ ...t, count: t.id === 'senaryolar' ? catalog.scenarios?.length
      : t.id === 'bicimler' ? catalog.formats?.length : t.id === 'sablonlar' ? catalog.templates?.length : 0 })),
    activeTab, (id) => { activeTab = id; draw(); }));
    content.replaceChildren();
    if (activeTab === 'senaryolar') content.append(scenariosTab(catalog));
    else if (activeTab === 'bicimler') content.append(formatsTab(catalog));
    else if (activeTab === 'sablonlar') content.append(templatesTab(catalog));
    else {
      const tab = activeTab;
      content.append(loading());
      try {
        guideData = guideData || await loadGuide();
        if (activeTab !== tab) return;
        content.replaceChildren(guideTab(guideData));
      } catch (error) {
        if (activeTab !== tab) return;
        content.replaceChildren(h('div', { class: 'banner bad' }, h('span', { class: 'grow', text: `Rehber yüklenemedi: ${error.detail || error.message}` })));
      }
    }
  };

  root.append(pageHeader({
    title: 'Örnekler ve şablonlar',
    subtitle: 'Hazır senaryolarla deneyin, biçim örneklerini inceleyin, boş şablonlarla okunması kolay dokümanlar yazın.',
    crumbs: [{ label: 'Projeler', href: '#/projeler' }, { label: 'Örnekler' }],
  }), bar, content);
  await draw();
}
