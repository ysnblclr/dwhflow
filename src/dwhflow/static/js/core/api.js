// API istemcisi. Değişiklik yapan her isteğe X-DWHFlow başlığı eklenir (sunucu CSRF koruması).
// Oturum düşerse (401) 'dwhflow:auth' olayı yayılır; ana modül giriş ekranını gösterir.

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

function detailOf(body, status) {
  if (!body) return `HTTP ${status}`;
  const d = body.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map((e) => `${(e.loc || []).slice(1).join('.')}: ${e.msg}`).join('; ');
  return `HTTP ${status}`;
}

export async function api(path, { method = 'GET', body, form, raw = false, signal } = {}) {
  const headers = {};
  const init = { method, headers, credentials: 'same-origin', signal };
  if (method !== 'GET' && method !== 'HEAD') headers['X-DWHFlow'] = '1';
  if (form) init.body = form;
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  const response = await fetch(path, init);
  if (response.status === 401 && !path.startsWith('/api/auth/')) {
    window.dispatchEvent(new CustomEvent('dwhflow:auth'));
  }
  if (!response.ok) {
    let payload = null;
    try { payload = await response.json(); } catch { /* gövde JSON değil */ }
    throw new ApiError(response.status, detailOf(payload, response.status));
  }
  if (raw) return response;
  if (response.status === 204) return null;
  const type = response.headers.get('content-type') || '';
  return type.includes('application/json') ? response.json() : response.text();
}

export const get = (path, opts) => api(path, opts);
export const post = (path, body, opts = {}) => api(path, { ...opts, method: 'POST', body });
export const put = (path, body, opts = {}) => api(path, { ...opts, method: 'PUT', body });
export const patch = (path, body, opts = {}) => api(path, { ...opts, method: 'PATCH', body });
export const del = (path, opts = {}) => api(path, { ...opts, method: 'DELETE' });
export const upload = (path, formData) => api(path, { method: 'POST', form: formData });

/** URL'yi proje ve adlarla güvenle kurar: p`/api/projects/${slug}/datasets/${id}` */
export function p(strings, ...values) {
  return strings.reduce((out, part, i) => out + part + (i < values.length ? encodeURIComponent(values[i]) : ''), '');
}

/** Arka plan işini bitene kadar izler. onProgress(job) her yoklamada çağrılır. */
export async function waitJob(jobId, onProgress, { interval = 700 } = {}) {
  for (;;) {
    const job = await get(p`/api/jobs/${jobId}`);
    if (onProgress) onProgress(job);
    if (job.state === 'done') return job;
    if (job.state === 'error') throw new ApiError(500, job.error || 'iş başarısız');
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
}

/** Oturum çereziyle indirme (GET). */
export function download(url) {
  const a = document.createElement('a');
  a.href = url;
  a.download = '';
  document.body.append(a);
  a.click();
  a.remove();
}
