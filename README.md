# DWHFlow

Veri ambarı dokümanlarını **uçtan uca akış diyagramlarına** çevirir. STTM
Excel'leri, Word/PDF tasarım dokümanları, SQL/BTEQ betikleri, **DataStage** exportları,
**Control-M** ve **Airflow** zamanlamaları, AutoSys JIL, Visio/draw.io/BPMN çizimleri,
e-postalar ve zip paketleri okunur (ayrıntı: [Desteklenen dokümanlar](#desteklenen-dokümanlar));
hepsi tek bir modelde birleşir, doğrulanır ve [Archify](https://github.com/tt-a1i/archify) motoruyla
**etkileşimli, tek dosyalık HTML diyagramlarına** dönüşür: kaynak sistemden
batch job'a, binary dosyadan ETL'e, DWH tablolarından hedef tablolara kadar.

- **Ajan gerekmez.** Uygulama kendi başına çalışır: doküman ver, diyagramları al.
- **Yapay zekâ isteğe bağlıdır.** Varsayılan yol tamamen kural tabanlıdır. İstenirse
  yerel bir LLM (ör. Ollama'da Qwen3 8B) yalnız kuralların çözemediği paragraflara
  bakar; önerdiği her akış belgede harfi harfine geçen bir cümleyle kanıtlanmak
  zorundadır, yoksa reddedilir.
- **Ekip için.** Giriş, roller (izleyici/düzenleyici/yönetici), projeler, elle düzeltme,
  onay kuyruğu ve kim neyi değiştirdi günlüğü. **Giriş şimdilik kapalıdır**
  (bkz. [Ekip uygulaması](#ekip-uygulaması)).
- **Çevrimdışı.** Telemetri yok, CDN yok, motorun güncelleme denetimi kapalı.
  Sunucu varsayılan olarak yalnız `127.0.0.1`'e bağlanır.

Planlar ve gerekçeler: [PLAN_V2.md](PLAN_V2.md) (ekranlar, elle müdahale, evrensel okuma,
LLM ara formatı; fazların durumu), [ARCHIFY_PLAN.md](ARCHIFY_PLAN.md) (diyagram süreci),
[PLAN.md](PLAN.md) (ilk kapsamlı plan).

---

## Nasıl çalışır

```
dokümanlar ──► okuyucular ──► tek model ──► düzeltmeler ──► doğrulama ──► dilimler ──► Archify
 xlsx docx      30 biçim,       tablolar,     elle yapılan    20 kural     genel akış,   4 kapı:
 pdf sql dsx    tablo/metin     job'lar,      işlemler her    (SLA, döngü, soy ağacı,    şema, teslim,
 controlm       kuralları       kenarlar,     okumada         katman…)     yol, batch    kontrol,
 airflow jil…   (+ isteğe bağlı kaynak izi    yeniden                      zinciri       tarayıcı
                LLM)                          uygulanır
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

Yeni bir bilgisayarda PowerShell'e yapıştırın. Git, Python ve Node.js'i kurar, depoyu
`%USERPROFILE%\dwhflow` altına indirir, DWHFlow'u kurar ve başlatır. Depo özel olduğu için
`git clone` sırasında GitHub girişi için tarayıcı açılabilir.

```powershell
winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
git clone https://github.com/ysnblclr/dwhflow.git "$HOME\dwhflow"
Set-Location "$HOME\dwhflow"
powershell -NoProfile -ExecutionPolicy Bypass -File .\kur.ps1
.\calistir.bat
```

Önkoşullar zaten kuruluysa yalnız son dört satır yeter. Kurulum dosya gezgininden
**`kur.bat`**'a çift tıklayarak da yapılır; sonraki açılışlarda **`calistir.bat`**'a çift tıklamak yeterlidir.

| Betik | Ne yapar |
|---|---|
| `kur.bat` (`kur.ps1`) | Python 3.11+ ve Node.js 18+ denetimi, proje klasöründe `.venv` sanal ortamı (sistem Python'una dokunmaz), bağımlılıklar, `dwhflow doctor`. `-Dev` test araçlarını da kurar. Yeniden çalıştırmak güvenlidir. |
| `calistir.bat` (`calistir.ps1`) | Sunucuyu başlatır ve tarayıcıyı açar. Veriler depo klasöründeki `.dwhflow` altındadır. Durdurmak için pencerede Ctrl+C. |
| `calistir.bat -Ekip` | Ekip sunucusu: ağa açar; bu bilgisayarın adı, tam adı ve IPv4 adresleri izinli olur ve hepsi ekrana yazılır. Ek ad için `-Ad dwhflow.kurum.local`. |

Diğer seçenekler: `-Giris` (kullanıcı adı/parola iste), `-Port 8766`, `-Veri D:\dwhflow-veri`,
`-TarayiciAcma`, `-KomutuGoster` (çalıştırmadan komutu yazar). `calistir` kodu her zaman depodaki
`src/` klasöründen çalıştırır; `.venv` yalnız bağımlılıkları sağlar.

Güncellemek için depo klasöründe:

```powershell
git pull
powershell -NoProfile -ExecutionPolicy Bypass -File .\kur.ps1
```

### GitHub'dan kurulum (Linux / macOS)

```bash
git clone https://github.com/ysnblclr/dwhflow.git && cd dwhflow && bash kur.sh
```

```bash
bash calistir.sh
```

Ekip sunucusu için `bash calistir.sh --ekip`; diğer seçenekler `--giris`, `--ad`, `--port`,
`--veri`, `--tarayici-acma`, `--komutu-goster`.

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

`calistir.bat` (ya da `dwhflow serve`) ile `http://127.0.0.1:8765` adresinde açılır.

**Giriş şimdilik kapalı (geçici).** Kullanıcı adı/parola sorulmaz; adrese erişen herkes
yönetici yetkisiyle çalışır ve günlükte **ekip** olarak görünür. Host ve CSRF denetimleri
yine uygulanır; kullanıcı ve oturum kodu yerinde durur. Girişi açmak için:

- tek seferlik: `calistir.bat -Giris` (ya da `dwhflow serve --giris`);
- kalıcı: `src/dwhflow/cli.py` içindeki `GIRIS_VARSAYILAN = False` satırını `True` yapın.

Giriş açıkken ilk açılışta sunucu makinesindeki tarayıcıdan yönetici hesabı oluşturulur
(ya da komutla: `dwhflow kullanici ekle ADINIZ --rol yonetici`). Girişsiz dönemde
Ayarlar › Kullanıcılar'dan eklenen hesaplar giriş açılınca geçerli olur. Ekibe açmak için:

```powershell
.\calistir.bat -Ekip
```

Ekip üyeleri ekranda yazan adreslerden birini (ör. `http://SUNUCU_ADI:8765`) açar. Bağlanamıyorlarsa
sunucuda Windows Güvenlik Duvarı'nda port bir kez açılmalıdır (yönetici PowerShell'de):

```powershell
New-NetFirewallRule -DisplayName 'DWHFlow 8765' -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow -Profile Domain,Private
```

Elle başlatmak için eşdeğer komut: `dwhflow serve --host 0.0.0.0 --allow-host SUNUCU_ADI`.
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
| **Örnekler** | İki hazır senaryo (kredi kartı; **DataStage · Control-M · Airflow** kurum ETL zinciri) — tek tıkla proje olur; her biçimden örnek dosya ve beklenen sonucu, boş şablonlar (STTM, job bağımlılık, tasarım dokümanı), biçim rehberi |
| **Ayarlar** | Proje ve genel ayarlar (okuma kuralları, ad → katman kuralları, sistem sözlüğü, okuyucu/şablonlar, diyagram, yapay zekâ), kullanıcılar, yapay zekâ konsolu, sistem durumu |

**Elle düzeltmeler kaybolmaz.** Her değişiklik bir işlem olarak saklanır ve dokümanlar
yeniden okunduğunda modele yeniden uygulanır. Hedefi artık bulunamayan düzeltme
"sahipsiz" olarak panoda ve günlükte listelenir.

**Ayarlar katmanlıdır:** paket varsayılanı < genel (yönetici) < proje. Her değerin yanında
nereden geldiği görünür; okuma kuralı değişiklikleri kaydetmeden önce önizlenebilir.

Çalışma alanı varsayılan olarak bulunduğunuz klasörde `.dwhflow/` altındadır
(`--data-dir` ile değiştirilebilir); içerik düz YAML/JSON dosyalarıdır, yedeklenebilir.

Kullanıcılar komut satırından da yönetilir (sunucu makinesinde):

```bash
dwhflow kullanici ekle ayse --rol duzenleyici
```

`kullanici liste`, `kullanici parola AD`, `kullanici rol AD izleyici`, `kullanici sil AD` da vardır.
Oturumlar sunucu belleğinde tutulur; sunucu yeniden başlarsa herkes yeniden giriş yapar.

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

| Grup | Biçim | Ne okunur |
|---|---|---|
| ETL | DataStage `.dsx`, DataStage XML (`<DSExport>`), `.isx` | job → süreç, connector/dosya stage'leri → tablo/dosya, sequence → job sırası, stage SQL'i |
| Zamanlayıcı | Control-M XML ve Automation API JSON | job'lar, INCOND/OUTCOND ve olaylar → job sırası, TIMEFROM/WEEKDAYS, Flow |
| Zamanlayıcı | Airflow DAG (`.py`) | görevler, `>>`/`chain` sırası, sensörler, operatör SQL'i — dosya **çalıştırılmaz**, `ast` ile okunur |
| Zamanlayıcı | AutoSys `.jil` | `insert_job`, `condition: s()/d()`, `start_times` |
| Tablo | `.xlsx` `.xlsm` `.xls` `.ods` `.csv` `.tsv` | STTM/eşleme tabloları, job–öncül tabloları, kolon eşlemeleri; uzun metin hücreleri metin kurallarına gider |
| Doküman | `.docx` `.doc` `.odt` `.rtf` `.pptx` `.pdf` `.html` `.mht` `.md` `.txt` | paragraflar (başlıklar konum olur), tablolar, slayt notları; `.pdf` yalnız metin katmanıyla |
| Kod | `.sql` `.bteq` | INSERT…SELECT, CTAS, MERGE, UPDATE; her ifade ayrı süreç |
| Diyagram | `.vsdx` `.drawio` `.bpmn` `.mmd` `.dot`/`.gv` | şekiller/düğümler → tablo/job, oklar → akış, ok etiketi → job |
| E-posta, arşiv | `.eml` `.msg` `.zip` | gövde metni; ekler ve zip içindekiler ayrı doküman olarak (iç içe 3 düzey) |
| Kurum şablonu | `.xml` `.json` | ayarlardaki `okuma.sablonlar` tanımıyla kuruma özel eşleme dosyaları (örnek: `examples/ornek_sablon`) |
| Model | `.yaml` `.yml` | DWHFlow model dosyaları |

Biçim önce **içerikten** tanınır, uzantı ikinci sıradadır: `.txt` uzantılı bir JIL, `.xml`
uzantılı bir Control-M ya da DataStage exportu doğru okuyucuya gider. Emin olunamayan
dosya için okuyucu **Dokümanlar** ekranından ya da ayarlardaki `okuyucu_zorla` ile seçilir.

`.msg` için `pip install "dwhflow[ofis]"` gerekir. Özel okuyucusu olmayan biçimler için
isteğe bağlı genel dönüştürücü: `pip install "dwhflow[evrensel]"`. Okunamayanlar (SSIS
`.dtsx`, Pentaho `.ktr/.kjb`, COBOL, rar/7z) açık bir mesajla raporlanır, sessizce atlanmaz.
Taranmış (görüntü) PDF okunmaz; OCR kapsam dışıdır.

Her biçimden bir örnek ve beklenen sonucu `src/dwhflow/examples/ornek_bicimler/`
altındadır; testler her örneğin hâlâ aynı sonucu verdiğini denetler.

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
- İstemler sürümlüdür ve arayüzden (Yapay zekâ ekranı › İstem kütüphanesi) yeni sürüm
  olarak düzenlenir; gömülü sürümler `src/dwhflow/llm/prompts/` altındadır
  (`extract_flows.v2` akış çıkarma, `table_roles.v1` tanınmayan tablo başlıkları).
  Doküman içeriği modele *veri* olarak verilir; içindeki talimatlar uygulanmaz.
- Model **DAF** (`dwhflow.extract/2`) biçiminde yanıt verir: tablolar, süreçler, kolon
  eşlemeli akışlar, bağımlılıklar ve belirsizlikler. Her öğe belgede birebir geçen bir
  kanıt cümlesi taşımalı, iki tablo adı da metinde bulunmalı; geçmeyen öneri reddedilir ve sayılır.
- Kabul edilenler **taslak** olarak girer (diyagramda kesikli, onay kuyruğunda "LLM"
  etiketli); güveni en çok 0.8'dir, projedeki güven eşiğinin altındakiler alınmaz.
- Kişisel veriler (TCKN, IBAN, kart no, e-posta, telefon) istenirse gönderilmeden önce
  maskelenir; yerel olmayan bir adrese gönderimde maskeleme her zaman uygulanır.
- Varsayılan olarak yalnız yerel adresler (`localhost`, `127.0.0.1`) kabul edilir. Dış bir
  API için yöneticinin profilde açıkça izin vermesi gerekir. API anahtarı hiçbir dosyaya
  yazılmaz; profilde yalnız anahtarı tutan **ortam değişkeninin adı** saklanır.
- **Yapay zekâ konsolu** bir paragrafı modele göndermeden ne gideceğini gösterir
  ("gönderim önizlemesi") ve sonucu modele dokunmadan dener.

---

## Güvenlik

- Sunucu varsayılan olarak yalnız `127.0.0.1`'e bağlanır; `Host` başlığı izinli adlardan
  biri değilse istek reddedilir (DNS rebinding). Ekibe açarken `--allow-host` verilir.
- **Giriş şimdilik kapalı** (`serve --girissiz` varsayılan): ağdan erişen herkes yönetici
  yetkisindedir; sunucuyu yalnız güvenilen kurum ağına açın. Giriş açıkken (`--giris`)
  parolalar PBKDF2-SHA256 ile saklanır (en az 10 karakter, en az iki
  karakter türü, kullanıcı adını içeremez); art arda hatalı girişte kullanıcı adı ve adres
  bazında bekleme uygulanır. Oturum çerezi HttpOnly ve SameSite=Strict'tir; parola
  değişince kullanıcının diğer oturumları kapanır.
- İlk yönetici yalnız sunucu makinesinden (loopback) ya da komut satırından oluşturulur.
- Yetki her uçta sunucuda denetlenir (izleyici < düzenleyici < yönetici).
- Değişiklik yapan her istek `X-DWHFlow: 1` başlığı ister; başka bir sitenin sayfası bu
  başlığı gönderemez (CSRF).
- Arayüz sıkı bir CSP ile sunulur (yalnız kendi betikleri, satır içi betik yok, dış kaynak yok).
- Yüklenen dokümanlar hiçbir zaman sayfa olarak açılmaz: yalnız ek (`attachment`,
  `application/octet-stream`) olarak indirilir, önizleme metin blokları olarak gösterilir.
- Yüklemeler boyut sınırıyla (64 MB) diske akıtılır; dosya adları her platformda aynı
  temizlenir; arşivler boyut, sayı ve derinlik sınırıyla açılır; güvenilmeyen XML
  `defusedxml` ile ayrıştırılır.
- Diyagram motoru sabit sürüm ve SHA-256 ile doğrulanır; zip içindeki yollar denetlenir.
- Her değişiklik kim/ne zaman bilgisiyle proje günlüğüne yazılır.

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
  ingest/          biçim kaydı, okuyucular, önizleme, okuma yapılandırması
  ingest/formats/  eklenti okuyucular (DataStage, Control-M, Airflow, Office, diyagram, e-posta…)
  daf.py           DAF ara formatı (dwhflow.extract/2) ve modele uygulanması
  llm/             isteğe bağlı YZ: istemci, sürümlü istemler, maskeleme, kanıt denetimi
  model/           kanonik model (pydantic)
  validate/        20 doğrulama kuralı
  graph/           graf sorguları (yol, etki, sıra)
  project/         çalışma alanı, katmanlı ayarlar, düzeltme katmanı, derleme hattı, onay kuyruğu
  auth.py          kullanıcılar, parolalar, oturumlar
  api/             web uygulaması (FastAPI), arka plan işleri, uç noktalar
  static/          arayüz: derleme adımı olmayan ES modülleri (js/core, js/screens)
  render/          dilimleyici, Archify derleyicisi, motor köprüsü, onarım, galeri
  engine/          sabitlenmiş Archify zip'i + kilit dosyası
  examples/        senaryolar, biçim örnekleri (+ beklenen sonuçlar), şablon örneği
```

Geliştirme sırasında örnek bir çalışma alanıyla sunucu açmak için
`python scripts/dev_ortam.py` (giriş bilgisi `.dwhflow-dev/giris.txt`'ye yazılır, git'e girmez).

## Bilinen sınırlar

- SSIS, Pentaho ve COBOL copybook okuyucuları yok; taranmış PDF okunmaz (OCR kapsam dışı).
- DataStage transformer/lookup/join gibi ara stage'lerin kolon türetmeleri okunmaz; kolon
  soyu için STTM kullanılır.
- Veri girdisi/çıktısı bilinmeyen batch job'lar yalnız job zinciri diyagramında görünür.
- Serbest metinden çıkarılan akışlar taslaktır; kesinleştirmek için onay kuyruğundan
  onaylanmaları ya da STTM/SQL/ETL exportu gibi yapısal bir kaynakla doğrulanmaları gerekir.
- Oturumlar bellekte tutulur; sunucu yeniden başlayınca yeniden giriş gerekir.

## Lisans

Apache-2.0. Gömülü diyagram motoru Archify MIT lisanslıdır
(`src/dwhflow/engine/engine.lock.json` kaynak ve sürümü kaydeder).
