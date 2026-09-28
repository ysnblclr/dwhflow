# DWHFlow × Archify — Süreç Dönüşüm Planı
### Analiz, plan ve uygulama durumu (28 Eylül 2026)

> **Soru:** DWHFlow'un tüm sürecini Archify altyapısına taşıyıp akışı Archify ile
> aynı yapmak.
>
> **Kısa cevap:** Mümkün ve mantıklı — ama "Archify'ı olduğu gibi kullan" değil,
> **"DWHFlow modeli + Archify motoru"** şeklinde. Archify küçük, küratörlü ve
> doğrulanmış *açıklama diyagramları* için tasarlanmış; tüm ambarı tek diyagramda
> çizemez (dataflow tipi en fazla 5 aşama). Doğru mimari: DWHFlow gerçeğin kaynağı
> (model, doğrulama, parser'lar) olarak kalır, **dilimleri** Archify spec'ine
> derler, Archify doğrular ve etkileşimli HTML üretir. Kullanıcı akışı ise tamamen
> Archify'ınki olur: *anlat → tipli spec → finalize → doğrulanmış HTML → sohbetle iyileştir.*

---

## 0. Durum: uygulandı (28 Eylül 2026)

§7'deki sorular yanıtlandı ve plan kodlandı. Planın ilk hâlinden sapmalar
aşağıda; sonraki bölümler orijinal analizdir, sapmalar yerlerinde işaretli.

**Kararlar**

| Soru (§7) | Karar |
|---|---|
| ODI benzeri arayüz | **Kaldırıldı.** Yerine tek sayfalık arayüz: doküman yükle → akışa çevir → galeri. `layout/`, eski `static/js/*` ve sunucu tarafı yerleşim motoru silindi. |
| Node.js | **Zorunlu** (üretim için, ≥18). Görüntüleme için gerekmez. |
| Hangi agent | **Hiçbiri.** Uygulama bağımsız çalışır; Archify'ın "ajan spec'i düzeltir" adımının yerini deterministik derleyici + motorun önerdiği düzeltmeleri uygulayan onarım adımı alır. |
| LLM | **İsteğe bağlı.** Yalnız kuralların çözemediği paragraflara; kanıt cümlesi birebir doğrulanır, sonuç taslaktır. |
| Motor indirme | Archify 3.0.1 zip'i pakete gömüldü (2 157 085 bayt, SHA-256 kilitli, MIT). |

**Spike (A0) bulguları ve sonuçları**

- `dataflow` tipi kullanılamadı: sabit geometri (aşama merkezleri sabit, ≤5 aşama,
  ≤5 satır, ilk aşamada kutu ≤152 px). Gerçek bir DWH yolu 13 yerleşim kısıtına takıldı.
- Bütün görünümler **`architecture` ızgarasıyla** üretiliyor: katmanlar yukarıdan
  aşağı satırlar, hücre/boşluk ölçüleri derleyicide; yazıların masaüstünde ≥6 px
  görünmesi için genişlik bütçesi ~1300 birim. Batch zinciri de aynı tiple
  (job'lar kutu, `topological_generations` satırlar).
- Archify teslim sırasında çıktı dizinini kilitler ve bir çıktıyı ilk adayına
  bağlar: **her diyagram ve her yeniden deneme ayrı klasörde**.
- Türkçe görüntüleyici `meta.translations` ile, **438/438 anahtar**. Kısmi çeviri
  Archify'ın stderr'ine uyarı basıp yapılandırılmış hata tanılarını bozuyordu
  (`internal/unclassified`); paket tamamlanınca gerçek tanılar okunur hâle geldi.
- Standart kalitede dar (tek sütun) diyagramda kenar etiketi kutuyla çakışabiliyor;
  motorun `Suggested fix: set labelAt [x, y]` önerisi otomatik uygulanıp ayrı
  klasörde yeniden deneniyor (`render/repair.py`). Tahmin yok: öneri yoksa ya da
  etiket belirsizse onarım yapılmaz.

**Uygulanan süreç**

```
dokümanlar → ingest (xlsx/csv/docx/pdf/md/txt/sql/bteq/jil/yaml, + isteğe bağlı LLM)
          → tek model (kaynak izi her nesnede) → 20 alan kuralı
          → dilimler (genel akış ≤24 tablo · soy ağaçları · A→B yolları · çevre · batch)
          → architecture spec → archify finalize (4 kapı)
             showcase → [etiket onarımı] → standard; başarısız asla "hazır" değil
          → galeri index.html + diyagram HTML'leri + model/ + dwhflow-run.json
```

Arayüzler: `dwhflow convert` (CLI) ve `dwhflow serve` (yerel web). İncelemedeki
14 madde kapatıldı; koddaki hatalar için regresyon testleri var (402 test).

---

## 1. Archify nedir — analiz

**Kaynak:** [github.com/tt-a1i/archify](https://github.com/tt-a1i/archify) · MIT lisans ·
v3.0.1 (28.09.2026) · ~73 bin yıldız · her gün commit alıyor · Node.js ≥18, çalışma
zamanında **sıfır npm bağımlılığı**.

### 1.1 Süreç (bizim kopyalayacağımız akış)

```
Kullanıcı isteği (doğal dil / Mermaid / repo)
      │
      ▼
Agent tipi seçer:  architecture | workflow | sequence | dataflow | lifecycle
      │
      ▼
Agent tipli JSON "candidate" yazar   ← şema + örnek + yazım kuralları
      │
      ▼
node bin/archify.mjs finalize <tip> candidate.json out.html --quality showcase --json
      │   ├─ validate      şema + yerleşim kuralları (çakışma, taşma, etiket sığması)
      │   ├─ deliver       tek dosyalık HTML + SHA-256 makbuz
      │   ├─ check         sıkı provenance denetimi
      │   └─ browser-check gerçek Chrome'da tema/görünüm kararlılığı
      ▼
Geçti → makbuz + HTML          Kaldı → teşhis → agent onarır → finalize tekrar
      │
      ▼
Sohbetle iyileştirme: "auth ekle", "cache-miss yolunu vurgula", "açık tema"
```

Her istek kendi klasörüne yazılır (`.archify/<tip>-<slug>-<zaman>/`), önceki sürümler
korunur. Başarısız bir komut asla "başarılı" raporlanmaz.

### 1.2 Görüntüleyici (üretilen HTML'in içinde, kurulum gerekmez)

| Archify özelliği | DWHFlow'daki karşılığı |
|---|---|
| **Route Probe** (`#route=a~b`) — iki uç arası yazılmış yolu izler | A→B yol analizi |
| **Reach** (`#focus=x&reach=downstream`) | Etki analizi (Archify bunu "impact" değil "authored reachability" diye adlandırmakta ısrarlı) |
| **Trace animasyonu** — okuyucu kontrollü akış hareketi | **M3'ün tamamı** (A→B animasyonlu yolculuk) |
| Node Finder, Semantic Passport (upstream/downstream kartı) | Arama + Property Inspector |
| Semantic Radar (mini harita), Lens | Kuşbakışı + katman filtresi |
| Temalar: Classic, Signal Flow, Blueprint, Editorial · açık/koyu | Tema |
| Dışa aktarma: PNG / JPEG / WebP / çift temalı SVG / **WebM video** / Route & Reach paylaşım kartı | SVG indir (bizimki çok daha zayıf) |
| Architecture **Delta** — iki sürümü karşılaştırma + makbuz | M5 drift raporu (yalnız architecture tipi için) |

### 1.3 Sert sınırlar (şemalardan doğrulandı)

| Tip | Sınır |
|---|---|
| **dataflow** | **2–5 aşama**; satır yerleşimi 0–4 için tasarlanmış; node tipi 7 genel türle sınırlı: `frontend, backend, database, cloud, security, messagebus, external`; kenar stili 4: `default, emphasis, security, dashed` |
| **workflow** | kolon 0–5 (6 kolon), kulvar (lane) sınırsız |
| **architecture** | ızgara ≤12 kolon; sınır kutusu tipi: `region`, `security-group` |
| **lifecycle** | ≤4 kulvar, ≤5 kolon |
| Kanıt (`sources`) | dataflow'da **yalnız node'larda**; kenarlarda (flow) yok |
| Dil | Yerleşik: en, zh, de, es, fr, it, ja, ko, pt, ru. **Türkçe yok**; `meta.translations` alanı var (kapsamı spike'ta doğrulanmalı) |
| Ağ | `finalize` 24 saatte bir sabit bir manifest'e GET atıyor (güncelleme hatırlatması). `ARCHIFY_UPDATE_CHECK_DISABLED=1` ile kapanıyor |
| Tarayıcı | `browser-check` Chrome/Chromium ister; yoksa "skipped" (çıkış 2) |

**Tasarım felsefesi:** "8–12 çekirdek bileşen, tek ana yol, destekleyici detay kartlarda."
Bu, ODI'nin "her seferinde tek mapping" yaklaşımıyla örtüşüyor ama "tüm ambarı göster"
ile örtüşmüyor.

### 1.4 Entegrasyon deseni: `ooiuuii/hermes-archify`

Archify'ı Hermes'e bağlayan bu eklenti bizim izleyeceğimiz deseni zaten kurmuş:
Python köprüsü → **sabitlenmiş** Archify sürümü (`engine-lock.json`: boyut + SHA-256) →
tek bir araç (hazırlık / doğrulama / teslim) → başarısız güncelleme önceki HTML'i korur →
`--archive` ile çevrimdışı kurulum → asla kendiliğinden güncellenmez.

---

## 2. Uyum analizi

### 2.1 Doğal örtüşmeler

- **Katmanlar → aşamalar.** Archify'ın önerdiği 5 aşama (*source, ingest, process,
  store, consume*) bizim 7 katmanımıza neredeyse birebir oturuyor:

  | Archify aşaması | DWHFlow katmanları |
  |---|---|
  | Kaynak | SOURCE |
  | Alım (Ingest) | INTERFACE + STAGING |
  | Entegrasyon | ODS + CORE |
  | Depo | MART |
  | Tüketim | CONSUMPTION |

- **Kontrol stilleri.** `security` kenarı → PII / kısıtlı akış; `dashed` → batch/CDC;
  `emphasis` → seçili A→B yolu. Archify'ın `classification` alanı → yükleme stratejisi
  ve hassasiyet ("delta", "SCD2", "PII").
- **Doğrulanmış teslim.** Archify'ın makbuz disiplini bizim "sessiz başarısızlık yok"
  ilkemizle aynı.
- **Kanıt.** `sources` (`path` + `line`) bizim provenance'ın (`file` + `locator`) node
  tarafıyla örtüşüyor.

### 2.2 Uymayanlar ve kayıplar (dürüst liste)

| Konu | Sorun | Çözüm |
|---|---|---|
| **Ölçek** | Tüm ambar tek diyagrama sığmaz (≤5 aşama; gerçek ambarda binlerce node) | **Dilimleme:** A→B yolu, odak+N adım, sistem, batch segmenti, kolon. Her dilim bütçe içinde kalır; aşarsa otomatik bölünür |
| **Kenar kanıtı** | Flow'larda `sources` yok; bizim lineage iddiamız kenarda | Kenar kanıtı hedef node'un `sources`'una ve bir "Kanıt" kartına taşınır |
| **Zengin tipler** | 16 dataset + 13 süreç tipi → 7 genel tipe iner (glif zenginliği kaybolur) | Tip, `tag`/`sublabel`'da korunur (ör. `tag: "EBCDIC"`, `sublabel: "TPT · truncate-load"`) |
| **Uzun adlar** | `SP_LOAD_CORE_BALANCE_HIST` gibi adlar showcase etiket-sığma kuralına takılır | Kısaltma + tam ad Passport'ta; derleyici kendi etiket bütçesini ölçer |
| **Türkçe arayüz** | Yerleşik değil | `meta.translations` ile (spike'ta doğrulanacak) ya da upstream'e `tr.json` katkısı |
| **Yeni çalışma zamanı** | Şu an yalnız Python; Archify **Node ≥18** ister | *Üretim* Node ister, *görüntüleme* yalnız tarayıcı. Taşıma paketine Node gerekliliği eklenir |
| **Ağ çağrısı** | Güncelleme kontrolü dışarıya GET atar — kapalı ağ sözümüzle çelişir | Köprü `ARCHIFY_UPDATE_CHECK_DISABLED=1` ile çalıştırır, testle bağlanır |
| **Upstream hızı** | Günlük commit, şema v1/v2 | Sürüm sabitlenir (zip + SHA-256); yükseltme bilinçli ve testli |
| **Terminoloji** | Archify "impact / blast radius" demeyi reddediyor | Arayüz metni "yazılmış erişim" olur; bizim etki analizi CLI'da kalır |
| **Simülasyon** | Archify SimPy tarzı zaman simülasyonu yapmaz | M4 bizde kalır; sonuçları sequence/lifecycle diyagramı olarak Archify'a verilebilir |

### 2.3 Kodlama kararı: tablolar mı node, joblar mı?

Archify kuralı: *"Kenar etiketi taşınan veri varlığını adlandırır, taşıma aracını değil."*

- **Dataflow görünümü:** **tablolar/dosyalar node**, joblar kenar etiketi
  (`STG_ACCOUNT ──SP merge · delta──▶ ODS_ACCOUNT`). Node sayısını yarıya indirir,
  DWH okuyucusunun asıl ilgilendiği tabloları öne çıkarır.
- **Batch görünümü (workflow):** **joblar node**, kulvarlar = sistemler
  (Core Banking, MFT, DWH, BI), kolonlar = fazlar (Extract, Transfer, Load,
  Integrate, Mart, Deliver — tam 6 = workflow sınırı).

---

## 3. Hedef mimari

```
┌──────────────────────────────────────────────────────────────────┐
│  İSTEK: "Hesap bakiyesi mainframe'den regülatöre nasıl gidiyor?" │
│  (agent sohbeti · dwhflow CLI · katalog arayüzü)                 │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
┌─ DWHFlow (Python, değişmeyen çekirdek) ──────────────────────────┐
│  kanonik model · YAML depo · graf · 20 alan kuralı · parser'lar  │
│                               │                                  │
│  DİLİMLEYİCİ  yol | odak+N | sistem | batch segmenti | kolon     │
│                               │   bütçe aşılırsa böl             │
│  SPEC DERLEYİCİ (deterministik) model dilimi → Archify JSON      │
│     aşama/satır ataması · etiket bütçesi · tip eşleme ·          │
│     kanıt → sources + kartlar · kendi onarım döngüsü             │
│                               │                                  │
│  KAPI 1: dwhflow validate (alan anlamı — Archify bunu bilmez)    │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
┌─ ARCHIFY KÖPRÜSÜ (hermes-archify deseni) ─────────────────────────┐
│  sabitlenmiş motor (zip + SHA-256) · update-check kapalı         │
│  KAPI 2: archify finalize → validate · deliver · check ·         │
│          browser-check (Chrome varsa)                            │
└──────────────────────────────┬───────────────────────────────────┘
                               ▼
  .dwhflow/diagrams/<tip>-<slug>-<zaman>/
      candidate.json · <slug>.html · finalize-summary.json · dwhflow-receipt.json
                               ▼
  Kullanıcı HTML'i açar, paylaşır (tek dosya, kurulum gerekmez)
  Sohbetle iyileştirir → yeni klasör → önceki sürüm korunur
```

**İki kapı, iki makbuz:** DWHFlow alan anlamını doğrular (öksüz tablo, SLA zinciri,
katman yönü…), Archify geometriyi ve teslimatı. İkisi birlikte geçmeden diyagram
"hazır" sayılmaz.

### 3.1 Görünüm eşlemesi

> **Uygulamada:** spike sonrası hepsi `architecture` tipiyle üretiliyor (bkz. §0).
> Sistem görünümü, kolon lineage, delta ve simülasyon görünümleri henüz yok.

| DWHFlow görünümü | Archify tipi | Not |
|---|---|---|
| A→B yolu | **dataflow** | yol `emphasis`, `animation: "trace"`, deep link `#route=a~b` |
| Odak + N adım | **dataflow** | merkez node vurgulu; bütçeyi aşarsa N düşürülür |
| Fiziksel / sistemler | **architecture** | sistemler = bileşen, Execution Unit = `region`, arayüz dosyaları = sınır geçen bağlantı |
| Batch zinciri | **workflow** | kulvar = sistem, kolon = faz, ana yol = kritik yol |
| Kolon lineage | **dataflow** | kolonlar node, `influences` = `dashed` |
| Model sürüm farkı | **architecture delta** | M5 drift raporu büyük ölçüde hazır gelir |
| (M4) simülasyon sonucu | **sequence / lifecycle** | job durumları, retry, bekleme |

### 3.2 Agent akışı (Archify'ınkiyle aynı)

> **Karar değişti:** agent yok (bkz. §0). Aşağıdaki akış yalnız kayıt için duruyor;
> uygulamada kullanıcı doküman verir, diyagramlar deterministik üretilir.

DWHFlow da bir **agent skill** olarak paketlenir (`SKILL.md`). Claude Code, Hermes,
Cursor veya Codex içinde kullanıcı şunu yazar:

> *"dwhflow ile hesap bakiyesinin kaynaktan regülatöre akışını çiz, EBCDIC adımlarını vurgula."*

Agent: `dwhflow slice path src_cb_account dwn_regulator --json` → gerçekleri alır →
`dwhflow render dataflow …` → iki kapı → HTML yolunu ve makbuzları raporlar.
Agent **olgu uydurmaz**: olgular modelden gelir, agent yalnızca dilimi, vurguyu ve
kartları seçer. M2d'deki LLM katmanı da bu deseni kullanır.

---

## 4. Mevcut koda etkisi

| Bileşen | Karar |
|---|---|
| model, store, graph, validate, CLI | **Kalır** — gerçeğin kaynağı |
| workspace (doküman yükleme, bağlantılar) | **Kalır** |
| M2 parser planı, M2d YZ planı | **Kalır**; çıktıları aynı modele akar |
| `layout/` (Sugiyama motoru) | **Emekliye ayrılır** (Archify yerleşimi yapar) |
| `static/js/canvas.js` ve diyagram çizimi | **Emekliye ayrılır** |
| ODI benzeri arayüz | **Karar gerekiyor** (bkz. §7): (a) ince **katalog**: navigator + doğrulama + "diyagram üret" + Archify HTML'ini iframe'de açma, ya da (b) tamamen kaldırılıp CLI + agent + statik galeri |
| İncelemedeki hatalar | 1 (veri kaybı), 3 (SLA), 4 (yol kesme), 5 (kilitlenme) **hâlâ geçerli** — dilimleyici `paths`'e dayanıyor, 4 numara doğrudan etkiliyor. 2 (CSRF) arayüz kalırsa geçerli. Yerleşim motoru hataları konu dışı kalır |

---

## 5. Taşınabilirlik etkisi

- **Üretim** için Node.js ≥18 gerekir (bu makinede v24.17 var). **Görüntüleme** için
  yalnız tarayıcı — HTML tek dosya, e-postayla gönderilebilir.
- Taşıma paketi (`scripts/paketle.py`) Archify zip'ini sabit sürüm ve SHA-256 ile
  içine alır; hedefte internet gerekmez. Node kurulu değilse `dwhflow doctor` bunu
  söyler ve sadece "üretim kapalı" moduna düşer.
- Chrome yoksa `browser-check` atlanır; makbuz bunu dürüstçe "skipped" olarak yazar.
- Lisans: MIT + `THIRD_PARTY_NOTICES` pakete dahil edilir.

---

## 6. Yol haritası

| Faz | Kapsam | Kabul kriteri | Süre |
|---|---|---|---|
| **A0 — Spike** | Archify'ı sabit sürümle indir, örnek dataflow'u finalize et. Demo modeldeki `src_cb_account → dwn_regulator` yolunu **elle** dataflow spec'ine çevir, finalize et. Ölç: etiket sığması, 5+ satır davranışı, Türkçe metin, `translations`, `views`, CJK-dışı genişlik tahmini | Gerçek bir DWH yolu showcase kalitesinde geçiyor mu, geçmiyorsa neden — somut rapor | 1–2 gün |
| **A1 — Köprü + dataflow derleyici** | `archify` köprüsü (hermes deseni), yol ve odak dilimleri, aşama/satır ataması, etiket bütçesi, onarım döngüsü, iki kapı, makbuzlar, golden spec testleri | `dwhflow render path A B` doğrulanmış HTML üretiyor; demo modelin tüm A→B yolları geçiyor | 1 hafta |
| **A2 — Diğer tipler** | workflow (batch), architecture (sistemler/EU), kolon lineage; architecture delta ile model farkı | Dört görünüm de Archify'dan üretiliyor | 1 hafta |
| **A3 — Agent skill + galeri** | `SKILL.md`, `dwhflow slice/render` JSON arayüzü, statik galeri (tüm dilimlerin indeksi) | Claude Code/Hermes'te doğal dil → doğrulanmış HTML | 3–4 gün |
| **A4 — Arayüz kararı** | §7'deki karara göre katalog ya da kaldırma | — | 2–4 gün |
| **A5 — Türkçe** | `translations` ile ya da upstream'e `tr.json` katkısı | Görüntüleyici arayüzü Türkçe | 1–2 gün |

Mevcut M-yol haritasına etkisi: **M3 (A→B animasyonu) büyük ölçüde A1 ile gelir**;
M5'in drift kısmı A2'deki delta ile gelir. M1b (düzeltme editörü), M2 (parser'lar)
ve M4 (SimPy) bizde kalır.

**Önce yapılması önerilen:** `git init` ve incelemedeki 1, 3, 4, 5 numaralı hataların
düzeltilmesi — dilimleyici bunların üstüne kurulacak.

---

## 7. Karar gerektiren konular

> **Yanıtlandı** — kararlar §0'da.

1. **Mevcut ODI benzeri arayüz ne olsun?** (a) ince katalog olarak kalsın — navigator,
   doğrulama, doküman yükleme, "diyagram üret" düğmesi, Archify çıktısını içinde açma;
   (b) tamamen kalksın — CLI + agent + statik galeri. *Önerim (a):* doküman yükleme ve
   bağlantılar panelinin bir yere ihtiyacı var ve bankada herkes agent kullanmayacak.
2. **Node.js hedef makinelerde zorunlu olsun mu?** Olmazsa o makineler yalnız hazır
   HTML'leri görüntüler, yeni diyagram üretemez.
3. **Hangi agent?** Claude Code, Hermes (makinende kurulu) ya da ikisi — skill paketlemesi
   buna göre yapılır.
4. **A0 spike için izin:** `github.com/tt-a1i/archify` adresinden sabit sürüm zip'ini
   (~2.1 MB, MIT) indirip yerelde `node` ile çalıştırmam gerekiyor.
5. **Sıra:** Önce git + hata düzeltmeleri, sonra A0 mı; yoksa doğrudan A0 mı?

---

## 8. Kaynaklar

- Archify: https://github.com/tt-a1i/archify — `README_EN.md`, `archify/SKILL.md`,
  `archify/schemas/*.schema.json`, `archify/renderers/dataflow/README.md`,
  `archify/references/{viewer-runtime,repository-authoring,delivery-contract}.md`,
  `archify/examples/product-analytics.dataflow.json`, `archify/delta/architecture-delta.mjs`
- Entegrasyon deseni: https://github.com/ooiuuii/hermes-archify — `README.md`, `engine-lock.json`
- Canlı örnekler: https://tt-a1i.github.io/archify/gallery.html
