// Diyagramlar: üretim planı, özel diyagramlar (yol, çevre, gizlenen nesneler), üretim ve çalıştırmalar;
// bir çalıştırmanın diyagramlarını uygulama içinde (iframe) görüntüleme.
//
// Özel diyagram ayarları proje ayarlarının ``diyagram`` bölümüne yazılır (PUT settings {patch}); liste
// alanları (yollar, cevreler, gizli) her seferinde bütün olarak gönderilir.

import { ApiError, del, get, p, post, put } from '../core/api.js';
import { append, clear, fmtNumber, fold, h } from '../core/dom.js';
import { go, link, setDirty } from '../core/router.js';
import { canEdit, on, store } from '../core/store.js';
import { badge, button, confirmDialog, dataTable, empty, pageHeader, toast, toastError, trackJob, when } from '../core/ui.js';

const KINDS = { overview: 'genel', lineage: 'soy ağacı', path: 'yol', focus: 'çevre', batch: 'batch' };
const QUALITIES = [
  { value: 'showcase', label: 'Vitrin kalitesi (showcase)' },
  { value: 'standard', label: 'Standart kalite' },
];
const RUN_STATES = { queued: ['sırada', 'info'], running: ['üretiliyor', 'info'], done: ['tamamlandı', 'ok'], error: ['hata', 'bad'] };
const DIAGRAM_STATUS = {
  pass: ['tüm kapılar geçti', 'ok'], skipped: ['tarayıcı kontrolü atlandı', 'warn'],
  fail: ['kapı başarısız', 'bad'], error: ['motor hatası', 'bad'],
};
const AUTO = [['genel', 'Genel akış'], ['soy', 'Soy ağaçları'], ['batch', 'Batch zinciri']];
const BUDGETS = [['butce', 'Diyagram başına en çok tablo', 8, 40], ['job_butcesi', 'Batch diyagramında en çok job', 8, 60]];
const POLL_MS = 2000;
const RUNS_POLL_MS = 3000;
const enc = encodeURIComponent;

// ---- yaşam döngüsü: zamanlayıcılar ve abonelikler dispose()'da temizlenir ---- //

let token = 0;
const timers = new Set();
let cleanups = [];

function stop() {
  token += 1;
  for (const id of timers) clearTimeout(id);
  timers.clear();
  for (const fn of cleanups) { try { fn(); } catch (error) { console.error(error); } }
  cleanups = [];
}

/** Ekran hâlâ aynı çizimdeyse ``fn``'i ``ms`` sonra çalıştırır. */
function later(current, fn, ms) {
  const id = setTimeout(() => { timers.delete(id); if (current === token) fn(); }, ms);
  timers.add(id);
}

export function dispose() {
  stop();
  setDirty(null);
}

export async function render(root, params, query = {}) {
  stop();
  const current = token;
  if (params.run) await renderRun(root, params.slug, params.run, query || {}, current);
  else await renderOverview(root, params.slug, current);
}

// ---- ortak parçalar ------------------------------------------------------ //

const isActive = (state) => state === 'queued' || state === 'running';

function fileUrl(slug, run, path = 'index.html') {
  return `/dosya/${enc(slug)}/${enc(run)}/${String(path).split('/').map(enc).join('/')}`;
}

function projectName(slug) {
  return (store.projectInfo && store.projectInfo.ad) || slug;
}

function runStateBadge(state) {
  const [text, kind] = RUN_STATES[state] || [state || 'bilinmiyor', 'muted'];
  return isActive(state) ? h('span', { class: `badge ${kind}` }, h('span', { class: 'spinner' }), ` ${text}`) : badge(text, kind);
}

function duration(seconds) {
  return seconds == null ? '—' : `${fmtNumber(seconds)} sn`;
}

function removeButton(title, onclick) {
  return h('button', { type: 'button', class: 'icon-btn', title, 'aria-label': title, onclick }, '✕');
}

// ---- plan, özel diyagramlar ve çalıştırmalar --------------------------- //

async function renderOverview(root, slug, current) {
  const editable = canEdit();
  const [firstPlan, firstRuns, datasets] = await Promise.all([
    get(p`/api/projects/${slug}/diagrams/plan`),
    get(p`/api/projects/${slug}/runs`),
    get(`${p`/api/projects/${slug}/datasets`}?limit=2000`).catch(() => ({ items: [] })),
  ]);
  let plan = firstPlan;
  let settings = plan.settings;
  let busy = false;
  const items = datasets.items || [];
  const known = new Set(items.flatMap((d) => [fold(d.name), fold(d.id)]));
  const listId = `dia-names-${current}`;

  // -- üretim -- //
  const quality = h('select', { class: 'dia-quality', 'aria-label': 'Diyagram kalitesi' },
    QUALITIES.map((q) => h('option', { value: q.value, selected: q.value === settings.kalite }, q.label)));
  const generateBtn = button('Diyagram üret', () => generate(), { kind: 'primary' });

  async function generate() {
    if (budgetDirty()) { toast('Önce bütçe değişikliklerini kaydedin ya da geri alın.', 'warn'); return; }
    generateBtn.disabled = true;
    try {
      const result = await post(p`/api/projects/${slug}/runs`, { quality: quality.value });
      trackJob(result.job, { title: 'Diyagram üretimi' });
      location.hash = `${link('p', slug, 'diyagramlar', result.run)}?is=${enc(result.job.id)}`;
    } catch (error) {
      toastError(error);
    } finally {
      if (current === token) { generateBtn.disabled = !(plan.slices || []).length; refreshRuns(); }
    }
  }

  // -- plan -- //
  const planBody = h('div', { class: 'stack' });

  function drawPlan() {
    clear(planBody);
    const slices = plan.slices || [];
    append(planBody, [
      (plan.notes || []).map((note) => h('div', { class: 'banner warn' }, h('span', { class: 'grow', text: note }))),
      slices.length
        ? h('ul', { class: 'list dia-plan' }, slices.map((slice) => h('li', {},
          badge(KINDS[slice.kind] || slice.kind, 'info'),
          h('div', { class: 'grow' }, h('b', { text: slice.title }),
            slice.subtitle ? h('div', { class: 'small muted', text: slice.subtitle }) : null),
          h('span', { class: 'small muted nowrap',
            text: slice.kind === 'batch' ? `${fmtNumber(slice.processes)} job` : `${fmtNumber(slice.datasets)} tablo` }),
          slice.trimmed ? badge('bütçeye göre kırpıldı', 'warn') : null)))
        : empty('Planda diyagram yok. Dokümanlardan tablo çıkarılamamış ya da otomatik diyagramlar kapalı olabilir.'),
    ]);
    generateBtn.disabled = !slices.length;
    generateBtn.title = slices.length ? `${slices.length} diyagram üretilecek` : 'Planda üretilecek diyagram yok';
  }

  async function refreshPlan() {
    try {
      const next = await get(p`/api/projects/${slug}/diagrams/plan`);
      if (current !== token) return;
      plan = next;
      settings = plan.settings;
      drawPlan();
    } catch (error) {
      if (current === token) toastError(error);
    }
  }

  // -- özel diyagramlar -- //
  const pathsBox = h('div');
  const focusBox = h('div');
  const hiddenBox = h('div');
  const autoBoxes = {};
  const budgetInputs = {};
  let budget = { butce: settings.butce, job_butcesi: settings.job_butcesi };
  const budgetDirty = () => budget.butce !== settings.butce || budget.job_butcesi !== settings.job_butcesi;

  async function save(patch, message) {
    if (busy) {
      toast('Önceki kayıt sürüyor; biraz sonra yeniden deneyin.', 'warn');
      drawEditor(); // değiştirilen onay kutusu kayıtlı değere dönsün
      return false;
    }
    busy = true;
    try {
      const response = await put(p`/api/projects/${slug}/settings`, { patch: { diyagram: patch } });
      settings = response.values.diyagram;
      if (response.job) trackJob(response.job, { title: 'Dokümanlar yeniden okunuyor' });
      if (message) toast(message, 'ok');
      await refreshPlan();
      return true;
    } catch (error) {
      toastError(error);
      return false;
    } finally {
      busy = false;
      if (current === token) drawEditor();
    }
  }

  async function confirmUnknown(refs) {
    if (!known.size) return true;
    const missing = refs.filter((ref) => !known.has(fold(ref)));
    if (!missing.length) return true;
    return confirmDialog(`Modelde bu adla bir tablo/dosya bulunamadı: ${missing.join(', ')}. Plan bu adı çözemezse `
      + 'diyagram üretilmez ve plana bir not düşülür. Yine de eklensin mi?', { confirmLabel: 'Ekle' });
  }

  const nameInput = (placeholder) => h('input', { type: 'text', list: listId, placeholder, 'aria-label': placeholder,
    autocomplete: 'off', maxlength: '300' });
  const onEnter = (fn) => (event) => { if (event.key === 'Enter') { event.preventDefault(); fn(); } };

  const pathSource = nameInput('Kaynak tablo');
  const pathTarget = nameInput('Hedef tablo');
  async function addPath() {
    const source = pathSource.value.trim();
    const target = pathTarget.value.trim();
    if (!source || !target) { toast('Kaynak ve hedef tablo gerekli', 'warn'); return; }
    if (fold(source) === fold(target)) { toast('Kaynak ile hedef aynı olamaz', 'warn'); return; }
    const paths = settings.yollar || [];
    if (paths.some(([a, b]) => fold(a) === fold(source) && fold(b) === fold(target))) { toast('Bu yol zaten ekli', 'warn'); return; }
    if (!await confirmUnknown([source, target])) return;
    if (await save({ yollar: [...paths, [source, target]] }, 'Yol eklendi')) { pathSource.value = ''; pathTarget.value = ''; }
  }
  pathSource.addEventListener('keydown', onEnter(addPath));
  pathTarget.addEventListener('keydown', onEnter(addPath));

  const focusName = nameInput('Merkez tablo');
  const focusHops = h('select', { 'aria-label': 'Adım sayısı' },
    [1, 2, 3, 4].map((n) => h('option', { value: String(n), selected: n === 2 }, `${n} adım`)));
  async function addFocus() {
    const nesne = focusName.value.trim();
    const adim = Number(focusHops.value);
    if (!nesne) { toast('Merkez tablo gerekli', 'warn'); return; }
    const focus = settings.cevreler || [];
    if (focus.some((f) => fold(f.nesne) === fold(nesne) && f.adim === adim)) { toast('Bu çevre diyagramı zaten ekli', 'warn'); return; }
    if (!await confirmUnknown([nesne])) return;
    if (await save({ cevreler: [...focus, { nesne, adim }] }, 'Çevre diyagramı eklendi')) focusName.value = '';
  }
  focusName.addEventListener('keydown', onEnter(addFocus));

  const hiddenName = nameInput('Gizlenecek tablo');
  async function addHidden() {
    const name = hiddenName.value.trim();
    if (!name) { toast('Gizlenecek tablonun adı gerekli', 'warn'); return; }
    const hidden = settings.gizli || [];
    if (hidden.some((x) => fold(x) === fold(name))) { toast('Bu nesne zaten gizli', 'warn'); return; }
    if (!await confirmUnknown([name])) return;
    if (await save({ gizli: [...hidden, name] }, 'Nesne gizlendi')) hiddenName.value = '';
  }
  hiddenName.addEventListener('keydown', onEnter(addHidden));

  for (const [key, text] of AUTO) {
    autoBoxes[key] = h('input', { type: 'checkbox', disabled: !editable, 'aria-label': text,
      onchange: (event) => save({ otomatik: { [key]: event.target.checked } }, `${text}: ${event.target.checked ? 'açıldı' : 'kapatıldı'}`) });
  }

  const saveBudgetBtn = button('Kaydet', () => saveBudget(), { kind: 'primary small' });
  const discardBudgetBtn = button('Geri al', () => {
    budget = { butce: settings.butce, job_butcesi: settings.job_butcesi };
    for (const [key] of BUDGETS) budgetInputs[key].value = String(budget[key]);
    updateBudgetState();
  }, { kind: 'small' });
  for (const [key, text, min, max] of BUDGETS) {
    const input = h('input', { type: 'number', min: String(min), max: String(max), step: '1', disabled: !editable, 'aria-label': text });
    input.addEventListener('input', () => {
      budget[key] = input.value === '' ? null : Number(input.value);
      updateBudgetState();
    });
    budgetInputs[key] = input;
  }

  function updateBudgetState() {
    const dirty = budgetDirty();
    saveBudgetBtn.disabled = !dirty;
    discardBudgetBtn.disabled = !dirty;
    setDirty(dirty ? 'Kaydedilmemiş diyagram bütçesi değişikliği var' : null);
  }

  async function saveBudget() {
    const patch = {};
    for (const [key, text, min, max] of BUDGETS) {
      const value = budget[key];
      if (!Number.isInteger(value) || value < min || value > max) {
        toast(`${text} ${min}–${max} arasında bir tam sayı olmalı`, 'warn');
        return;
      }
      if (value !== settings[key]) patch[key] = value;
    }
    if (Object.keys(patch).length) await save(patch, 'Bütçe kaydedildi');
  }

  function drawEditor() {
    clear(pathsBox); clear(focusBox); clear(hiddenBox);
    const paths = settings.yollar || [];
    pathsBox.append(paths.length
      ? h('ul', { class: 'list' }, paths.map(([source, target], index) => h('li', {},
        h('span', { class: 'grow' }, h('b', { text: source }), ' → ', h('b', { text: target })),
        editable ? removeButton(`${source} → ${target} yolunu kaldır`,
          () => save({ yollar: paths.filter((_, i) => i !== index) }, 'Yol kaldırıldı')) : null)))
      : h('p', { class: 'small muted', text: 'Özel yol yok.' }));
    const focus = settings.cevreler || [];
    focusBox.append(focus.length
      ? h('ul', { class: 'list' }, focus.map((item, index) => h('li', {},
        h('span', { class: 'grow' }, h('b', { text: item.nesne }), ` · ${item.adim} adım`),
        editable ? removeButton(`${item.nesne} çevre diyagramını kaldır`,
          () => save({ cevreler: focus.filter((_, i) => i !== index) }, 'Çevre diyagramı kaldırıldı')) : null)))
      : h('p', { class: 'small muted', text: 'Özel çevre diyagramı yok.' }));
    const hidden = settings.gizli || [];
    hiddenBox.append(hidden.length
      ? h('div', { class: 'set-chips' }, hidden.map((name, index) => h('span', { class: `set-chip${editable ? '' : ' plain'}` },
        h('span', { text: name }),
        editable ? h('button', { type: 'button', title: `${name} yeniden gösterilsin`, 'aria-label': `${name} yeniden gösterilsin`,
          onclick: () => save({ gizli: hidden.filter((_, i) => i !== index) }, 'Nesne yeniden gösteriliyor') }, '×') : null)))
      : h('p', { class: 'small muted', text: 'Gizlenen nesne yok.' }));
    for (const [key] of AUTO) autoBoxes[key].checked = Boolean(settings.otomatik && settings.otomatik[key]);
    if (!budgetDirty()) { // kaydedilmemiş bütçe yazımı korunur
      budget = { butce: settings.butce, job_butcesi: settings.job_butcesi };
      for (const [key] of BUDGETS) budgetInputs[key].value = String(budget[key]);
    }
    updateBudgetState();
  }

  const editorCard = h('div', { class: 'card' },
    h('div', { class: 'card-head' }, h('h2', { text: 'Özel diyagramlar' }),
      h('a', { class: 'btn small', href: `${link('p', slug, 'ayarlar')}?sekme=diyagram` }, 'Tüm diyagram ayarları')),
    h('datalist', { id: listId }, items.map((d) => h('option', { value: d.name }))),
    h('div', { class: 'dia-box' }, h('h3', { text: 'Yollar' }),
      h('p', { class: 'small muted', text: 'İki tablo arasındaki tüm akışı tek diyagramda gösterir.' }),
      pathsBox,
      editable ? h('div', { class: 'dia-add' }, pathSource, h('span', { text: '→' }), pathTarget, button('Yol ekle', () => addPath(), { kind: 'small' })) : null),
    h('div', { class: 'dia-box' }, h('h3', { text: 'Çevre diyagramları' }),
      h('p', { class: 'small muted', text: 'Bir tablonun belirli adım yukarı ve aşağı akışını gösterir.' }),
      focusBox,
      editable ? h('div', { class: 'dia-add' }, focusName, focusHops, button('Çevre ekle', () => addFocus(), { kind: 'small' })) : null),
    h('div', { class: 'dia-box' }, h('h3', { text: 'Gizlenen nesneler' }),
      h('p', { class: 'small muted', text: 'Diyagramlarda gösterilmez (vurgulanan yol üyeleri hariç).' }),
      hiddenBox,
      editable ? h('div', { class: 'dia-add' }, hiddenName, button('Gizle', () => addHidden(), { kind: 'small' })) : null),
    h('div', { class: 'dia-box' }, h('h3', { text: 'Otomatik diyagramlar' }),
      h('div', { class: 'row' }, AUTO.map(([key, text]) => h('label', { class: 'field inline' }, autoBoxes[key], h('span', { text }))))),
    h('div', { class: 'dia-box' }, h('h3', { text: 'Bütçe' }),
      h('div', { class: 'dia-budget' },
        BUDGETS.map(([key, text, min, max]) => h('label', { class: 'field' },
          h('span', { class: 'label', text: `${text} (${min}–${max})` }), budgetInputs[key])),
        editable ? h('div', { class: 'row' }, saveBudgetBtn, discardBudgetBtn) : null)),
    editable ? null : h('p', { class: 'small muted', text: 'Bu ayarları yalnız düzenleyiciler değiştirebilir.' }));

  // -- çalıştırmalar -- //
  const runsCount = h('span', { class: 'badge muted', text: String(firstRuns.length) });
  const runsTable = dataTable({
    rows: firstRuns,
    rowKey: (r) => r.id,
    onRowClick: (r) => go('p', slug, 'diyagramlar', r.id),
    empty: 'Henüz diyagram üretilmedi.',
    columns: [
      { key: 'created_at', label: 'Zaman', render: (r) => when(r.created_at) },
      { key: 'id', label: 'Çalıştırma', render: (r) => h('a', { class: 'mono', href: link('p', slug, 'diyagramlar', r.id), text: r.id,
        onclick: (event) => event.stopPropagation() }) },
      { key: 'created_by', label: 'Üreten' },
      { key: 'ready', label: 'Hazır', num: true, render: (r) => (isActive(r.state) ? '—' : `${r.ready}/${r.total}`) },
      { key: 'state', label: 'Durum', render: (r) => runStateBadge(r.state) },
      { key: 'duration_s', label: 'Süre', num: true, sort: (r) => (r.duration_s == null ? -1 : r.duration_s), render: (r) => duration(r.duration_s) },
      { key: 'actions', label: '', sort: false, render: (r) => (editable
        ? h('button', { type: 'button', class: 'icon-btn', title: isActive(r.state) ? 'Sürerken silinemez' : 'Çalıştırmayı sil',
          'aria-label': 'Çalıştırmayı sil', disabled: isActive(r.state),
          onclick: (event) => { event.stopPropagation(); removeRun(r); } }, '🗑')
        : null) },
    ],
  });

  let runsPending = false;
  function scheduleRuns() {
    if (runsPending) return;
    runsPending = true;
    later(current, () => { runsPending = false; refreshRuns(); }, RUNS_POLL_MS);
  }

  async function refreshRuns() {
    let runs;
    try {
      runs = await get(p`/api/projects/${slug}/runs`);
    } catch (error) {
      console.warn('çalıştırmalar yenilenemedi', error);
      return;
    }
    if (current !== token) return;
    runsTable.setRows(runs);
    runsCount.textContent = String(runs.length);
    if (runs.some((r) => isActive(r.state))) scheduleRuns();
  }

  async function removeRun(run) {
    if (!await confirmDialog(`${run.id} çalıştırması ve ürettiği diyagram dosyaları kalıcı olarak silinsin mi?`,
      { danger: true, confirmLabel: 'Sil' })) return;
    try {
      await del(p`/api/projects/${slug}/runs/${run.id}`);
      toast('Çalıştırma silindi', 'ok');
    } catch (error) {
      toastError(error);
    }
    await refreshRuns();
  }

  // Bir iş (okuma ya da diyagram) bitince plan ve çalıştırmalar yenilenir; düzenlenen alanlara dokunulmaz.
  cleanups.push(on('project:changed', () => { if (current === token) { refreshRuns(); refreshPlan(); } }));
  if (firstRuns.some((r) => isActive(r.state))) scheduleRuns();

  root.append(
    pageHeader({
      title: 'Diyagramlar',
      subtitle: 'Güncel (düzeltilmiş) modelden Archify diyagramları üretilir; her üretim ayrı bir çalıştırma olarak saklanır.',
      crumbs: [{ label: 'Projeler', href: '#/projeler' }, { label: projectName(slug), href: link('p', slug) }, { label: 'Diyagramlar' }],
      actions: editable ? [quality, generateBtn] : [],
    }),
    h('div', { class: 'grid-2' },
      h('div', { class: 'card' },
        h('div', { class: 'card-head' }, h('h2', { text: 'Plan' }),
          h('span', { class: 'small muted', text: 'Bir sonraki üretimde oluşturulacak diyagramlar' })),
        planBody),
      editorCard),
    h('div', { class: 'dia-section' },
      h('div', { class: 'card-head' }, h('h2', {}, 'Çalıştırmalar ', runsCount),
        button('Yenile', () => refreshRuns(), { kind: 'small' })),
      runsTable.el),
  );
  drawPlan();
  drawEditor();
}

// ---- bir çalıştırma ------------------------------------------------------ //

async function fetchJob(jobId) {
  try { return await get(p`/api/jobs/${jobId}`); } catch { return null; }
}

/** Çalışan çalıştırmanın işi: sunucu ayrıntıda verir (başka kullanıcılar da ilerlemeyi görür). */
async function runJob(detail, jobId) {
  if (!isActive(detail.status && detail.status.state)) return null;
  if (detail.job) return detail.job;
  return jobId ? fetchJob(jobId) : null;
}

async function renderRun(root, slug, run, query, current) {
  const editable = canEdit();
  const jobId = query.is || null;
  const crumbs = [{ label: 'Projeler', href: '#/projeler' }, { label: projectName(slug), href: link('p', slug) },
    { label: 'Diyagramlar', href: link('p', slug, 'diyagramlar') }, { label: run }];
  let detail;
  try {
    detail = await get(p`/api/projects/${slug}/runs/${run}`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      root.append(pageHeader({ title: 'Diyagram çalıştırması', crumbs }),
        h('div', { class: 'card' }, empty('Çalıştırma bulunamadı; silinmiş olabilir.',
          h('a', { class: 'btn', href: link('p', slug, 'diyagramlar') }, 'Diyagramlara dön'))));
      return;
    }
    throw error;
  }
  let job = await runJob(detail, jobId);
  let pollError = null;
  const viewer = h('div');

  async function poll() {
    let next;
    try {
      next = await get(p`/api/projects/${slug}/runs/${run}`);
    } catch (error) {
      if (current !== token) return;
      pollError = error.detail || error.message;
      draw();
      return;
    }
    if (current !== token) return;
    detail = next;
    job = await runJob(detail, jobId);
    if (current !== token) return;
    draw();
    tick();
  }

  function tick() {
    if (!pollError && isActive(detail.status && detail.status.state)) later(current, poll, POLL_MS);
  }

  async function removeRun() {
    if (!await confirmDialog(`${run} çalıştırması ve ürettiği diyagram dosyaları kalıcı olarak silinsin mi?`,
      { danger: true, confirmLabel: 'Sil' })) return;
    try {
      await del(p`/api/projects/${slug}/runs/${run}`);
      toast('Çalıştırma silindi', 'ok');
      go('p', slug, 'diyagramlar');
    } catch (error) {
      toastError(error);
    }
  }

  function openViewer(diagram, url) {
    clear(viewer);
    viewer.append(h('div', { class: 'card dia-viewer' },
      h('div', { class: 'card-head' }, h('h2', { text: diagram.title || diagram.id }),
        h('a', { class: 'btn small', href: url, target: '_blank', rel: 'noopener' }, 'Yeni sekmede aç'),
        button('Kapat', () => clear(viewer), { kind: 'small' })),
      h('iframe', { class: 'viewer', src: url, title: `Diyagram: ${diagram.title || diagram.id}` })));
    viewer.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function diagramCard(diagram) {
    const [statusText, statusKind] = DIAGRAM_STATUS[diagram.status] || [diagram.status || 'bilinmiyor', 'muted'];
    const url = diagram.html ? fileUrl(slug, run, diagram.html) : null;
    return h('div', { class: 'card dia-card' },
      h('div', { class: 'dia-badges' },
        badge(KINDS[diagram.kind] || diagram.kind || '?', 'info'),
        badge(statusText, statusKind),
        diagram.quality ? badge(diagram.quality === 'showcase' ? 'vitrin' : 'standart', 'muted') : null,
        diagram.trimmed ? badge('bütçeye göre kırpıldı', 'warn') : null,
        diagram.repairs ? badge(`${diagram.repairs} etiket onarımı`, 'muted') : null),
      h('h2', { text: diagram.title || diagram.id || 'Diyagram' }),
      diagram.subtitle ? h('div', { class: 'small muted', text: diagram.subtitle }) : null,
      diagram.summary ? h('div', { class: 'small', text: diagram.summary }) : null,
      h('div', { class: 'dia-actions' }, url
        ? [button('Aç', () => openViewer(diagram, url), { kind: 'primary small' }),
          h('a', { class: 'btn small', href: url, target: '_blank', rel: 'noopener' }, 'Yeni sekmede aç')]
        : h('span', { class: 'small muted', text: 'HTML üretilemedi.' })));
  }

  function draw() {
    const status = detail.status || {};
    const report = detail.report;
    const state = status.state || (report ? 'done' : null);
    const active = isActive(state);
    clear(root);
    root.append(pageHeader({
      title: 'Diyagram çalıştırması',
      subtitle: h('span', {}, h('span', { class: 'mono', text: run }), ' · ', when(status.created_at || (report && report.created_at)),
        status.created_by ? ` · ${status.created_by}` : ''),
      crumbs,
      actions: [
        runStateBadge(state),
        report ? h('a', { class: 'btn', href: fileUrl(slug, run), target: '_blank', rel: 'noopener' }, 'Galeri') : null,
        state === 'done' ? h('a', { class: 'btn', href: `${link('p', slug, 'karsilastir')}?a=${enc(run)}&b=guncel` }, 'Güncel modelle karşılaştır') : null,
        editable ? button('Sil', () => removeRun(), { kind: 'danger', disabled: active, title: active ? 'Sürerken silinemez' : null }) : null,
      ],
    }));

    if (active) {
      const last = job && job.progress && job.progress.length ? job.progress[job.progress.length - 1].message : null;
      root.append(pollError
        ? h('div', { class: 'banner bad' }, h('span', { class: 'grow', text: `Durum alınamadı: ${pollError}` }),
          button('Yeniden dene', () => { pollError = null; draw(); poll(); }, { kind: 'small' }))
        : h('div', { class: 'banner info' }, h('span', { class: 'spinner' }),
          h('span', { class: 'grow' }, state === 'queued' ? 'Üretim sırada bekliyor…' : 'Diyagramlar üretiliyor…',
            last ? h('div', { class: 'small muted', text: last }) : null),
          h('span', { class: 'small muted', text: 'Sayfa kendiliğinden yenilenir.' })));
      return;
    }
    if (state === 'error') {
      root.append(h('div', { class: 'banner bad' }, h('span', { class: 'grow', text: `Üretim başarısız: ${status.error || 'bilinmeyen hata'}` })));
    }
    if (!report) {
      if (state !== 'error') root.append(h('div', { class: 'banner warn' }, h('span', { class: 'grow', text: 'Bu çalıştırmanın raporu bulunamadı.' })));
      return;
    }

    const diagrams = report.diagrams || [];
    const ready = diagrams.filter((d) => d.html).length;
    const summary = (report.validation && report.validation.summary) || {};
    root.append(h('div', { class: 'card', style: { marginBottom: '14px' } },
      h('dl', { class: 'kv' },
        h('dt', { text: 'Hazır diyagram' }), h('dd', { text: `${ready}/${diagrams.length}` }),
        h('dt', { text: 'Süre' }), h('dd', { text: duration(report.duration_s) }),
        h('dt', { text: 'Doküman' }), h('dd', { text: fmtNumber((report.inputs || []).length) }),
        h('dt', { text: 'Elle düzeltme' }), h('dd', { text: fmtNumber(report.corrections || 0) }),
        h('dt', { text: 'Doğrulama' }), h('dd', { text: `${fmtNumber(summary.error || 0)} hata, ${fmtNumber(summary.warning || 0)} uyarı` }),
        h('dt', { text: 'Motor' }), h('dd', { text: report.engine ? `${report.engine.name} ${report.engine.version}` : '—' }))));
    for (const note of report.notes || []) {
      root.append(h('div', { class: 'banner warn' }, h('span', { class: 'grow', text: note })));
    }
    root.append(viewer, diagrams.length
      ? h('div', { class: 'dia-cards' }, diagrams.map(diagramCard))
      : h('div', { class: 'card' }, empty('Bu çalıştırmada diyagram yok.')));
  }

  draw();
  tick();
}
