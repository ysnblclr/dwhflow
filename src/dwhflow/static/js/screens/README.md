# Ekran modülleri — sözleşme

Her ekran `js/screens/<ad>.js` dosyasıdır; `core/router.js` içindeki `ROUTES` tablosu adresi
modüle bağlar. Yönlendirici modülü dinamik yükler ve şunu çağırır:

```js
export const refreshOnChange = true;          // isteğe bağlı: bir iş bitince (okuma, diyagram) ekran yeniden çizilir
export async function render(root, params, query) { … }  // root: boş <div>; params: {slug, id, name, run}
export function dispose() { … }                // isteğe bağlı: zamanlayıcıları temizle
```

`render` hata fırlatırsa kabuk hatayı kart içinde gösterir. 401 durumunda kabuk giriş ekranına döner.

## Kurallar

- **Dış kaynak yok**: CDN, web fontu, satır içi `<script>` ya da `onclick="…"` yok (CSP: `script-src 'self'`).
  Olaylar `h('button', {onclick: fn})` ile bağlanır.
- **Metin her zaman `textContent`**: `innerHTML` kullanmayın. Kullanıcı ve doküman verisi güvenilmezdir.
- **Yerleşik `node.append(...)`'e `null` geçirmeyin** (metin olarak "null" yazar). Koşullu çocuklar için
  `h()` ya da `append(node, [..])` (core/dom.js) kullanın; ikisi de `null/false` atlar.
- API çağrıları yalnız `core/api.js` ile: `get/post/put/patch/del/upload`, adresler `p\`/api/projects/${slug}/…\`` ile
  (kodlama). Değişiklik yapan istekler CSRF başlığını otomatik taşır.
- Yetki: düzenleme düğmeleri yalnız `canEdit()`; yönetici işleri `isAdmin()` (core/store.js). Sunucu yine de denetler.
- Arka plan işi dönen uçlar (`job` alanı): `trackJob(job, {title})` (core/ui.js) — sol altta ilerleme gösterir, bitince
  `project:changed` yayar; `refreshOnChange` ekranlar kendiliğinden yenilenir.
- Etiketler: `label.layer(v)`, `label.datasetKind(v)`, `label.processKind(v)`, `label.edgeKind(v)`, `label.op(v)` …;
  açılır listeler için `options('layers')` vb. (`/api/meta`'dan).
- Ortak bileşenler (core/ui.js): `pageHeader`, `button`, `dataTable`, `modal`, `confirmDialog`, `formDialog`, `field`,
  `buildForm`, `tabs`, `badge`, `statusBadge`, `layerPill`, `docStatusBadge`, `severityBadge`, `originBadge`, `stat`,
  `evidence`, `dropdown`, `dropzone`, `trackJob`, `toast`, `toastError`, `loading`, `empty`, `when`.
- Bağlantılar `link('p', slug, 'tablolar', id)` (core/router.js); gezinme `go(...)`. Kaydedilmemiş form varken
  `setDirty('Kaydedilmemiş değişiklikler var')`, kaydedince `setDirty(null)`.
- Türkçe arayüz metni; kısa, açıklayıcı. Tarih `when(iso)`, boyut `fmtSize`.
- Stil: `css/app.css` sınıfları (`card`, `grid-2`, `split`, `kv`, `list`, `toolbar`, `banner`, `badge`, `layer`, `block` …).
  Yeni sınıf gerekiyorsa `css/app.css` sonuna, ekran adıyla ön eklenmiş olarak ekleyin.

## Uç noktalar

Tam liste ve biçimler: `src/dwhflow/api/routes_*.py` (FastAPI). Özet: `/api/projects/{slug}/…` altında
`dashboard`, `documents` (+ `/{name}`, `/{name}/preview`, `/{name}/download`, `/{name}/reader`, `sample`),
`build`, `jobs`, `search`, `datasets` (+ `/{id}`), `processes` (+ `/{id}`), `links`, `sttm`, `impact/{id}`, `paths`,
`model/export`, `corrections` (+ `preview`, `/{id}` DELETE = geri al, `/{id}/redo`), `audit`, `review` (+ `decide`),
`findings`, `diagrams/plan`, `runs` (+ `/{run}`), `compare`, `settings` (+ `preview`, `name-test`, `export`, `import`),
`guide`. Genel: `/api/settings`, `/api/users`, `/api/system`, `/api/formats`, `/api/meta`, `/api/samples`,
`/api/templates/{id}`, `/api/guide`, `/api/jobs/{id}`. Diyagram dosyaları: `/dosya/{slug}/{run}/index.html`.

Düzeltme işlemleri (`POST corrections {ops:[{islem, hedef, deger, neden}]}`) — hedefler **ADLA** verilir:

| islem | hedef | deger |
|---|---|---|
| yeniden_adlandir | {nesne} | {ad} |
| tur_degistir | {nesne} | {tur} |
| katman_degistir | {nesne} | {katman} |
| sistem_ata | {nesne} | {sistem} |
| hassasiyet_ata | {nesne} | {hassasiyet} |
| aciklama | {nesne} | {aciklama} |
| birlestir | {nesneler: [a, b]} | {ad: kalacak ad} |
| nesne_ekle | {ad, tip: dataset/process} | {tur, katman, sistem, aciklama} |
| nesne_sil | {nesne} | — |
| bag_ekle | {kaynak, hedef, tur?} (kolon için `TABLO#KOLON`) | {surec?, ifade?} |
| bag_sil | {kaynak, hedef, tur?} | — |
| bag_yon_cevir | {kaynak, hedef, tur?} | — |
| surec_ata | {kaynak, hedef} | {surec} |
| onayla | {nesne} ya da {kaynak, hedef, tur?} | — |
| kolon_ekle | {nesne} | {ad, tip, pk} |
| kolon_esle | {kaynak: T#K, hedef: T#K} | {ifade} |
| bulgu_yoksay | {kural, ozne} | — (neden zorunlu tutun) |
| oneri_reddet | {anahtar} | — |
