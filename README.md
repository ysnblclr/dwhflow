# DWHFlow

Veri ambarı dokümanlarını **uçtan uca akış diyagramlarına** çevirir. STTM
Excel'leri, Word/PDF tasarım dokümanları, SQL/BTEQ betikleri, Autosys JIL
tanımları, YAML ve düz metin notları okunur; hepsi tek bir modelde birleşir,
doğrulanır ve [Archify](https://github.com/tt-a1i/archify) motoruyla
**etkileşimli, tek dosyalık HTML diyagramlarına** dönüşür: kaynak sistemden
batch job'a, binary dosyadan ETL'e, DWH tablolarından hedef tablolara kadar.

- **Ajan gerekmez.** Uygulama kendi başına çalışır: doküman ver, diyagramları al.
- **Yapay zekâ isteğe bağlıdır.** Varsayılan yol tamamen kural tabanlıdır. İstenirse
  yerel bir LLM (ör. Ollama'da Qwen3 8B) yalnız kuralların çözemediği paragraflara
  bakar; önerdiği her akış belgede harfi harfine geçen bir cümleyle kanıtlanmak
  zorundadır, yoksa reddedilir.
- **Ekip için.** Giriş, roller (izleyici/düzenleyici/yönetici), projeler, elle düzeltme,
  onay kuyruğu ve kim neyi değiştirdi günlüğü.
- **Çevrimdışı.** Telemetri yok, CDN yok, motorun güncelleme denetimi kapalı.
  Sunucu varsayılan olarak yalnız `127.0.0.1`'e bağlanır.

Planlar ve gerekçeler: [PLAN_V2.md](PLAN_V2.md) (sıradaki sürüm: ekranlar, elle müdahale,
evrensel okuma, LLM ara formatı), [ARCHIFY_PLAN.md](ARCHIFY_PLAN.md) (güncel süreç),
[PLAN.md](PLAN.md) (ilk kapsamlı plan).

---

## Nasıl çalışır

```
dokümanlar ──► okuyucular ──► tek model ──► doğrulama ──► dilimler ──► Archify ──► galeri
 xlsx docx      tablo/metin     tablolar,     20 kural     genel akış,   4 kapı:     index.html
 pdf sql jil    kuralları,      job'lar,      (SLA, döngü, soy ağacı,    şema,       + her diyagram
 yaml md txt    (+ isteğe bağlı kenarlar,     katman…)     yol, batch    teslim,     ayrı HTML
                LLM)            kaynak izi                  zinciri       kontrol,
                                                                         tarayıcı
```

1. **Okuma.** Her biçimin kendi okuyucusu var. Tablolar sütun başlıklarından tanınır
   (Türkçe/İngilizce eş anlamlılar: "Hedef Tablo", "Target Table", "Kaynak",
   "Job", "Öncül"…). Serbest metin Türkçe hâl ekleriyle çözülür
   ("*STG_MUSTERI tablosundan ODS_MUSTERI tablosuna …*"). SQL, sqlglot ile
   soy analizine girer (INSERT…SELECT, CTAS, MERGE, UPDATE; CTE'ler hariç).
2. **Birleştirme.** Aynı tablo farklı dokümanlarda farklı yazılsa da
   (`MART_KART_OZET` / `DWH.MART_KART_OZET`) tek nesnede birleşir. Her nesne ve kenar
   hangi dosyanın hangi satırından/başlığından geldiğini taşır.
3. **Doğrulama.** Katman yönü, döngüler, zamanlama çakışmaları, SLA zinciri, eksik
   yükleme stratejisi, kanıtsız LLM çıktısı gibi 20 kural.
4. **Dilimleme.** Büyük bir ambar tek diyagrama sığmaz. Diyagram başına en çok 24
   tablo: genel akış (sığıyorsa), her uç tablonun soy ağacı, job zinciri ve
   istediğiniz A→B yolları.
5. **Üretim.** Her dilim Archify `architecture` spec'ine derlenir (katmanlar
   yukarıdan aşağı satırlar, job adları kenar etiketleri) ve `archify finalize`
   ile dört kapıdan geçer. Showcase kalitesi tutmazsa önce motorun önerdiği etiket
   düzeltmeleri uygulanır, sonra standart kalite denenir. Başarısız bir diyagram
   asla "hazır" gösterilmez.

### Diyagramda neler var

- Katman bölgeleri (Kaynak, Arayüz/Landing, Staging, ODS, Çekirdek, Mart…).
- Kenar üzerinde job/prosedür adı; birden fazlaysa `+N`.
- Kesikli çizgi: dosya aktarımı, CDC ya da **taslak** (yalnız serbest metinden gelen)
  bağ. Kırmızı: kişisel veri (PII) taşıyan akış. Vurgulu: istenen A→B yolu, iz
  animasyonuyla.
- Yan kartlar: vurgulanan yol, süreçler, **kaynak dokümanlar** (hangi bilgi nereden
  geldi) ve doğrulama bulguları.
- Archify görüntüleyicisi Türkçe: arama, odak, yol izleyici, SVG/PNG dışa aktarma.

---

## Kurulum

Gereksinimler:

| | Ne için | Zorunlu mu |
|---|---|---|
| Python 3.11+ | uygulamanın kendisi | evet |
| Node.js 18+ | diyagram üretimi (Archify motoru) | üretim için evet; görüntüleme için hayır |
| Chrome / Chromium | diyagramların tarayıcı kontrolü | hayır — yoksa kontrol "atlandı" diye raporlanır |
| Ollama vb. | isteğe bağlı YZ | hayır |

### GitHub'dan kurulum (Windows)

Önkoşullar yoksa (bir kez; kurduktan sonra yeni bir PowerShell penceresi açın):

```powershell
winget install -e --id Git.Git
winget install -e --id Python.Python.3.12
winget install -e --id OpenJS.NodeJS.LTS
```

Kurulum:

```powershell
git clone https://github.com/ysnblclr/dwhflow.git
cd dwhflow
powershell -ExecutionPolicy Bypass -File kur.ps1
```

`kur.ps1` Python ve Node.js sürümlerini denetler, proje klasöründe `.venv` sanal
ortamını kurar (sistem Python'una dokunmaz), bağımlılıkları yükler ve
`dwhflow doctor` çalıştırır. `-Dev` test araçlarını da kurar, `-Serve` bitince
arayüzü açar. Sonra:

```powershell
.\.venv\Scripts\dwhflow serve
```

Güncellemek için depo klasöründe `git pull` ve ardından `kur.ps1`'i yeniden çalıştırın.

### GitHub'dan kurulum (Linux / macOS)

```bash
git clone https://github.com/ysnblclr/dwhflow.git && cd dwhflow && bash kur.sh
```

```bash
.venv/bin/dwhflow serve
```

### Elle kurulum

Proje kökünde (tercihen bir sanal ortamda):

```bash
pip install .
```

```bash
dwhflow doctor
```

`doctor` Python paketlerini, Node.js sürümünü ve diyagram motorunu denetler. Motor
(Archify 3.0.1, MIT) paketin içinde gelir; ilk kullanımda SHA-256'sı doğrulanıp
`~/.dwhflow/engine/` altına açılır. İnternetten hiçbir şey indirilmez.

### Başka makineye taşıma (Docker gerekmez)

İnternete çıkabilen bir makinede, hedefin işletim sistemi ve Python sürümü için:

```bash
python scripts/paketle.py --platform windows --python 3.11
```

Çıkan zip'i hedefe kopyalayıp açın, zip içindeki `kur.bat` (Windows) ya da
`bash kur.sh` betiğini çalıştırın. Hedefte internet gerekmez. Node.js kurulum dosyasını da yanınızda
götürün. Ayrıntılar zip içindeki `KUR.txt`'de.

Bağımlılıkların hepsi hazır tekerlek olarak gelir; yalnız `pydantic_core` ve `pyyaml`
platforma bağlıdır — paketin hedefe göre hazırlanmasının sebebi bu.

---

## Kullanım

### Ekip uygulaması

```bash
dwhflow serve
```

`http://127.0.0.1:8765` adresinde açılır. **Giriş zorunludur.** İlk açılışta sunucu
makinesindeki tarayıcıdan yönetici hesabı oluşturulur (ya da komutla:
`dwhflow kullanici ekle ADINIZ --rol yonetici`). Ekibe açmak için:

```bash
dwhflow serve --host 0.0.0.0 --allow-host SUNUCU_ADI
```

Trafik şifresizdir; kurum içinde HTTPS sağlayan bir ters vekil (IIS/nginx) arkasında
çalıştırın. Roller: **izleyici** (okur), **düzenleyici** (doküman yükler, düzeltir, onaylar,
proje ayarı yapar), **yönetici** (kullanıcılar, genel ayarlar, LLM profilleri, istemler).

Üst menü ve ekranlar:

| Menü | Ne yapılır |
|---|---|
| **Proje seçici** | Projeler arasında geçiş; her projenin kendi dokümanları, ayarları, düzeltmeleri ve diyagramları vardır |
| **Pano** | Sayılar, "model eskidi" uyarısı, sorunlu dokümanlar, sahipsiz düzeltmeler, son değişiklikler |
| **Dokümanlar** | Sürükle-bırak yükleme (aynı adla yükleme yeni sürüm olur), okuma raporu, okuyucuyu zorlama, sürümler, örnek ekleme |
| Doküman inceleme | Belge ile çıkarılanlar yan yana; bir bağa tıklayınca kanıt cümlesi vurgulanır; çözülemeyen paragraftan bağ eklenir |
| **Model › Tablolar / Süreçler** | Filtrelenebilir listeler ve ayrıntı paneli; ad, katman, tür, sistem, hassasiyet düzeltme, birleştirme, bağ ekleme/silme/yön çevirme, süreç atama, silme (önce fark önizlemesi), etki analizi, yol bulma |
| **Model › STTM** | Kolon eşlemeleri; ekle, sil, CSV indir |
| **Onay** | Taslak bağlar, LLM önerileri, birleştirme önerileri, insan bakmalı paragraflar, tanınmayan tablolar (sütunları rollere eşle); toplu kabul/ret ve önizleme |
| **Bulgular** | Doğrulama sonuçları; gerekçeyle yok sayma |
| **Diyagramlar** | Plan, özel A→B yolları, çevre diyagramları, gizlenen nesneler; üretim, çalıştırma geçmişi, diyagramı uygulama içinde açma |
| **Karşılaştır** | İki çalıştırma ya da çalıştırma ile güncel model arasındaki fark |
| **Günlük** | Tüm elle düzeltmeler (geri al/yinele) ve kim neyi ne zaman değiştirdi |
| **Ara (Ctrl+K)** | Tablo, süreç, doküman araması |
| **Örnekler** | Senaryo ve biçim örnekleri, boş şablonlar (STTM, job bağımlılık, tasarım dokümanı), biçim rehberi |
| **Ayarlar** | Proje ve genel ayarlar (okuma kuralları, ad → katman kuralları, sistem sözlüğü, okuyucu/şablonlar, diyagram, yapay zekâ), kullanıcılar, yapay zekâ konsolu, sistem durumu |

**Elle düzeltmeler kaybolmaz.** Her değişiklik bir işlem olarak saklanır ve dokümanlar
yeniden okunduğunda modele yeniden uygulanır. Hedefi artık bulunamayan düzeltme
"sahipsiz" olarak panoda ve günlükte listelenir.

**Ayarlar katmanlıdır:** paket varsayılanı < genel (yönetici) < proje. Her değerin yanında
nereden geldiği görünür; okuma kuralı değişiklikleri kaydetmeden önce önizlenebilir.

Çalışma alanı varsayılan olarak bulunduğunuz klasörde `.dwhflow/` altındadır
(`--data-dir` ile değiştirilebilir); içerik düz YAML/JSON dosyalarıdır, yedeklenebilir.

### Komut satırı

```bash
dwhflow convert --ornek --open
```

Paketle gelen örnek dokümanlarla (6 biçimde 6 doküman, kredi kartı işlemleri
senaryosu) dener ve galeriyi tarayıcıda açar.

```bash
dwhflow convert ./dokumanlar -o ./cikti --path "SRC_KART_ISLEM>BDDK_PORTAL"
```

| Seçenek | Anlamı |
|---|---|
| `-o, --out` | çıktı klasörü (varsayılan `./dwhflow-cikti/<zaman>-<başlık>`) |
| `-p, --path KAYNAK>HEDEF` | vurgulu yol diyagramı (tekrarlanabilir) |
| `--focus TABLO`, `--hops N` | bir tablonun N adımlık çevresi |
| `--no-auto` | otomatik planı (genel akış, soy ağaçları, batch) kapat |
| `--no-render` | yalnız model + rapor, diyagram yok (Node gerekmez) |
| `--quality standard` | daha hızlı, daha az katı yerleşim |
| `--llm` | kuralların çözemediği paragraflar için YZ |
| `--llm-url`, `--llm-model`, `--llm-key-env` | YZ bağlantısı (varsayılan: Ollama, `qwen3:8b`) |
| `--llm-allow-external` | yerel olmayan LLM adresine izin ver — doküman içeriği makineden çıkar |

Diyagramlardan biri üretilemezse komut 1 ile çıkar (CI'da kullanılabilir).

### Çıktı klasörü

```
cikti/
  index.html              galeri: diyagramlar, dokümanlar, bulgular
  diagrams/<id>/<id>.html etkileşimli diyagram (tek dosya, e-postayla gönderilebilir)
  diagrams/<id>/<id>.json Archify spec'i ve finalize makbuzları
  model/*.yaml            birleşik model (git'e konabilir)
  dwhflow-run.json        makine okunur rapor
```

### Model komutları

Üretilen `model/` klasörü üzerinde: `info`, `validate`, `show`, `paths`, `impact`
(bir job düşerse etkilenenler), `order` (yürütme sırası), `columns` (kolon soyu),
`export-dot`. Nesneler kimlikle ya da adıyla verilebilir (şemasız ad da olur):

```bash
dwhflow paths ./cikti/model --from SRC_KART_ISLEM --to BDDK_PORTAL
```

```bash
dwhflow impact TPT_STG_KART ./cikti/model
```

---

## Desteklenen dokümanlar

| Biçim | Ne okunur |
|---|---|
| `.xlsx` `.xlsm` `.csv` `.tsv` | STTM/eşleme tabloları, job–öncül tabloları, kolon eşlemeleri; uzun metin hücreleri metin kurallarına gider |
| `.docx` | paragraflar (başlıklar konum bilgisi olur) ve tablolar |
| `.pdf` | sayfa metni (metin katmanı olan PDF'ler) |
| `.md` `.txt` | paragraflar, markdown tabloları |
| `.sql` `.bteq` | INSERT…SELECT, CTAS, MERGE, UPDATE; her ifade ayrı süreç |
| `.jil` | Autosys job'ları, `condition: s()/d()` bağımlılıkları, başlama saatleri |
| `.yaml` `.yml` | DWHFlow model dosyaları |

Henüz desteklenmeyenler açık bir mesajla raporlanır, sessizce atlanmaz: `.doc`
`.xls` (yeni biçime kaydedin), `.vsdx` (Visio), `.dtsx` (SSIS), `.ktr/.kjb`
(Pentaho), `.dsx` (DataStage), `.cpy/.cbl` (COBOL copybook), `.pptx`.

Okunamayan tablolar ve kuralların çözemediği paragraflar dokümanın raporunda
not olarak listelenir — "ne okunmadı" her zaman görünür.

---

## Yapay zekâ (isteğe bağlı)

```bash
ollama pull qwen3:8b
dwhflow convert ./dokumanlar --llm
```

- Yalnız kuralların akış bulamadığı ama içinde en az iki tablo adı geçen
  paragraflar gönderilir.
- İstem uygulamanın içindedir: `src/dwhflow/llm/prompts/extract_flows.v1.yaml`
  (Türkçe, sürümlü, örnekli). Doküman içeriği modele *veri* olarak verilir;
  içindeki talimatlar uygulanmaz.
- Her önerilen akış için: kanıt cümlesi belgede birebir geçmeli, iki tablo adı da
  metinde bulunmalı. Geçmeyen öneri reddedilir ve sayılır.
- Kabul edilenler **taslak** olarak girer (diyagramda kesikli, "· taslak" etiketli),
  güveni en çok 0.8'dir ve kaynağında `llm:<model>@extract_flows/v1` yazar.
- Varsayılan olarak yalnız yerel adresler (`localhost`, `127.0.0.1`) kabul edilir.
  Dış bir API için ayarlarda açıkça izin vermek gerekir.

---

## Güvenlik

- Sunucu yalnız `127.0.0.1`'e bağlanır; `Host` başlığı yerel değilse istek reddedilir
  (DNS rebinding).
- Değişiklik yapan her istek `X-DWHFlow: 1` başlığı ister; başka bir sitenin sayfası bu
  başlığı gönderemez (CSRF).
- Yüklemeler boyut sınırıyla diske akıtılır; dosya adları her platformda aynı
  temizlenir; yalnız desteklenen uzantılar kabul edilir.
- Diyagram motoru sabit sürüm ve SHA-256 ile doğrulanır; zip içindeki yollar denetlenir.

---

## Geliştirme

```bash
pip install -e ".[dev]"
python -m pytest -q
python -m ruff check src tests scripts
```

Motor gerektiren testler Node.js yoksa atlanır. Örnek dokümanları yeniden üretmek için:

```bash
python scripts/ornek_dokuman_uret.py
```

Kaynak ağacı:

```
src/dwhflow/
  ingest/     okuyucular (tablo, metin, SQL, JIL…) ve model kurucu
  llm/        isteğe bağlı YZ: istemci, gömülü istem, kanıt denetimi
  model/      kanonik model (pydantic)
  validate/   20 doğrulama kuralı
  graph/      graf sorguları (yol, etki, sıra)
  render/     dilimleyici, Archify derleyicisi, motor köprüsü, onarım, galeri
  engine/     sabitlenmiş Archify zip'i + kilit dosyası
  api/        web uygulaması ve çalışma alanı
  static/     arayüz (tek sayfa, dış kaynak yok)
  examples/   örnek dokümanlar ve örnek model
```

## Bilinen sınırlar

- Visio, SSIS, Pentaho, DataStage, COBOL copybook okuyucuları henüz yok.
- Taranmış (metin katmanı olmayan) PDF'ler okunmaz.
- Veri girdisi/çıktısı bilinmeyen batch job'lar yalnız job zinciri diyagramında görünür.
- Serbest metinden çıkarılan akışlar taslaktır; kesinleştirmek için STTM ya da SQL
  gibi yapısal bir kaynakla doğrulanmaları gerekir.

## Lisans

Apache-2.0. Gömülü diyagram motoru Archify MIT lisanslıdır
(`src/dwhflow/engine/engine.lock.json` kaynak ve sürümü kaydeder).
