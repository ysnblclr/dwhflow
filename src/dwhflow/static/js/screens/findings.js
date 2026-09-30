// Doğrulama bulguları: özet, süzgeçler, bulgu tablosu; düzenleyici bulguyu nedenle yok sayar ya da geri alır.

import { get, p } from '../core/api.js';
import { debounce, fold, h } from '../core/dom.js';
import { link } from '../core/router.js';
import { canEdit } from '../core/store.js';
import { badge, button, dataTable, pageHeader, severityBadge, stat, when } from '../core/ui.js';
import * as edit from './_model_edit.js';

export const refreshOnChange = true;

const memory = { slug: null, filters: null };
const blankFilters = () => ({ severity: '', rule: '', q: '', ignored: false });
const SEVERITY_RANK = { error: 0, warning: 1, info: 2 };
const SEVERITY_OPTIONS = [{ value: 'error', label: 'hata' }, { value: 'warning', label: 'uyarı' }, { value: 'info', label: 'bilgi' }];

function subjectCell(slug, f) {
  if (f.subject && f.subject_type === 'dataset') return h('a', { href: link('p', slug, 'tablolar', f.subject), text: f.subject_name });
  if (f.subject && f.subject_type === 'process') return h('a', { href: link('p', slug, 'surecler', f.subject), text: f.subject_name });
  return h('span', { style: { overflowWrap: 'anywhere' }, text: f.subject_name || 'model' });
}

function ignoredCell(f) {
  if (!f.ignored) return null;
  const info = f.ignore;
  return h('div', {}, badge('yok sayıldı', 'muted'),
    info?.reason ? h('div', { class: 'small', text: info.reason }) : null,
    info ? h('div', { class: 'small muted' }, `${info.by || '—'} · `, when(info.at)) : null);
}

export async function render(root, { slug }) {
  if (memory.slug !== slug) { memory.slug = slug; memory.filters = blankFilters(); }
  const filters = memory.filters;
  const data = await get(p`/api/projects/${slug}/findings`);
  const summary = data.summary || {};

  const ignore = async (f) => {
    const values = await edit.formModal({
      title: 'Bulguyu yok say', submitLabel: 'Yok say',
      intro: `${f.rule} · ${f.subject_name || 'model'}: ${f.message}. Aynı kural ve özne için bulgu artık listelenmez; yok sayma geri alınabilir.`,
      fields: [{ name: 'neden', label: 'Neden', type: 'textarea', required: true, wide: true, placeholder: 'ör. bu dosya dış sistemden elle gelir, yazan süreç yok' }],
      onSubmit: async (v) => {
        await edit.sendOps(slug, [edit.op('bulgu_yoksay', { kural: f.rule, ozne: f.subject || '' }, null, v.neden)]);
        return v;
      },
    });
    if (values) edit.changed('Bulgu yok sayıldı');
  };

  root.append(pageHeader({
    title: 'Bulgular',
    subtitle: 'Modelin doğrulama bulguları: eksik yazan süreç, katman sırasına ters akış, doğrulanmamış bağ …',
    crumbs: [{ label: 'Pano', href: link('p', slug) }, { label: 'Bulgular' }],
  }));
  root.append(h('div', { class: 'grid-3', style: { marginBottom: '14px' } },
    stat(summary.error ?? 0, 'hata', { kind: summary.error ? 'bad' : 'ok' }),
    stat(summary.warning ?? 0, 'uyarı', { kind: summary.warning ? 'warn' : '' }),
    stat(summary.info ?? 0, 'bilgi'),
    stat(data.ignored ?? 0, 'yok sayılan')));

  const rules = [...new Set(data.items.map((f) => f.rule))].sort();
  const visible = () => {
    const needle = fold(filters.q);
    return data.items.filter((f) => (filters.ignored || !f.ignored)
      && (!filters.severity || f.severity === filters.severity)
      && (!filters.rule || f.rule === filters.rule)
      && (!needle || fold(`${f.rule} ${f.subject_name || ''} ${f.message}`).includes(needle)));
  };

  const columns = [
    { key: 'severity', label: 'Önem', render: (f) => severityBadge(f.severity), sort: (f) => SEVERITY_RANK[f.severity] ?? 9 },
    { key: 'rule', label: 'Kural', render: (f) => h('code', { text: f.rule }) },
    { key: 'subject_name', label: 'Özne', wrap: true, render: (f) => subjectCell(slug, f) },
    { key: 'message', label: 'Açıklama', wrap: true },
    { key: 'ignored', label: 'Durum', sort: (f) => (f.ignored ? 1 : 0), render: (f) => ignoredCell(f) },
  ];
  if (canEdit()) {
    columns.push({ key: 'actions', label: '', sort: false, render: (f) => {
      if (!f.ignored) return button('Yok say', edit.act(() => ignore(f)), { kind: 'small' });
      return f.ignore?.id ? button('Geri al', edit.act(() => edit.undoCorrection(slug, { ...f.ignore, label: 'Bulguyu yok say' })), { kind: 'small' }) : null;
    } });
  }
  const table = dataTable({
    rows: visible(), columns, filter: false,
    rowKey: (f) => `${f.rule}|${f.subject || ''}|${f.message}|${f.ignored}`,
    empty: data.items.length ? 'Süzgeçlerle eşleşen bulgu yok' : 'Bulgu yok: model doğrulamadan temiz geçti.',
  });
  const refresh = () => table.setRows(visible());

  const severity = h('select', { 'aria-label': 'Önem' }, h('option', { value: '' }, 'Tüm önem düzeyleri'),
    SEVERITY_OPTIONS.map((o) => h('option', { value: o.value }, o.label)));
  severity.value = filters.severity;
  severity.addEventListener('change', () => { filters.severity = severity.value; refresh(); });
  const rule = h('select', { 'aria-label': 'Kural' }, h('option', { value: '' }, 'Tüm kurallar'),
    rules.map((r) => h('option', { value: r }, `${r} (${data.items.filter((f) => f.rule === r && !f.ignored).length})`)));
  rule.value = rules.includes(filters.rule) ? filters.rule : '';
  filters.rule = rule.value;
  rule.addEventListener('change', () => { filters.rule = rule.value; refresh(); });
  const text = h('input', { type: 'search', placeholder: 'Ara…', 'aria-label': 'Bulgularda ara', value: filters.q });
  text.addEventListener('input', debounce(() => { filters.q = text.value.trim(); refresh(); }, 200));
  const showIgnored = h('input', { type: 'checkbox', checked: filters.ignored });
  showIgnored.addEventListener('change', () => { filters.ignored = showIgnored.checked; refresh(); });

  root.append(h('div', { class: 'toolbar' }, severity, rule, text,
    h('label', { class: 'field inline' }, showIgnored, h('span', { text: `Yok sayılanları göster (${data.ignored ?? 0})` }))));
  root.append(table.el);
  edit.restoreScroll();
}
