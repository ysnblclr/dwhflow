// DWHFlow arayüzü: doküman yükle → dönüştür → sonuçları aç. Framework yok, derleme yok.

const $ = (id) => document.getElementById(id);
const HEADERS = { 'X-DWHFlow': '1' }; // değişiklik yapan istekler için zorunlu (CSRF koruması)

let accepted = [];
let readable = [];
let selected = [];
let llmEnabled = false;
let pollTimer = null;

// ---------------------------------------------------------------- yardımcılar

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(typeof child === 'string' ? document.createTextNode(child) : child);
  }
  return node;
}

async function api(path, { method = 'GET', body, form } = {}) {
  const options = { method, headers: { ...(method !== 'GET' ? HEADERS : {}) } };
  if (form) options.body = form;
  else if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  let data = null;
  try { data = await response.json(); } catch { /* boş yanıt */ }
  if (!response.ok) throw new Error((data && data.detail) || response.statusText);
  return data;
}

const STATE_TR = { queued: 'sırada', running: 'sürüyor', done: 'tamamlandı', error: 'hata' };

function when(stamp) {
  // ISO → "28.09.2026 22:23" (sunucu yerel saatiyle yazar; saat dilimi eklenmez)
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(stamp || '');
  return m ? `${m[3]}.${m[2]}.${m[1]} ${m[4]}:${m[5]}` : (stamp || '');
}

function size(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

function ext(name) {
  const index = name.lastIndexOf('.');
  return index >= 0 ? name.slice(index).toLowerCase() : '';
}

function showError(target, message) {
  target.hidden = !message;
  target.className = 'alert';
  target.textContent = message || '';
}

// ---------------------------------------------------------------- durum

async function loadHealth() {
  try {
    const health = await api('/api/health');
    accepted = health.accepted;
    readable = health.readable;
    $('accepted-hint').textContent = `Okunan: ${readable.join(' ')}`;
    $('file-input').setAttribute('accept', accepted.join(','));
    const chip = $('engine-chip');
    if (health.engine.ok) {
      chip.className = 'chip ok';
      chip.textContent = `Archify ${health.engine.version} · Node ${health.node.major}`;
    } else {
      chip.className = 'chip bad';
      chip.textContent = 'Diyagram motoru hazır değil';
      chip.title = health.engine.error || '';
    }
    llmEnabled = health.llm.enabled;
    $('llm-chip').textContent = llmEnabled ? `YZ: ${health.llm.model}` : 'YZ kapalı';
    $('llm-chip').className = llmEnabled ? 'chip ok' : 'chip';
    $('use-llm').disabled = !llmEnabled;
    $('llm-switch').classList.toggle('disabled', !llmEnabled);
    $('llm-switch').title = llmEnabled ? '' : 'Ayarlar\'dan yapay zekâyı etkinleştir';
  } catch (error) {
    $('engine-chip').className = 'chip bad';
    $('engine-chip').textContent = `sunucuya ulaşılamadı: ${error.message}`;
  }
}

// ---------------------------------------------------------------- dosya seçimi

function addFiles(list) {
  for (const file of list) {
    if (!selected.some((f) => f.name === file.name && f.size === file.size)) selected.push(file);
  }
  renderFiles();
}

function renderFiles() {
  const list = $('file-list');
  list.replaceChildren();
  for (const [index, file] of selected.entries()) {
    const known = readable.includes(ext(file.name));
    list.append(el('li', {}, [
      el('span', { class: 'name', text: file.name, title: file.name }),
      el('span', { class: `kind ${known ? '' : 'no'}`, text: known ? ext(file.name) : 'okunamaz' ,
        title: known ? '' : 'Bu tür henüz okunamıyor; sonuç sayfasında nedeni yazacak' }),
      el('span', { class: 'size', text: size(file.size) }),
      el('button', { type: 'button', title: 'Kaldır', text: '✕',
        onclick: () => { selected.splice(index, 1); renderFiles(); } }),
    ]));
  }
  $('btn-run').disabled = selected.length === 0;
  $('run-hint').textContent = selected.length
    ? `${selected.length} doküman seçildi.` : 'Önce doküman ekle.';
}

const zone = $('dropzone');
zone.addEventListener('click', () => $('file-input').click());
zone.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); $('file-input').click(); }
});
zone.addEventListener('dragover', (event) => { event.preventDefault(); zone.classList.add('hot'); });
zone.addEventListener('dragleave', () => zone.classList.remove('hot'));
zone.addEventListener('drop', (event) => {
  event.preventDefault();
  zone.classList.remove('hot');
  addFiles(event.dataTransfer.files);
});
$('file-input').addEventListener('change', (event) => { addFiles(event.target.files); event.target.value = ''; });

// ---------------------------------------------------------------- dönüştürme

$('btn-run').addEventListener('click', async () => {
  showError($('run-error'), '');
  const form = new FormData();
  for (const file of selected) form.append('files', file, file.name);
  form.append('title', $('title').value);
  form.append('quality', $('quality').value);
  form.append('paths', $('paths').value);
  form.append('use_llm', $('use-llm').checked ? 'true' : 'false');
  $('btn-run').disabled = true;
  try {
    const { id } = await api('/api/runs', { method: 'POST', form });
    selected = [];
    renderFiles();
    watch(id);
  } catch (error) {
    showError($('run-error'), error.message);
    $('btn-run').disabled = false;
  }
});

function watch(id) {
  clearInterval(pollTimer);
  $('progress').hidden = false;
  $('result').hidden = true;
  $('progress-log').replaceChildren();
  const tick = async () => {
    let status;
    try { status = await api(`/api/runs/${id}`); } catch { return; }
    $('progress-title').textContent = status.state === 'queued' ? 'Sırada…' : 'Dönüştürülüyor…';
    const log = $('progress-log');
    log.replaceChildren(...(status.progress || []).map((item) =>
      el('li', { text: item.detail })));
    if (status.state === 'done' || status.state === 'error') {
      clearInterval(pollTimer);
      if (status.state === 'error') {
        $('progress-title').textContent = 'Dönüştürme başarısız';
        log.append(el('li', { class: 'error', text: status.error || 'bilinmeyen hata' }));
      } else {
        $('progress').hidden = true;
        showResult(id, status);
      }
      loadRuns();
    }
  };
  tick();
  pollTimer = setInterval(tick, 1000);
}

const STATUS = {
  pass: ['ok', 'Tüm kapılar geçti'],
  skipped: ['warn', 'Tarayıcı kontrolü atlandı'],
  fail: ['bad', 'Üretilemedi'],
  error: ['bad', 'Motor hatası'],
};

function showResult(id, status) {
  const report = status.report || {};
  $('result').hidden = false;
  $('result-title').textContent = report.title || status.title || 'Sonuç';
  $('result-open').href = `/runs/${id}/index.html`;
  const grid = $('result-diagrams');
  grid.replaceChildren();
  const diagrams = report.diagrams || [];
  if (!diagrams.length) {
    grid.append(el('p', { class: 'muted', text: (report.notes || []).join(' ') || 'Diyagram üretilmedi.' }));
  }
  for (const item of diagrams) {
    const [kind, label] = STATUS[item.status] || ['bad', item.status];
    grid.append(el('div', { class: 'diagram' }, [
      el('h4', { text: item.title }),
      el('p', {}, [el('span', { class: `pill ${kind}`, text: label })]),
      item.subtitle ? el('p', { text: item.subtitle }) : null,
      item.html ? el('a', { class: 'btn', href: `/runs/${id}/${item.html}`, target: '_blank',
        rel: 'noopener', text: 'Aç' }) : null,
    ]));
  }
  const docs = $('result-docs');
  docs.replaceChildren(el('h3', { text: 'Dokümanlar' }));
  for (const doc of (report.ingest || {}).documents || []) {
    const summary = `${doc.file} — ${doc.datasets} tablo, ${doc.processes} süreç, ${doc.flows} akış`;
    docs.append(el('details', {}, [
      el('summary', { text: summary + (doc.status !== 'ok' ? ` (${doc.status})` : '') }),
      doc.notes.length ? el('ul', {}, doc.notes.map((note) => el('li', { text: note }))) : null,
    ]));
  }
}

// ---------------------------------------------------------------- geçmiş

async function loadRuns() {
  const box = $('runs');
  let runs;
  try { runs = await api('/api/runs'); } catch (error) {
    box.replaceChildren(el('p', { class: 'muted', text: error.message }));
    return;
  }
  if (!runs.length) {
    box.replaceChildren(el('p', { class: 'muted', text: 'Henüz dönüştürme yok.' }));
    return;
  }
  box.replaceChildren(...runs.map((run) => el('div', { class: 'run-row' }, [
    el('div', { class: 'title' }, [
      el('b', { text: run.title }),
      el('div', { class: 'meta', text: `${when(run.created_at)} · ${run.documents} doküman · `
        + `${run.ready}/${run.total} diyagram · ${STATE_TR[run.state] || run.state}` }),
    ]),
    run.state === 'done' ? el('a', { class: 'btn', href: `/runs/${run.id}/index.html`,
      target: '_blank', rel: 'noopener', text: 'Aç' }) : null,
    el('button', { class: 'btn', type: 'button', text: 'Sil', onclick: async () => {
      if (!confirm(`"${run.title}" dönüştürmesi silinsin mi? Bu geri alınamaz.`)) return;
      await api(`/api/runs/${run.id}`, { method: 'DELETE' });
      loadRuns();
    } }),
  ])));
}

// ---------------------------------------------------------------- ayarlar

async function openSettings() {
  const settings = await api('/api/settings');
  const llm = settings.llm;
  $('llm-enabled').checked = llm.enabled;
  $('llm-url').value = llm.base_url;
  $('llm-model').value = llm.model;
  $('llm-key-env').value = llm.api_key_env;
  $('llm-timeout').value = llm.timeout_s;
  $('llm-external').checked = llm.allow_external_api;
  $('settings-msg').hidden = true;
  $('settings').showModal();
}

function readSettings() {
  return {
    llm: {
      enabled: $('llm-enabled').checked,
      base_url: $('llm-url').value.trim(),
      model: $('llm-model').value.trim(),
      api_key_env: $('llm-key-env').value.trim(),
      timeout_s: Number($('llm-timeout').value) || 180,
      allow_external_api: $('llm-external').checked,
    },
  };
}

$('btn-settings').addEventListener('click', () => openSettings().catch((e) => alert(e.message)));
$('btn-save').addEventListener('click', async () => {
  const msg = $('settings-msg');
  try {
    await api('/api/settings', { method: 'PUT', body: readSettings() });
    msg.hidden = false; msg.className = 'alert ok'; msg.textContent = 'Kaydedildi.';
    loadHealth();
  } catch (error) { showError(msg, error.message); }
});
$('btn-llm-test').addEventListener('click', async () => {
  const msg = $('settings-msg');
  try {
    // Test, KAYITLI ayarla yapılır; önce kaydet.
    await api('/api/settings', { method: 'PUT', body: readSettings() });
    msg.hidden = false; msg.className = 'alert'; msg.textContent = 'deneniyor…';
    const result = await api('/api/llm/test', { method: 'POST' });
    msg.className = result.ok ? 'alert ok' : 'alert';
    msg.textContent = result.ok
      ? [`Bağlandı (HTTP ${result.status}).`, result.models?.length ? `Modeller: ${result.models.join(', ')}` : '',
        result.hint || ''].filter(Boolean).join('\n')
      : (result.error || `HTTP ${result.status}`);
    loadHealth();
  } catch (error) { showError(msg, error.message); }
});

loadHealth().then(loadRuns);
