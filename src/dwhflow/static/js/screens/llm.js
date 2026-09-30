// Yapay zekâ: test konsolu (gönderim önizlemesi dahil), istem kütüphanesi, maskeleme önizlemesi.

import { get, p, post } from '../core/api.js';
import { append, clear, h } from '../core/dom.js';
import { canEdit, isAdmin, store } from '../core/store.js';
import { badge, button, empty, pageHeader, tabs, toast, toastError, when } from '../core/ui.js';

const TYPE_TR = { dataset: 'tablo', process: 'süreç', flow: 'akış', dependency: 'bağımlılık' };

function pre(text) {
  return h('pre', { class: 'mono', style: { whiteSpace: 'pre-wrap', background: 'var(--line-2)', padding: '10px',
    borderRadius: '8px', maxHeight: '320px', overflow: 'auto', margin: '6px 0' } }, text ?? '');
}

function describeItem(entry) {
  const item = entry.item || entry;
  if (item.source && item.target) return `${item.source} → ${item.target}${item.process ? ` (${item.process})` : ''}`;
  if (item.before && item.after) return `${item.before} → ${item.after} (önce çalışır)`;
  if (item.header) return `${item.header} → ${item.role}`;
  return item.name || JSON.stringify(item);
}

// ---- test konsolu ------------------------------------------------------- //

async function consoleTab(root, prompts, system) {
  const profiles = system.llm_profiles?.length ? system.llm_profiles : ['yerel'];
  const promptSelect = h('select', {}, prompts.map((x) => h('option', { value: x.id, text: x.title || x.id })));
  const versionSelect = h('select');
  const profileSelect = h('select', {}, profiles.map((name) => h('option', { value: name, text: name })));
  const mask = h('input', { type: 'checkbox', checked: true });
  const text = h('textarea', { rows: 8, placeholder: 'Dokümandan bir paragraf yapıştırın. Tablo başlığı eşleme için: başlık satırı (| ile ayrılmış) ve altında en çok 3 örnek satır.' });
  const out = h('div', { class: 'stack' });

  const fillVersions = () => {
    const prompt = prompts.find((x) => x.id === promptSelect.value);
    clear(versionSelect);
    versionSelect.append(h('option', { value: '', text: `en yeni (v${prompt?.latest ?? '?'})` }));
    for (const v of prompt?.versions || []) {
      versionSelect.append(h('option', { value: String(v.version), text: `v${v.version} · ${v.origin === 'user' ? 'kullanıcı' : 'yerleşik'}` }));
    }
  };
  promptSelect.addEventListener('change', fillVersions);
  fillVersions();

  const run = async (dryRun) => {
    if (!text.value.trim()) { toast('Önce bir metin girin', 'warn'); return; }
    clear(out);
    out.append(h('div', { class: 'loading' }, h('span', { class: 'spinner' }), dryRun ? ' Hazırlanıyor…' : ' Model çalışıyor…'));
    try {
      const body = { text: text.value, prompt_id: promptSelect.value, profile: profileSelect.value,
        mask: mask.checked, dry_run: dryRun };
      if (versionSelect.value) body.version = Number(versionSelect.value);
      if (store.project) body.project = store.project;
      renderResult(await post('/api/llm/console', body), dryRun);
    } catch (error) { clear(out); toastError(error); }
  };

  const renderResult = (r, dryRun) => {
    clear(out);
    const masked = r.masked || {};
    const counts = Object.entries(masked.counts || {}).filter(([, n]) => n);
    append(out, [
      h('div', { class: 'row' },
        r.prompt ? badge(`${r.prompt.id} v${r.prompt.version}`, 'info') : null,
        masked.applied ? badge(masked.forced ? 'maskeleme zorunlu (dış adres)' : 'maskelendi', 'warn') : badge('maskeleme kapalı', 'muted'),
        counts.length ? h('span', { class: 'small muted', text: counts.map(([k, n]) => `${k}: ${n}`).join(', ') }) : null,
        r.duration_ms ? h('span', { class: 'small muted', text: `${r.duration_ms} ms` }) : null),
      r.warning ? h('div', { class: 'banner warn', text: r.warning }) : null,
      r.error ? h('div', { class: 'banner bad', text: r.error }) : null,
      h('h3', { text: dryRun ? 'Modele gidecek mesaj' : 'Gönderilen mesaj' }), pre(r.sent),
    ]);
    if (dryRun || (r.error && !r.raw)) return;
    append(out, [
      h('h3', { text: 'Ham yanıt' }), pre(r.raw),
      h('h3', { text: `Kabul edilenler (${r.accepted.length})` }),
      r.accepted.length ? h('ul', { class: 'list' }, r.accepted.map((a) => h('li', {}, badge(TYPE_TR[a.type] || a.type || 'öğe', 'ok'),
        h('span', { class: 'grow', text: describeItem(a) }), a.dropped?.length ? h('span', { class: 'small muted', text: `atılan: ${a.dropped.join(', ')}` }) : null)))
        : h('p', { class: 'muted', text: 'Kabul edilen öğe yok.' }),
      h('h3', { text: `Reddedilenler (${r.rejected.length})` }),
      r.rejected.length ? h('ul', { class: 'list' }, r.rejected.map((x) => h('li', { style: { display: 'block' } },
        h('b', { text: describeItem(x) }), h('div', { class: 'small', style: { color: 'var(--bad)' }, text: x.reason || x.code })))) : h('p', { class: 'muted', text: 'Reddedilen öğe yok.' }),
      r.unresolved?.length ? [h('h3', { text: 'İnsan bakmalı' }), h('ul', { class: 'list' }, r.unresolved.map((u) => h('li', { style: { display: 'block' } }, h('div', { text: u.text }), h('div', { class: 'small muted', text: u.reason }))))] : null,
      r.synonyms && Object.keys(r.synonyms).length ? [h('h3', { text: 'Onaylanırsa eklenecek eş anlamlılar' }), pre(JSON.stringify(r.synonyms, null, 2))] : null,
      r.daf ? [h('h3', { text: 'Ara format (DAF)' }), pre(JSON.stringify(r.daf, null, 2))] : null,
    ]);
  };

  root.append(h('div', { class: 'card stack' },
    h('p', { class: 'muted', text: 'Bir paragrafı seçilen istemle dener; modele hiçbir şey yazılmaz. "Gönderim önizlemesi" modeli çağırmadan, maskelenmiş hâliyle gidecek mesajı gösterir.' }),
    h('div', { class: 'form-grid' },
      h('label', { class: 'field' }, h('span', { class: 'label', text: 'İstem' }), promptSelect),
      h('label', { class: 'field' }, h('span', { class: 'label', text: 'Sürüm' }), versionSelect),
      h('label', { class: 'field' }, h('span', { class: 'label', text: 'LLM profili' }), profileSelect),
      h('label', { class: 'field inline' }, mask, h('span', { text: 'Kişisel veriyi maskele' }))),
    text,
    h('div', { class: 'row' },
      button('Gönderim önizlemesi', () => run(true)),
      canEdit() ? button('Çalıştır', () => run(false), { kind: 'primary' }) : h('span', { class: 'small muted', text: 'Çalıştırmak için düzenleyici yetkisi gerekir.' })),
    out));
}

// ---- istem kütüphanesi -------------------------------------------------- //

async function promptsTab(root, prompts) {
  const detail = h('div', { class: 'stack' });
  const show = async (id, version) => {
    clear(detail);
    let prompt;
    try { prompt = await get(p`/api/llm/prompts/${id}` + (version ? `?version=${version}` : '')); } catch (error) { toastError(error); return; }
    const info = prompts.find((x) => x.id === id);
    append(detail, [
      h('div', { class: 'card-head' }, h('h2', { text: `${id} · v${prompt.version}` }),
        badge(prompt.origin === 'user' ? 'kullanıcı sürümü' : 'yerleşik', prompt.origin === 'user' ? 'info' : 'muted'),
        isAdmin() ? button('Bu sürümden yeni sürüm', () => edit(prompt, info)) : null),
      h('p', { class: 'small muted', text: `${prompt.task || ''}${prompt.author ? ` · ${prompt.author}` : ''}` }),
      prompt.created ? h('p', { class: 'small muted' }, 'Oluşturma: ', when(prompt.created)) : null,
      h('h3', { text: 'Sistem metni' }), pre(prompt.system),
      h('h3', { text: 'Kullanıcı şablonu' }), pre(prompt.user),
      h('h3', { text: `Örnekler (${(prompt.fewshot || []).length})` }),
      (prompt.fewshot || []).map((shot, i) => h('div', { class: 'card', style: { marginBottom: '8px' } },
        h('b', { text: `Örnek ${i + 1}` }), pre(shot.user), pre(shot.assistant))),
    ]);
  };

  const edit = (prompt, info) => {
    clear(detail);
    const system = h('textarea', { rows: 14 }); system.value = prompt.system || '';
    const user = h('textarea', { rows: 5 }); user.value = prompt.user || '';
    const notes = h('input', { type: 'text', placeholder: 'Bu sürümde ne değişti?' });
    const shots = (prompt.fewshot || []).map((s) => ({ user: s.user, assistant: s.assistant }));
    const shotBox = h('div', { class: 'stack' });
    const drawShots = () => {
      clear(shotBox);
      shots.forEach((shot, i) => {
        const u = h('textarea', { rows: 4 }); u.value = shot.user; u.addEventListener('input', () => { shot.user = u.value; });
        const a = h('textarea', { rows: 4 }); a.value = shot.assistant; a.addEventListener('input', () => { shot.assistant = a.value; });
        shotBox.append(h('div', { class: 'card stack' }, h('div', { class: 'row' }, h('b', { class: 'grow', text: `Örnek ${i + 1}` }),
          button('Kaldır', () => { shots.splice(i, 1); drawShots(); }, { kind: 'small danger' })),
        h('label', { class: 'field' }, h('span', { class: 'label', text: 'Kullanıcı' }), u),
        h('label', { class: 'field' }, h('span', { class: 'label', text: 'Beklenen yanıt (JSON)' }), a)));
      });
    };
    drawShots();
    const placeholders = info?.placeholders;
    append(detail, [
      h('h2', { text: `${prompt.id}: yeni sürüm (v${prompt.version} temel alınarak)` }),
      placeholders ? h('p', { class: 'small muted', text: `Zorunlu yer tutucular: ${placeholders.required.map((x) => `{${x}}`).join(', ')} · izinli: ${placeholders.allowed.map((x) => `{${x}}`).join(', ')}` }) : null,
      h('label', { class: 'field' }, h('span', { class: 'label', text: 'Sistem metni' }), system),
      h('label', { class: 'field' }, h('span', { class: 'label', text: 'Kullanıcı şablonu' }), user),
      h('label', { class: 'field' }, h('span', { class: 'label', text: 'Not' }), notes),
      h('h3', { text: 'Örnekler' }), shotBox,
      h('div', { class: 'row' }, button('+ Örnek ekle', () => { shots.push({ user: '', assistant: '' }); drawShots(); }),
        h('span', { class: 'grow' }), button('Vazgeç', () => show(prompt.id, prompt.version)),
        button('Kaydet', async () => {
          try {
            const saved = await post(p`/api/llm/prompts/${prompt.id}`, { prompt: {
              ...prompt, system: system.value, user: user.value, fewshot: shots, notes: notes.value || null } });
            toast(`${prompt.id} v${saved.version} kaydedildi. Sonraki LLM okumaları bu sürümü kullanır.`, 'ok');
            location.reload();
          } catch (error) { toastError(error); }
        }, { kind: 'primary' })),
    ]);
  };

  const list = h('div', { class: 'stack' }, prompts.map((prompt) => h('div', { class: 'card' },
    h('div', { class: 'card-head' }, h('h2', { text: prompt.title || prompt.id }), badge(`en yeni v${prompt.latest}`, 'info')),
    h('ul', { class: 'list' }, prompt.versions.map((v) => h('li', {},
      h('a', { href: '#', onclick: (e) => { e.preventDefault(); show(prompt.id, v.version); }, text: `v${v.version}` }),
      badge(v.origin === 'user' ? 'kullanıcı' : 'yerleşik', v.origin === 'user' ? 'info' : 'muted'),
      h('span', { class: 'small muted grow', text: [v.author, v.notes].filter(Boolean).join(' · ') }),
      v.created ? when(v.created) : null))),
    prompt.errors?.length ? h('div', { class: 'banner warn', text: prompt.errors.join('; ') }) : null)));

  root.append(h('div', { class: 'split' }, list, h('div', { class: 'card detail' }, detail)));
  append(detail, [empty('Görüntülemek için bir sürüm seçin.')]);
}

// ---- maskeleme ---------------------------------------------------------- //

function maskTab(root) {
  const input = h('textarea', { rows: 6, placeholder: 'Ör. Müşteri 10000000146 numaralı TCKN ve TR33 0006 1005 1978 6457 8413 26 IBAN ile…' });
  const out = h('div');
  root.append(h('div', { class: 'card stack' },
    h('p', { class: 'muted', text: 'LLM\'e gönderilmeden önce kişisel verilerin (TCKN, IBAN, kart numarası, e-posta, telefon) nasıl maskeleneceğini gösterir. Dış bir LLM adresinde maskeleme her zaman uygulanır.' }),
    input, h('div', {}, button('Maskele', async () => {
      try {
        const r = await post('/api/llm/mask', { text: input.value });
        clear(out);
        const counts = Object.entries(r.counts || {}).filter(([, n]) => n);
        append(out, [pre(r.masked), h('p', { class: 'small muted', text: counts.length ? counts.map(([k, n]) => `${k}: ${n}`).join(', ') : 'Maskelenecek veri bulunmadı.' })]);
      } catch (error) { toastError(error); }
    }, { kind: 'primary' })), out));
}

// ---- ekran -------------------------------------------------------------- //

export async function render(root, params, query) {
  const [prompts, system] = await Promise.all([get('/api/llm/prompts'), get('/api/system')]);
  const active = query?.sekme || 'konsol';
  root.append(pageHeader({
    title: 'Yapay zekâ',
    subtitle: 'İsteğe bağlıdır: kuralların çözemediği paragraflar ve tanınmayan tablo başlıkları için. Bağlantı profilleri Ayarlar › Genel ayarlar › Yapay zekâ bölümündedir.',
    actions: [isAdmin() ? h('a', { class: 'btn', href: '#/ayarlar' }, 'LLM profilleri') : null],
  }));
  const body = h('div');
  root.append(tabs([{ id: 'konsol', label: 'Test konsolu' }, { id: 'istemler', label: 'İstem kütüphanesi' },
    { id: 'maske', label: 'Maskeleme' }], active, (id) => { location.hash = `#/yz?sekme=${id}`; }), body);
  if (active === 'istemler') await promptsTab(body, prompts);
  else if (active === 'maske') maskTab(body);
  else await consoleTab(body, prompts, system);
}
