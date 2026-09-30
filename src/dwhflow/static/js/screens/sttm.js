// STTM (kaynaktan hedefe kolon eşlemeleri): süzülebilir tablo, elle eşleme ekleme/silme ve CSV indirme.

import { get, p } from '../core/api.js';
import { h } from '../core/dom.js';
import { link } from '../core/router.js';
import { canEdit, label } from '../core/store.js';
import { badge, button, dataTable, pageHeader, statusBadge } from '../core/ui.js';
import * as edit from './_model_edit.js';

export const refreshOnChange = true;

const CSV_HEADERS = ['Kaynak tablo', 'Kaynak kolon', 'Hedef tablo', 'Hedef kolon', 'Dönüşüm', 'Durum', 'Elle', 'Kaynak dosya', 'Konum'];

/** Noktalı virgüllü CSV hücresi. Formül gibi başlayan değerlere ' eklenir (elektronik tabloda çalışmasın). */
function csvCell(value) {
  let text = value == null ? '' : String(value);
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return /[;"\r\n]/.test(text) || text !== text.trim() ? `"${text.replace(/"/g, '""')}"` : text;
}

function downloadCsv(slug, rows) {
  const lines = [CSV_HEADERS, ...rows.map((r) => [
    r.source_table, r.source_column, r.target_table, r.target_column, r.expression || '', label.status(r.status),
    r.manual ? 'evet' : '', r.sources[0]?.file || '', r.sources[0]?.locator || '',
  ])].map((cells) => cells.map(csvCell).join(';'));
  // UTF-8 BOM: Excel Türkçe karakterleri doğru açsın.
  const blob = new Blob([`﻿${lines.join('\r\n')}\r\n`], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = h('a', { href: url, download: `${slug}-sttm.csv`, class: 'hidden' });
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

const refOf = (table, column) => `${table}#${column}`;

function sourceCell(slug, sources) {
  const first = sources[0];
  if (!first) return '—';
  const where = first.file ? edit.docLink(slug, first.file) : (first.extractor || '—');
  return h('span', { class: 'small' }, where, first.locator ? h('span', { class: 'muted', text: ` · ${first.locator}` }) : null,
    sources.length > 1 ? h('span', { class: 'muted', text: ` (+${sources.length - 1})` }) : null);
}

export async function render(root, { slug }) {
  const [rows, datasets] = await Promise.all([
    get(p`/api/projects/${slug}/sttm`),
    edit.loadAll(slug, 'datasets').catch(() => []),
  ]);
  const ids = new Map(datasets.map((d) => [d.name, d.id]));
  const tableLink = (name) => (ids.has(name) ? h('a', { href: link('p', slug, 'tablolar', ids.get(name)), text: name }) : name);

  const addMapping = async () => {
    const names = datasets.map((d) => d.name);
    const values = await edit.formModal({
      title: 'Kolon eşlemesi ekle', submitLabel: 'Ekle',
      intro: 'Tablolar modelde bulunmalıdır. Kolon adları büyük harfe çevrilir; tabloda olmayan kolon eklenir.',
      fields: [
        { name: 'kt', label: 'Kaynak tablo', required: true, suggestions: names },
        { name: 'kk', label: 'Kaynak kolon', required: true },
        { name: 'ht', label: 'Hedef tablo', required: true, suggestions: names },
        { name: 'hk', label: 'Hedef kolon', required: true },
        { name: 'ifade', label: 'Dönüşüm ifadesi', wide: true, placeholder: 'ör. doğrudan, TRIM(AD), CASE …' },
        edit.reasonField(),
      ],
      validate: (v) => (v.kk.includes('#') || v.hk.includes('#') ? "Kolon adı '#' içeremez" : null),
      onSubmit: async (v) => {
        const ifade = v.ifade.trim();
        await edit.sendOps(slug, [edit.op('kolon_esle',
          { kaynak: refOf(v.kt.trim(), v.kk.trim()), hedef: refOf(v.ht.trim(), v.hk.trim()) }, ifade ? { ifade } : null, v.neden)]);
        return v;
      },
    });
    if (values) edit.changed('Kolon eşlemesi eklendi');
  };

  const remove = async (row) => {
    const source = refOf(row.source_table, row.source_column);
    const target = refOf(row.target_table, row.target_column);
    const values = await edit.formModal({
      title: 'Kolon eşlemesini sil', submitLabel: 'Sil', danger: true,
      intro: `${source} → ${target}${row.expression ? ` (${row.expression})` : ''} eşlemesi modelden kaldırılsın mı? Düzeltme günlükten geri alınabilir.`,
      fields: [edit.reasonField()],
      onSubmit: async (v) => {
        await edit.sendOps(slug, [edit.op('bag_sil', { kaynak: source, hedef: target, tur: 'column_maps_to' }, null, v.neden)]);
        return v;
      },
    });
    if (values) edit.changed('Kolon eşlemesi silindi');
  };

  root.append(pageHeader({
    title: 'STTM — kolon eşlemeleri',
    subtitle: `${rows.length} eşleme: hangi hedef kolonun hangi kaynak kolondan, hangi dönüşümle beslendiği.`,
    crumbs: [{ label: 'Pano', href: link('p', slug) }, { label: 'STTM' }],
    actions: [
      button('CSV indir', () => downloadCsv(slug, rows), { disabled: !rows.length, title: 'Tüm eşlemeler; noktalı virgülle ayrılmış, UTF-8' }),
      canEdit() ? button('+ Eşleme ekle', edit.act(addMapping), { kind: 'primary' }) : null,
    ],
  }));

  const columns = [
    { key: 'source_table', label: 'Kaynak tablo', render: (r) => tableLink(r.source_table) },
    { key: 'source_column', label: 'Kaynak kolon', render: (r) => h('span', { class: 'mono', text: r.source_column }) },
    { key: 'target_table', label: 'Hedef tablo', render: (r) => tableLink(r.target_table) },
    { key: 'target_column', label: 'Hedef kolon', render: (r) => h('span', { class: 'mono', text: r.target_column }) },
    { key: 'expression', label: 'Dönüşüm', wrap: true, render: (r) => r.expression || '—' },
    { key: 'status', label: 'Durum', sort: (r) => label.status(r.status),
      render: (r) => h('span', { class: 'nowrap' }, statusBadge(r.status), r.manual ? [' ', badge('elle', 'info')] : null) },
    { key: 'kaynak', label: 'Kaynak', sort: (r) => r.sources[0]?.file || '', render: (r) => sourceCell(slug, r.sources) },
  ];
  if (canEdit()) {
    columns.push({ key: 'actions', label: '', sort: false, render: (r) => h('button', {
      class: 'icon-btn', type: 'button', title: 'Eşlemeyi sil', 'aria-label': 'Eşlemeyi sil',
      onclick: edit.act(() => remove(r)),
    }, '🗑') });
  }
  const table = dataTable({
    rows, columns,
    rowKey: (r) => `${refOf(r.source_table, r.source_column)}>${refOf(r.target_table, r.target_column)}`,
    empty: 'Henüz kolon eşlemesi yok. STTM dokümanı yükleyin ya da elle ekleyin.',
  });
  root.append(table.el);
  edit.restoreScroll();
}
