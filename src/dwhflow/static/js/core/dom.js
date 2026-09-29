// DOM yardımcıları. Metin her zaman textContent ile yazılır (HTML enjeksiyonu yok).

const BOOL_ATTRS = new Set(['disabled', 'checked', 'hidden', 'selected', 'required', 'readonly', 'multiple', 'open']);

/**
 * h('div', {class: 'x', onclick: fn, dataset: {id: 1}}, 'metin', h('span'), [liste])
 * - on* anahtarları olay dinleyicisi olur, style nesne ya da metin olabilir.
 * - null/undefined/false çocuklar atlanır; diziler düzleştirilir.
 */
export function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = Array.isArray(value) ? value.filter(Boolean).join(' ') : value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'style' && typeof value === 'object') Object.assign(node.style, value);
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
    else if (key === 'value') node.value = value;
    else if (BOOL_ATTRS.has(key)) { if (value) node.setAttribute(key, ''); node[key] = !!value; }
    else node.setAttribute(key, value === true ? '' : value);
  }
  append(node, children);
  return node;
}

export function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

/** Türkçe duyarsız karşılaştırma anahtarı: "İŞLEM" ~ "islem". */
export function fold(text) {
  return String(text ?? '')
    .replace(/[İIı]/g, 'i').replace(/[Şş]/g, 's').replace(/[Ğğ]/g, 'g').replace(/[Üü]/g, 'u')
    .replace(/[Öö]/g, 'o').replace(/[Çç]/g, 'c')
    .toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '');
}

export function fmtDate(iso) {
  if (!iso) return '—';
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
  return m ? `${m[3]}.${m[2]}.${m[1]} ${m[4]}:${m[5]}` : String(iso);
}

export function fmtSize(bytes) {
  if (bytes == null) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function fmtNumber(n) {
  return n == null ? '—' : Number(n).toLocaleString('tr-TR');
}

export function debounce(fn, ms = 250) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

export function plural(n, word) {
  return `${fmtNumber(n)} ${word}`;
}
