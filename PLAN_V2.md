# DWHFlow v2 — Ekranlar, Elle Müdahale, Evrensel Okuma ve LLM Ara Formatı
### Pazar araştırması ve faz faz user story planı (29 Eylül 2026) — kod içermez

> **İstek:** Daha fazla ekran; ayarların arayüzden yapılabilmesi; modele elle müdahale
> edilebilmesi; her biçim için hazır örnek girdi dosyaları; her türlü girdi dosyasının
> okunabilmesi; LLM'in girdi dosyasını çevireceği formatın tanımlanması; benzer araçların
> araştırılıp özelliklerinin listelenmesi; faz faz, user story tekniğiyle plan.
>
> **Varsayımlar** (yanlışsa §10'daki sorularla düzeltilir):
> tek kullanıcılı yerel uygulama olarak kalır; yığın aynıdır (Python + Node.js/Archify);
> çevrimdışı çalışma, `127.0.0.1`, parola saklamama ilkeleri korunur; mevcut
> 404 testlik davranış bozulmaz.
>
> Önceki planlar: [PLAN.md](PLAN.md) (kapsamlı ilk plan), [ARCHIFY_PLAN.md](ARCHIFY_PLAN.md)
> (Archify geçişi, §0'da uygulanan süreç).

---

## Uygulama durumu (30 Eylül 2026)

**Kararlar (§10'un cevapları):** kurumda **DataStage, Control-M ve Airflow** kullanılıyor;
uygulamayı **ekip** kullanacak (yukarıdaki "tek kullanıcılı" varsayımı geçersiz: giriş,
roller, projeler, günlük eklendi); **taranmış doküman gerekmiyor** (OCR, US-6.7 kapsam dışı);
gezinme **üst menüde** (US-1.1'deki sol menü yerine). Arayüz §6'daki Preact yerine derleme
adımı olmayan düz ES modülleriyle yazıldı.

| Faz | Durum | Not |
|---|---|---|
| 0 Temel | tamam | Projeler, doküman sürümleri, okuma önbelleği, 2 senaryo + her biçim için örnek ve altın test, 3 boş şablon, otomatik biçim rehberi. Okuyucular DAF yerine doğrudan model kurucuyu kullanır; DAF LLM ve içe aktarım yolunda (US-0.3 kısmen). |
| 1 Kabuk ve inceleme | tamam | Üst menü, pano, dokümanlar, doküman inceleme (kanıt vurgusu), tablolar, süreçler, bulgular, diyagramlar, Ctrl+K. Listeler 2 000 satırda kesilir (sanal liste yok). |
| 2 Ayarlar | tamam | Katmanlı ayarlar ve kaynak etiketleri, eş anlamlılar (etki önizlemeli), ad kuralları (yukarı/aşağı sıralama + test kutusu), sistem sözlüğü, diyagram, LLM profilleri, dışa/içe aktarma. US-2.7 kısmen: durum ekranı var; Node/Chrome yolu ve çalıştırma temizliği ekranda yok. |
| 3 Elle müdahale | tamam | Düzeltme katmanı (18 işlem), birleştirme ve öneriler, bağ ekle/sil/çevir, elle nesne, onay kuyruğu (toplu, önizlemeli), günlük, geri al/yinele (günlükten; Ctrl+Z yok), sahipsiz düzeltmeler, bulgu yok sayma, STTM düzenleme. US-3.12 kısmen: gizleme tüm diyagramlar için; diyagram başına sabitleme/başlık yok. |
| 4 Evrensel okuma I | tamam | .doc/.xls/.pptx/.odt/.ods/.rtf/.html/.mht, .eml/.msg, zip, Visio, draw.io, BPMN, Mermaid, Graphviz, kurum XML/JSON şablonu, içerikten tespit, isteğe bağlı genel dönüştürücü. PlantUML yok. |
| 5 Evrensel okuma II | kısmen | **DataStage** (.dsx, XML, .isx), **Control-M** (XML, JSON), **Airflow** (AST), AutoSys, eklenti okuyucu (`ingest/formats/*.py`). ODI, Informatica, SSIS, Talend, Pentaho, TWS, cron, dbt, JCL, copybook, shell/TPT yok — kurumda kullanılmıyor. |
| 6 LLM v2 | tamam (6.7 hariç) | DAF `extract/2`, tablo rolleri, istem kütüphanesi, test konsolu, maskeleme ve gönderim önizlemesi, güven eşiği, parçalama, ölçüm modülü (`llm/evaluate.py`). |
| 7 Canlı metadata | kısmen | Yalnız US-7.4 (iki çalıştırma ya da çalıştırma ↔ güncel model farkı, liste olarak). ODBC yok. |
| 8 Dışa aktarım | başlamadı | Model JSON/YAML indirme var; STTM Excel, OpenLineage, proje yedeği yok. |

---

## 0. Özet

| Konu | Karar / öneri |
|---|---|
| Yeni kavram | **Proje**: dokümanlar, ayarlar, elle düzeltmeler ve çalıştırmalar tek çatı altında. Bugünkü "her yükleme bağımsız" modeli elle müdahaleyi imkânsız kılıyor. |
| Ekranlar | 1 sayfa + 1 pencereden **14 ekrana**: pano, dokümanlar, doküman inceleme, model gezgini, süreçler, onay kuyruğu, bulgular, diyagramlar, karşılaştırma, ayarlar (7 sekme), örnekler, günlük… (§3.2) |
| Elle müdahale | **Düzeltme katmanı**: her elle değişiklik bir işlem olarak saklanır ve her yeniden okumada modele yeniden uygulanır; dokümanlar yeniden yüklense de düzeltme kaybolmaz (§3.4). |
| Evrensel okuma | 4 basamaklı zincir: özel okuyucu → genel dönüştürücü → metin kuralları → LLM; hiçbiri olmazsa "okunamadı + neden" — **sessiz atlama yok**. 14 biçimden ~45 biçime (§3.5). |
| LLM formatı | **DAF (DWHFlow Ara Formatı) `dwhflow.extract/2`**: tablolar, süreçler, kolon eşlemeli akışlar, bağımlılıklar, belirsizlikler; her öğe birebir kanıtlı. Bütün okuyucular da aynı formatı üretir (§3.6). |
| Örnek girdiler | **Tek senaryo, her biçim**: kredi kartı akışı her desteklenen biçimde yazılır; hepsinden aynı soy çıkmalı (altın test). + boş şablonlar (§3.7). |
| Öne çıkan ders | Solidatus: YZ önerileri "kum havuzunda", görsel fark + geri al; OpenMetadata/DataHub/Dataedo: elle bağ düzenleme; erwin: STTM editörü; Manta/Octopai: okuyucu kütüphanesinin genişliği asıl değer (§2.5). |
| Süre | 9 faz, tek geliştiriciyle ~25 hafta. **MVP = Faz 0–3 (~10 hafta)**: ekranlar + ayarlar + elle müdahale. |

---

## 1. Bugün ne var, ne eksik

| Alan | Bugün | Eksik |
|---|---|---|
| Arayüz | Tek sayfa: yükle → dönüştür → galeri; önceki çalıştırmalar; LLM ayar penceresi | Model, doküman, bulgu, diyagram ekranları yok; ayarların çoğu kodda |
| Müdahale | Yok. Yanlış okunan tablo ancak dokümanı düzelterek düzelir | Ad/katman/tür düzeltme, birleştirme, bağ ekleme/silme, taslak onayı |
| Okuma | 14 uzantı: xlsx xlsm csv tsv docx pdf md txt sql bteq jil yaml yml | Eski Office, pptx, html, e-posta, zip, Visio, draw.io, ETL/scheduler exportları, JCL, copybook, taranmış belge |
| LLM | Yalnız serbest paragraftan `flows` (kaynak, hedef, job, kanıt) | Tablo/süreç/kolon/bağımlılık çıkarımı, tanınmayan tablo başlığı eşleme, istem editörü, ölçüm |
| Örnekler | 6 dosya, 6 biçim | Her biçim için örnek, boş şablonlar, biçim rehberi |
| Kalıcılık | Her çalıştırma ayrı klasör | Proje, doküman sürümleri, düzeltme geçmişi |

---

## 2. Pazar araştırması

### 2.1 Ticari veri soyu (lineage) araçları

| Araç | Ne yapar | Öne çıkan özellikler | Bizim için ders |
|---|---|---|---|
| **IBM Manta Data Lineage** | Kod tarayarak otomatik soy | 50+ tarayıcı; Informatica mapping'leri, DataStage job'ları, Ab Initio, SAS, SSIS kodunu çözümler; Teradata'da SQL, BTEQ, saklı yordam, makro, TPT ve sorgu loglarını okur; kolon düzeyi | Asıl değer **okuyucu kütüphanesinin genişliği**. Teradata/BTEQ/TPT bizim kitle için kritik |
| **Cloudera Data Lineage (eski Octopai)** | Otomatik keşif + katalog | Sistemler arası, sistem içi (ör. PowerCenter mapping içindeki ifade, lookup, join) ve uçtan uca kolon soyu; 60+ bağlayıcı; desteklenmeyen sistemler için "özel soy bağlayıcısı" | Sistem içi dönüşüm ayrıntısı (kolon ifadeleri) + kullanıcı tanımlı bağlayıcı fikri |
| **Solidatus** | Soy modelleme + YZ asistanı | PDF, Excel ve görüntülerden YZ ile yapısal soy; her YZ önerisi **kum havuzunda, görsel farkla, geri al/yinele ile** onaylanır; güven puanı; halüsinasyon tespiti; denetim izi; Purview/Collibra/Informatica entegrasyonu | Bizim LLM yaklaşımımızın ticari karşılığı. **Onay kuyruğu + fark önizleme + geri al** doğrudan alınmalı |
| **erwin Data Intelligence (Mapping Manager)** | Kaynak-hedef eşleme (STTM) yönetimi | Sürükle-bırak eşleme; ETL/saklı yordamdan tersine mühendislik; ileri/geri/çift yönlü soy; eşlemelerde merkezi sürüm kontrolü | **STTM'nin kendisi bir ekran olmalı**: satır satır eşleme editörü + Excel'e dışa aktarım |
| **Gudu SQLFlow** | SQL'den soy | 39 veritabanı lehçesi; kolon soyu; saklı yordam çağrı grafı; ER diyagramı; dbt; bulut/kurum içi/API | Prosedür çağrı grafı; çok lehçeli SQL (bizde sqlglot) |
| **Dataedo** | Dokümantasyon + katalog | Portalda **düzenleme moduyla elle soy** (veri sorumluları); desteklenmeyen sistemler için "elle nesne" (form, API, dosya); OpenLineage içe aktarımı; ERD | Elle nesne + elle soy; OpenLineage uyumu |
| **Microsoft Purview** | Bulut veri yönetişimi | Data Factory/Power BI'dan otomatik soy; eksik yerler için portalda elle soy; Atlas REST API ile özel soy | Dış kataloglara aktarım için Atlas/Purview uyumlu JSON (Faz 8) |

### 2.2 Açık kaynak

| Proje | ★ | Lisans | Ne yapar | Ders |
|---|---|---|---|---|
| [OpenMetadata](https://github.com/open-metadata/OpenMetadata) | 15.4k | Apache-2.0 | Katalog; SQL sorgu analiziyle kolon soyu; **sürükle-bırak ile tablo ve kolon soyunu elle düzenleme** | Kolon ucundan tutup çizgi çekme etkileşimi |
| [DataHub](https://github.com/datahub-project/datahub) | 12.8k | Apache-2.0 | Katalog; arayüzden **yukarı/aşağı akış bağı ekleme/çıkarma** (veri kümesi, pano, job) | "Yukarı akışı düzenle" penceresi: ada göre ara, seç, kaydet |
| [Marquez](https://github.com/MarquezProject/marquez) + [OpenLineage](https://github.com/OpenLineage/OpenLineage) | 2.3k / 2.7k | Apache-2.0 | Soy standardı ve referans sunucu | Dışa aktarım standardı |
| [Apache Atlas](https://github.com/apache/atlas) | 2.1k | Apache-2.0 | Hadoop çevresi metadata/yönetişim | Purview'ün de kullandığı tip sistemi |
| [Amundsen](https://github.com/amundsen-io/amundsen) | 4.8k | Apache-2.0 | Veri keşfi | Arama öncelikli gezinme |
| [sqllineage](https://github.com/reata/sqllineage) | 1.7k | MIT | Python SQL soy analizi | sqlglot'a alternatif/doğrulayıcı |
| [sqlglot](https://github.com/tobymao/sqlglot) | 9.6k | MIT | SQL ayrıştırıcı (kullanıyoruz) | — |
| [Spline](https://github.com/AbsaOSS/spline) | 0.7k | Apache-2.0 | Spark soy takibi | Kapsam dışı (Spark) |
| [tokern/data-lineage](https://github.com/tokern/data-lineage) | 0.3k | — | Sorgu geçmişinden soy | Faz 7'de log tablolarından soy fikri |
| [Archify](https://github.com/tt-a1i/archify) | 74k | MIT | Doğrulanmış diyagram motoru (kullanıyoruz) | — |

### 2.3 Doküman okuma ve LLM çıkarım kütüphaneleri

| Proje | ★ | Lisans | Özellikler | Kullanım önerisi |
|---|---|---|---|---|
| [MarkItDown](https://github.com/microsoft/markitdown) | 188k | MIT | PDF, Word, PowerPoint, Excel, görüntü (EXIF + OCR), ses, HTML, CSV/JSON/XML, ZIP, EPUB, Outlook `.msg` → Markdown; biçim başına isteğe bağlı bağımlılık; LLM görüntü ile OCR eklentisi | **Genel dönüştürücü** basamağı (isteğe bağlı ek) |
| [Docling](https://github.com/docling-project/docling) | 68k | MIT | Gelişmiş PDF (sayfa düzeni, okuma sırası, tablo yapısı), OCR, DOCX/PPTX/XLSX/HTML/EPUB, `.eml`/`.msg`, görüntü; kayıpsız JSON | Taranmış/karmaşık PDF için **ağır, isteğe bağlı** ek |
| [Unstructured](https://github.com/Unstructured-IO/unstructured) | 15.5k | Apache-2.0 | 60+ dosya türü; PDF/görüntü için poppler + tesseract gerekir | Alternatif; sistem bağımlılıkları taşınabilirliği zorlar |
| [Apache Tika](https://github.com/apache/tika) | 4.1k | Apache-2.0 | 1000+ dosya türünden metin ve metadata | Java gerektirir → varsayılanda yok |
| [LangExtract](https://github.com/google/langextract) | 39k | Apache-2.0 | LLM ile yapısal çıkarım; **her çıkarımı kaynak metindeki karakter aralığına bağlar**, bulunamayanı işaretler; uzun doküman için parçalama, paralel, çok geçiş; etkileşimli HTML inceleme; Ollama desteği | Kanıt → karakter aralığı ve **doküman inceleme ekranı** fikri |
| [Neo4j LLM Graph Builder](https://github.com/neo4j-labs/llm-graph-builder) | 5.3k | Apache-2.0 | Yapısız veriden LLM ile bilgi grafı | Şema kısıtlı çıkarım fikri |

### 2.4 Özellik matrisi

✓ = kaynakla doğrulandı · – = yok ya da bu araştırmada doğrulanamadı · ◐ = kısmen

| Özellik | DWHFlow bugün | DWHFlow v2 | Manta | Octopai | Solidatus | erwin DI | SQLFlow | Dataedo | OpenMetadata | DataHub |
|---|---|---|---|---|---|---|---|---|---|---|
| SQL'den otomatik soy | ✓ | ✓ | ✓ | ✓ | – | ✓ | ✓ | ✓ | ✓ | – |
| ETL aracı exportları (Informatica, DataStage, SSIS…) | – | ✓ | ✓ | ✓ | – | ✓ | – | – | – | – |
| Scheduler (AutoSys, Control-M…) | ◐ JIL | ✓ | – | – | – | – | – | – | – | – |
| Yapısız dokümandan soy (Word/PDF/metin) | ✓ | ✓ | – | – | ✓ | – | – | – | – | – |
| YZ önerisini kanıt + onayla kabul | ◐ kanıt | ✓ | – | – | ✓ | – | – | – | – | – |
| Elle soy düzenleme | – | ✓ | – | – | ✓ | ✓ | – | ✓ | ✓ | ✓ |
| STTM / eşleme editörü | – | ✓ | – | – | – | ✓ | – | – | – | – |
| Kolon düzeyi soy | ◐ | ✓ | ✓ | ✓ | – | ✓ | ✓ | ✓ | ✓ | – |
| Türkçe dil kuralları | ✓ | ✓ | – | – | – | – | – | – | – | – |
| Tamamen çevrimdışı / tek kullanıcı kurulum | ✓ | ✓ | – | – | – | – | ◐ | – | – | – |
| Açık kaynak | – | – | – | – | – | – | – | – | ✓ | ✓ |
| OpenLineage içe/dışa | – | ✓ | – | – | – | – | – | ✓ | – | – |

### 2.5 Çıkarımlar

**Alınacaklar**

1. **Onay kuyruğu + fark önizleme + geri al** (Solidatus): taslaklar ve LLM önerileri modele doğrudan girmez; kuyrukta kanıtıyla bekler, kabul edilmeden önce "bu değişiklik şu bağları ekler/siler" farkı görünür.
2. **Elle soy** (OpenMetadata, DataHub, Dataedo): "yukarı/aşağı akışı düzenle" penceresi, ada göre arayıp bağ ekleme; dokümanda olmayan sistemler için **elle nesne**.
3. **STTM editörü** (erwin): kaynak.kolon → hedef.kolon + dönüşüm, satır satır; düzeltilmiş hâliyle Excel'e geri yazma.
4. **Okuyucu kütüphanesi** (Manta, Octopai): değer biçim sayısında. Okuyucular eklenti olarak eklenebilmeli; kurum kendi biçimi için şablon tanımlayabilmeli ("özel bağlayıcı").
5. **Kanıtı karakter aralığına bağlama ve belge içinde vurgulama** (LangExtract): doküman inceleme ekranının temeli.
6. **Genel dönüştürücü** (MarkItDown/Docling): özel okuyucusu olmayan biçimler için yedek basamak; isteğe bağlı ek paket.
7. **OpenLineage / Atlas uyumlu dışa aktarım**: kurumun ileride alacağı kataloğa geçiş yolu.

**Alınmayacaklar:** Sunucu/küme mimarisi, çok kiracılı katalog, canlı sorgu logu dinleme (Faz 7'deki salt okunur log okuma dışında), Java tabanlı Tika (taşınabilirlik).

**Farkımız:** doküman öncelikli (sisteme erişim olmadan da çalışır), Türkçe dil kuralları, kanıtı zorunlu LLM, tamamen yerel ve çevrimdışı, doğrulanmış diyagramlar, tek komutla kurulum.

---

## 3. Hedef ürün

### 3.1 Kavramlar

| Kavram | Tanım |
|---|---|
| **Proje** | Bir iş alanının (ör. "Kredi kartı") dokümanları, ayarları, düzeltmeleri ve çalıştırmaları. Klasör: `<çalışma alanı>/projeler/<ad>/` |
| **Doküman sürümü** | Aynı adla yeniden yüklenen dosya yeni sürüm olur; eskisi arşivde kalır |
| **Okuma** | Doküman → DAF parçaları (her öğe kanıtlı). Okuyucu değişmedikçe önbellekten gelir |
| **Model** | Tüm DAF parçaları + **düzeltme katmanı** → birleşik model |
| **Taslak** | Yalnız serbest metinden ya da LLM'den gelen, onay bekleyen öğe |
| **Düzeltme** | Elle yapılan, kalıcı, geri alınabilir işlem |
| **Çalıştırma** | Modelin o anki hâlinden üretilen diyagram seti (bugünkü "run") |

### 3.2 Ekran haritası

```
┌────────────┬───────────────────────────────────────────────────────────┐
│ DWHFlow    │  Proje: Kredi kartı ▾      [ Ara… Ctrl+K ]     ⚙  ?         │
├────────────┼───────────────────────────────────────────────────────────┤
│ Pano       │                                                           │
│ Dokümanlar │                  (seçili ekranın içeriği)                  │
│ Model      │                                                           │
│  Tablolar  │                                                           │
│  Süreçler  │                                                           │
│  STTM      │                                                           │
│ Onay (12)  │                                                           │
│ Bulgular   │                                                           │
│ Diyagramlar│                                                           │
│ Karşılaştır│                                                           │
│ ────────── │                                                           │
│ Örnekler   │                                                           │
│ Ayarlar    │                                                           │
│ Günlük     │                                                           │
└────────────┴───────────────────────────────────────────────────────────┘
```

| # | Ekran | Amaç | Ana eylemler | Faz |
|---|---|---|---|---|
| E1 | **Projeler** | Proje listesi | yeni, aç, yedekle, sil | 0 |
| E2 | **Pano** | Projenin sağlığı tek bakışta | sayılar → ilgili ekrana; "yeniden oku", "diyagram üret" | 1 |
| E3 | **Dokümanlar** | Yükleme ve okuma raporu | yükle (dosya/klasör/zip), sürümler, yeniden oku, okuyucuyu zorla, sil | 1 |
| E4 | **Doküman inceleme** | Belge ile çıkarılanı yan yana görmek | öğeye tıkla → kanıt vurgulanır; okunamayan paragraflar işaretli; paragraftan "bağ oluştur" | 1 / 3 |
| E5 | **Model › Tablolar ve dosyalar** | Tüm veri nesneleri | filtre, detay paneli, düzenle, birleştir, elle nesne ekle | 1 / 3 |
| E6 | **Model › Süreçler** | Job, prosedür, betik | zamanlama, öncül/ardıl, okuduğu/yazdığı; düzenle | 1 / 3 |
| E7 | **Model › STTM** | Kolon eşlemeleri | satır satır düzenle, Excel'e aktar | 3 / 8 |
| E8 | **Onay kuyruğu** | Taslaklar, LLM önerileri, birleştirme önerileri | kabul, reddet, düzenleyerek kabul, toplu işlem, fark önizleme | 3 |
| E9 | **Bulgular** | Doğrulama sonuçları | filtre, özneye git, gerekçeyle yok say | 1 / 3 |
| E10 | **Diyagramlar** | Diyagram planı ve sonuçlar | yol/çevre ekle, nesne gizle/sabitle, yeniden üret, önizle | 1 / 3 |
| E11 | **Karşılaştır** | İki çalıştırma ya da doküman–veritabanı farkı | fark listesi, renkli fark diyagramı | 7 |
| E12 | **Örnekler ve şablonlar** | Hazır girdiler | "bununla dene", şablon indir, biçim rehberi | 0 |
| E13 | **Ayarlar** (7 sekme) | Her şeyi arayüzden ayarlamak | §3.3 | 2 |
| E14 | **Günlük** | Kim neyi ne zaman değiştirdi | filtre, geri al | 3 |

### 3.3 Ayarlar

**Katmanlama:** paket varsayılanı < kullanıcı (`~/.dwhflow/ayarlar.yaml`) < proje
(`proje/ayarlar.yaml`). Her ayarın yanında etkin değer ve **nereden geldiği** görünür;
"varsayılana dön" her düzeyde var. Ayar dosyaları YAML'dır, ekip içinde paylaşılabilir.

| Sekme | İçerik |
|---|---|
| Genel | dil, tema, çalışma alanı konumu, paralellik, eski çalıştırmaları temizleme |
| Okuma kuralları | STTM sütun eş anlamlıları (rol → başlık listesi); metin kuralları sözlüğü (iş kelimeleri, fiiller, hâl ekleri — ileri düzey); tablo adı deseni; biçim → okuyucu eşlemesi |
| Adlandırma ve katman | sıralı kural listesi: önek/sonek/düzenli ifade → katman + tür (bugünkü `SRC_`, `STG_`, `DIM_`… kuralları); **test kutusu**: ad yaz, sonucu gör |
| Sistemler | kaynak sistem sözlüğü ve eş anlamlıları ("Core Banking" = "CB" = "Ana Bankacılık") |
| Diyagram | diyagram başına tablo/job bütçesi, otomatik diyagram türleri, kalite, etiket uzunluğu, görsel önayar |
| Yapay zekâ | bağlantı profilleri (birden fazla), model, zaman aşımı, parça boyutu, güven eşiği, uygulanacak doküman türleri, maskeleme kuralları, istem kütüphanesi (Faz 6) |
| Bağlantılar ve sistem | ODBC bağlantıları (Faz 7), Node.js ve Chrome yolu, motor denetimi, disk kullanımı |

Okuma ayarı değişince kaydetmeden önce **etki önizlemesi** gösterilir:
"Bu değişiklikle 3 dokümanda 41 yeni satır tanınır, 2 tablo katman değiştirir".

### 3.4 Elle müdahale: düzeltme katmanı

Sorun: model her seferinde dokümanlardan yeniden kurulur; modele doğrudan yazılan
bir düzeltme bir sonraki okumada kaybolur. Çözüm: düzeltmeler **ayrı bir işlem
listesi** olarak saklanır ve okumadan sonra modele sırayla uygulanır.

```yaml
# projeler/kredi-karti/duzeltmeler.yaml — elle yapılan değişiklikler (sıra önemlidir)
- id: d-0007
  islem: katman_degistir
  hedef: {nesne: STG_MUSTERI}          # iç kimlikle değil ADLA hedeflenir
  deger: {katman: ods}
  neden: "Tasarım dokümanında eski ad; aslında ODS tablosu"
  kim: <işletim sistemi kullanıcısı>
  zaman: 2026-09-29T10:12:00
- id: d-0008
  islem: birlestir
  hedef: {nesneler: [MUSTERI_TBL, STG_MUSTERI]}
  deger: {ad: STG_MUSTERI}
- id: d-0009
  islem: bag_ekle
  hedef: {kaynak: EXP_BDDK_KART, hedef: BDDK_PORTAL}
  deger: {surec: MFT_BDDK, tur: transfers}
  neden: "Operasyon ekibiyle doğrulandı"
```

**İşlemler:** `yeniden_adlandir`, `birlestir`, `ayir`, `tur_degistir`, `katman_degistir`,
`sistem_ata`, `hassasiyet_ata`, `aciklama`, `nesne_ekle`, `nesne_sil`, `bag_ekle`, `bag_sil`,
`bag_yon_cevir`, `surec_ata`, `kolon_esle`, `onayla`, `reddet`, `bulgu_yoksay`,
`diyagramda_gizle`, `diyagramda_sabitle`.

**Kurallar**
- Düzeltmeler adla hedeflenir (bugünkü ad birleştirme anahtarıyla: büyük/küçük harf ve şema önekine duyarsız); böylece yeniden okumada iç kimlik değişse de tutar.
- Hedefi artık bulunamayan düzeltme **sahipsiz** olur: uygulanmaz, Pano ve Günlük'te listelenir; kullanıcı yeniden bağlar ya da siler.
- Elle öğeler kaynağında `elle:<kullanıcı>` taşır, **onaylı** sayılır; diyagramda "elle" etiketiyle ayırt edilebilir (ayarla kapatılabilir).
- Doküman ile düzeltme çelişirse (doküman A→B diyor, düzeltme bağı silmiş) düzeltme kazanır ama bulgu üretilir: "doküman ile elle düzeltme çelişiyor".
- Her işlem geri alınabilir; Günlük ekranı `duzeltmeler.yaml`'ın okunabilir hâlidir.

### 3.5 Evrensel okuma

**Zincir** (her doküman için yukarıdan aşağı; ilk sonuç veren basamakta durulur,
ama yapısal okuyucunun okuyamadığı paragraflar alttaki basamaklara iner):

```
1. Biçim tespiti      uzantı + içerik (sihirli bayt, XML kök elemanı: .txt içinde JIL, .xml içinde DTSX)
2. Özel okuyucu       yapısal: tablo başlıkları, XML şeması, SQL ayrıştırma      → DAF (onaylı)
3. Genel dönüştürücü  tanınmayan biçim → metin/Markdown (MarkItDown, isteğe bağlı) → 4
4. Metin kuralları    Türkçe/İngilizce cümle kalıpları                             → DAF (taslak)
5. LLM (isteğe bağlı) kalan paragraflar ve tanınmayan tablo başlıkları             → DAF (taslak, kanıtlı)
6. Rapor              hiçbiri olmadıysa: "okunamadı + neden + ne yapılabilir"
```

**Biçim matrisi**

| Grup | Biçimler | Yöntem | Faz | Bağımlılık |
|---|---|---|---|---|
| Var | xlsx xlsm csv tsv docx pdf(metin) md txt sql bteq jil yaml yml | mevcut okuyucular | — | var |
| Eski Office | .doc .xls | .xls: xlrd; .doc: LibreOffice varsa dönüştür, yoksa OLE metin çıkarımı + uyarı | 4 | xlrd, olefile |
| Ofis/web | .pptx .odt .ods .rtf .html .htm .xml .json | python-pptx; ODF zip-XML; striprtf; stdlib html.parser; genel XML/JSON şablonu | 4 | isteğe bağlı `[ofis]` eki |
| E-posta ve arşiv | .eml .msg .zip (iç içe), klasör | stdlib email; extract-msg; zipfile (boyut/derinlik sınırı) | 4 | `[ofis]` |
| Diyagram | .vsdx .drawio .bpmn .mmd .dot .puml | şekil → nesne, bağlayıcı/ok → akış; şekil kalıbı ayarı | 4 | stdlib (zip + XML) |
| ETL | ODI Smart Export XML, Informatica PowerCenter XML, SSIS .dtsx, DataStage .dsx/.isx, Talend .item, Pentaho .ktr/.kjb | XML şemasına özel okuyucu; gömülü SQL → sqlglot | 5 | defusedxml |
| Scheduler | Control-M XML, AutoSys (genişletme: box, file watcher), TWS/IWS, cron, Airflow DAG (.py) | koşul/öncül → `precedes`; Airflow için Python AST (çalıştırmadan) | 5 | stdlib |
| Dönüşüm aracı | dbt `manifest.json`, OpenLineage olay JSON | ref/source grafiği; olay → akış | 5 | stdlib |
| Mainframe | JCL, COBOL copybook (.cpy) | adım sırası, DD DSN → dosya; alan düzeni → kolonlar (PIC, COMP-3) | 5 | stdlib |
| Betik | .sh .ksh .bat, Teradata TPT, PL/SQL paket, T-SQL prosedür | araç çağrıları (bteq, tpt, fastload, sqlplus, sftp) → süreç + dosya; prosedür çağrı grafı | 5 | sqlglot |
| Görsel | .png .jpg .tiff, taranmış PDF | OCR (Tesseract varsa) ya da görsel-dil modeli (isteğe bağlı) → metin → 4/5 | 6 | `[ocr]` eki + Tesseract |
| Kurum biçimi | herhangi XML/JSON/CSV | **kullanıcı tanımlı şablon**: XPath/JSONPath/sütun → kaynak/hedef/job; ekrandan tanımlanır | 4 | stdlib |

**Güvenlik:** yüklenen XML'ler XXE'ye karşı `defusedxml` ile okunur; zip için toplam boyut,
dosya sayısı ve derinlik sınırı; Office makroları ve Airflow/Python dosyaları **asla
çalıştırılmaz** (yalnız ayrıştırılır).

**Taşınabilirlik:** varsayılan kurulum hafif kalır; ağır okuyucular ek paketlerle gelir
(`pip install dwhflow[ofis]`, `[evrensel]` = MarkItDown, `[ocr]`). Eksik ek, ilgili
dokümanın raporunda "bu biçim için `[ofis]` eki gerekli" diye yazılır; `doctor` hepsini listeler.

### 3.6 LLM ara formatı: DAF (`dwhflow.extract/2`)

DAF, **hem LLM'in hem de bütün kural tabanlı okuyucuların** ürettiği ortak ara formattır.
Böylece birleştirme, onay kuyruğu, kanıt vurgulama ve testler tek bir yoldan geçer.
LLM için üç görev vardır; hepsi aynı zarfla çağrılır.

#### Girdi zarfı (LLM'e giden)

```text
<belge dosya="Kart_Tasarim_Dokumani.docx" konum="§ 2 Yükleme · paragraf 3" tur="tasarim_dokumani">
Kart işlemleri gece 01:00'de TPT_STG_KART ile STG_KART_ISLEM tablosuna yüklenir.
Ardından SP_ODS_KART prosedürü STG_KART_ISLEM verisini ODS_KART_ISLEM tablosuna
ISLEM_ID üzerinden merge eder; TUTAR alanı TL'ye çevrilerek TUTAR_TL'ye yazılır.
</belge>
<bilinen_adlar>STG_KART_ISLEM, ODS_KART_ISLEM, DWH.F_KART_ISLEM, TPT_STG_KART</bilinen_adlar>
```

`bilinen_adlar` kural tabanlı okumadan gelir; yalnız adların yazımını hizalamaya yarar.
Metinde geçmeyen bilinen ad çıktıda kullanılırsa öğe yine reddedilir.

#### Görev A — akış çıkarımı: çıktı şeması

```json
{
  "schema": "dwhflow.extract/2",
  "datasets": [
    {"name": "STG_KART_ISLEM", "kind": "staging_table", "layer": "staging", "system": null,
     "columns": [{"name": "ISLEM_ID", "type": null, "pk": true}],
     "evidence": "…STG_KART_ISLEM tablosuna yüklenir."}
  ],
  "processes": [
    {"name": "SP_ODS_KART", "kind": "stored_procedure", "tool": null, "load_strategy": "merge",
     "schedule": {"times": [], "calendar": null},
     "evidence": "Ardından SP_ODS_KART prosedürü STG_KART_ISLEM verisini ODS_KART_ISLEM tablosuna ISLEM_ID üzerinden merge eder;"}
  ],
  "flows": [
    {"source": "STG_KART_ISLEM", "target": "ODS_KART_ISLEM", "process": "SP_ODS_KART",
     "columns": [{"source": "TUTAR", "target": "TUTAR_TL", "expression": "TL'ye çevrilerek"}],
     "evidence": "Ardından SP_ODS_KART prosedürü STG_KART_ISLEM verisini ODS_KART_ISLEM tablosuna ISLEM_ID üzerinden merge eder; TUTAR alanı TL'ye çevrilerek TUTAR_TL'ye yazılır.",
     "confidence": 0.85}
  ],
  "dependencies": [
    {"before": "TPT_STG_KART", "after": "SP_ODS_KART", "condition": "success",
     "evidence": "Ardından SP_ODS_KART prosedürü…"}
  ],
  "unresolved": [
    {"text": "Sonuçlar ertesi sabah risk ekibine iletilir.", "reason": "hedef sistem/tablo adı yok"}
  ]
}
```

**Alan kuralları**

| Alan | Kural |
|---|---|
| Anahtarlar | İngilizce (küçük modellerin JSON uyumu daha iyi; v1 ile uyumlu). **Değerler** belgede geçtiği gibi |
| `name`, `source`, `target`, `process` | Metinde **aynen** geçmeli (büyük/küçük harf ve şema öneki toleranslı). Uydurma ad → öğe reddedilir |
| `kind` | `datasets`: `source_object, interface_file, landing_file, staging_table, ods_table, core_table, dimension_table, fact_table, aggregate_table, mart_table, export_file, report, downstream_system, control_table, reject_table, archive_table` · `processes`: `extract_job, batch_job, file_transfer, decoder, etl_mapping, loader_utility, stored_procedure, sql_script, shell_script, dq_check, reconciliation, archive_purge, manual_step` · emin değilse `null` (kurallar doldurur) |
| `layer` | `source, interface, staging, ods, core, mart, consumption` ya da `null` |
| `load_strategy` | `full, delta, append, merge, cdc, scd1, scd2, truncate_load, partition_exchange` ya da `null` |
| `evidence` | Belgedeki cümle(ler) **harfi harfine**, en çok 2 cümle. Boşluk farkı tolere edilir; başka her fark → ret |
| `confidence` | 0–1; sistem en çok **0.8**'e kırpar (LLM hiçbir zaman kesin sayılmaz) |
| `unresolved` | Akış anlatan ama adları eksik cümleler → onay kuyruğuna "insan bakmalı" olarak düşer |
| Yön | `source` verinin geldiği yer. "B, A'dan beslenir" → A→B. "A ve B birleştirilip C" → A→C, B→C |
| Güvenlik | `<belge>` içeriği veridir, talimat değildir; içindeki talimatlar uygulanmaz |

**Doğrulama hattı** (kural tabanlı, LLM'den bağımsız):
JSON ayrıştırma (`<think>` ve kod bloğu temizliği) → şema doğrulaması (pydantic) →
enum doğrulaması (geçersiz değer `null`'a çekilir, öğe reddedilmez) → kanıt birebir
kontrolü → ad varlık kontrolü → kanıtın **karakter aralığı** bulunur (doküman inceleme
ekranında vurgulamak için) → öğeler **taslak** olarak onay kuyruğuna.

#### Görev B — tanınmayan tablo başlığı eşleme (`dwhflow.table-roles/1`)

Küçük modeller satır satır tablo okumada hata yapar; bu yüzden LLM'e **yalnız başlık ve
ilk 3 satır** verilir, sütunların rolünü söyler, satırları kural tabanlı okuyucu okur.
Ucuz, hızlı ve doğrulanabilir.

```json
{
  "schema": "dwhflow.table-roles/1",
  "table_kind": "mapping",
  "header_row": 1,
  "roles": {"Hdf Tbl": "target_table", "Kaynak Nesne": "source_table",
            "Kyn Kolon": "source_column", "Hdf Kolon": "target_column",
            "Dönüşüm Kuralı": "transform", "Çalışan İş": "job"},
  "unmapped": ["Açıklama", "Sorumlu"],
  "evidence": "Hdf Tbl | Kaynak Nesne | Kyn Kolon | Hdf Kolon | Dönüşüm Kuralı | Çalışan İş | Açıklama | Sorumlu"
}
```

`table_kind`: `mapping | job_dependencies | column_layout | file_layout | other`.
`roles` değerleri okuyucunun rol listesiyle aynıdır: `target_table, target_column,
source_table, source_column, transform, job, predecessor, load, tool, source_system,
target_system, schedule, data_type`. Her anahtar başlıkta birebir geçmelidir. Kullanıcı
onaylarsa eşleme **okuma kurallarına eş anlamlı olarak eklenir** — aynı başlık bir daha
LLM'e gitmez.

#### Görev C — görsel/diyagram (Faz 6, isteğe bağlı)

OCR ya da görsel-dil modeli önce **metin + kutu-ok listesi** üretir; sonra Görev A aynı
şemayla çalışır. Kanıt, OCR metnidir; görselden gelen her öğe ayrıca "görselden" diye işaretlenir.

#### Kural tabanlı okuyucular da DAF üretir

Örneğin STTM satırı `ODS_KART_ISLEM ← STG_KART_ISLEM (SP_ODS_KART)` şu DAF parçası olur:
`flows: [{source, target, process, evidence: "STTM · satır 3: …", confidence: 1.0}]`,
kaynağı `excel:tablo`. Fark yalnız güven ve durumdadır (yapısal okuyucu = onaylı).

### 3.7 Örnek girdi korpusu

**İlke: tek senaryo, her biçim.** Kredi kartı işlemleri akışı (kaynak → dosya → staging →
ODS → DWH → mart → BDDK) her desteklenen biçimde ayrı ayrı yazılır. Her dosyanın
**altın çıktısı** (beklenen DAF) testlerde tutulur; tümü birlikte okunduğunda bugünkü
12 tablo / 14 süreçlik model çıkmalıdır.

| Klasör | İçerik |
|---|---|
| `ornekler/kart/` | bugünkü 6 dosya + her yeni biçimin kart senaryosu sürümü (dtsx, PowerCenter XML, ODI XML, Control-M XML, vsdx, drawio, JCL, copybook, pptx, eml, taranmış PDF…) |
| `ornekler/musteri360/` | ikinci senaryo (farklı adlandırma kuralları, İngilizce doküman) — genelleme testi |
| `ornekler/zor/` | bozuk dosya, şifreli PDF, zip bombası taklidi, 200 sayfalık doküman, **içinde LLM'e talimat gömülü metin** (istem enjeksiyonu testi), aynı tablonun 4 farklı yazımı |
| `sablonlar/` | boş STTM Excel şablonu, job bağımlılık şablonu, Word tasarım dokümanı şablonu, biçim rehberi (hangi başlıklar ve cümle kalıpları tanınır) |

Örnekler ekranında her dosyanın yanında "bununla dene" düğmesi ve beklenen sonucun
küçük önizlemesi bulunur.

---

## 4. Roller

| Rol | İhtiyaç |
|---|---|
| **DWH analisti** | Dağınık dokümandan akışı çıkarmak, yanlışları düzeltmek, diyagramı paylaşmak |
| **ETL geliştiricisi** | Değişikliğin etkisini görmek; ETL exportlarını doğrudan okutmak |
| **Veri yönetişimi / denetim** | Regülatör raporunun kaynağını kanıtıyla göstermek; kim neyi değiştirdi |
| **Batch operasyon** | Job zincirini, zamanlamayı, bir job düşerse etkilenenleri görmek |
| **Sistem yöneticisi** | Kurmak, güncellemek, taşımak, ayarları ekiple paylaşmak |

---

## 5. Faz faz plan

**Biçim:** `US-<faz>.<no>` · *Bir [rol] olarak, [istek] istiyorum; böylece [fayda].*
Kabul kriterleri Türkçe Gherkin ile: **Diyelim ki / Eğer / O zaman**.
Öncelik MoSCoW (**M** olmalı, **S** olmalı ama ertelenebilir, **C** olabilir).
Büyüklük: S (≤1 gün), M (2–3 gün), L (1 hafta).

**Her faz için tamamlanma tanımı:** birim + entegrasyon testleri geçer; ilgili örnek
dosya ve altın çıktı eklenir; README ve biçim rehberi güncellenir; ekran klavyeyle
kullanılabilir; çevrimdışı ilke bozulmaz; önceki fazların testleri geçer.

### Faz 0 — Temel: proje, ara format, örnek korpus (2 hafta)

Amaç: sonraki her şeyin dayandığı üç temel. Kullanıcıya görünen çıktı: projeler ve örnekler.

| ID | Story | Kabul kriterleri | Ön. | Büy. |
|---|---|---|---|---|
| US-0.1 | Bir **DWH analisti** olarak dokümanlarımı bir proje altında toplamak istiyorum; böylece her seferinde hepsini yeniden yüklemem ve düzeltmelerim kaybolmaz. | Diyelim ki "Kart" projesinde 6 doküman var · Eğer yeni bir Word yüklersem · O zaman model 7 dokümandan yeniden kurulur ve önceki diyagramlar geçmişte kalır | M | L |
| US-0.2 | Bir **analist** olarak aynı adla yüklediğim dosyanın yeni sürüm olmasını istiyorum; böylece hangi diyagramın hangi sürümden üretildiğini bilirim. | Eğer `STTM.xlsx`'i yeniden yüklersem · O zaman sürüm 2 olur, sürüm 1 arşivde kalır, çalıştırma raporu sürüm numarasını yazar | S | M |
| US-0.3 | Bir **geliştirici** olarak her okuyucunun ve LLM'in DAF üretmesini istiyorum; böylece birleştirme, onay ve kanıt gösterimi tek yoldan geçer. | Mevcut 404 test geçer · her okuyucu çıktısı `dwhflow.extract/2` şemasına uyar · okuma sonucu dokümanın özetine (SHA-256) göre önbelleğe alınır | M | L |
| US-0.4 | Bir **yeni kullanıcı** olarak her desteklenen biçimde hazır örnek dosya istiyorum; böylece kendi dokümanımı hazırlamadan aracı denerim ve biçimleri öğrenirim. | Örnekler ekranında her biçim için ≥1 dosya · "bununla dene" tek tıkla örnek proje oluşturur · her örneğin altın çıktı testi var | M | M |
| US-0.5 | Bir **analist** olarak boş STTM, job bağımlılık ve tasarım dokümanı şablonları istiyorum; böylece yeni dokümanlar baştan okunabilir yazılır. | Şablonla doldurulan doküman %100 kural tabanlı okunur (LLM'siz) · şablonlar Örnekler ekranından indirilir | M | S |
| US-0.6 | Bir **analist** olarak hangi başlık ve cümle kalıplarının tanındığını gösteren bir rehber istiyorum; böylece "neden okunmadı" sorusunu kendim çözerim. | Rehber ayarlardaki eş anlamlılardan **otomatik** üretilir (ayar değişince güncellenir) · ekranda ve Markdown olarak | S | S |

### Faz 1 — Uygulama kabuğu ve inceleme ekranları (3 hafta)

Amaç: modeli ve okuma sonuçlarını **görmek** (salt okunur). Teknik karar §6.

| ID | Story | Kabul kriterleri | Ön. | Büy. |
|---|---|---|---|---|
| US-1.1 | Bir **kullanıcı** olarak sol menülü, adres çubuğuyla gezilebilen bir uygulama istiyorum; böylece ekranlar arasında kaybolmam ve bağlantı paylaşabilirim. | Her ekranın kendi adresi var (`#/proje/kart/tablolar/stg_musteri`) · tarayıcı geri tuşu çalışır · 1280 px ve üstünde düzgün | M | M |
| US-1.2 | Bir **analist** olarak proje panosu istiyorum; böylece projenin durumunu tek bakışta görürüm. | Doküman, tablo, süreç, bağ, bekleyen onay, hata, uyarı, okunamayan bölüm sayıları · her sayı ilgili ekrana filtreli gider | M | M |
| US-1.3 | Bir **analist** olarak dokümanlar ekranında her dokümanın okuma raporunu görmek istiyorum; böylece neyin okunup neyin okunmadığını bilirim. | Biçim, okuyucu, durum, tablo/süreç/akış sayısı, notlar · "yeniden oku" · "şu okuyucuyla oku" (ör. `.txt`'yi JIL olarak) · klasör ve zip yükleme | M | M |
| US-1.4 | Bir **analist** olarak belgeyi ve çıkarılanları yan yana görmek istiyorum; böylece her bağın hangi cümleden geldiğini doğrularım. | Diyelim ki Word dokümanı açık · Eğer sağdaki bir akışa tıklarsam · O zaman soldaki kanıt cümlesi vurgulanıp görünür alana kayar · okunamayan paragraflar sarı | M | L |
| US-1.5 | Bir **analist** olarak tüm tablo ve dosyaları filtrelenebilir bir listede görmek istiyorum; böylece büyük modelde aradığımı bulurum. | Ad, tür, katman, sistem, durum, kaynak sayısı sütunları · 5.000 satırda akıcı (sanal liste) · detay: kolonlar, yukarı/aşağı akış, kaynak dokümanlar, bulgular | M | M |
| US-1.6 | Bir **batch operasyon** üyesi olarak süreçler ekranı istiyorum; böylece job'ların sırasını, zamanını ve dokunduğu tabloları görürüm. | Araç, yükleme stratejisi, zamanlama, öncül/ardıl, okuduğu/yazdığı · "bu job düşerse" etki listesi (mevcut `impact`) | M | M |
| US-1.7 | Bir **yönetişim** sorumlusu olarak bulgular ekranı istiyorum; böylece modeldeki tutarsızlıkları önceliğe göre ele alırım. | Kural, önem, özne, mesaj; filtre ve sıralama · özneye tek tıkla git | M | S |
| US-1.8 | Bir **analist** olarak diyagramlar ekranında plan ve sonuçları görmek istiyorum; böylece hangi diyagramın neden üretilmediğini anlarım. | Plan (genel, soy ağaçları, yollar, batch) · durum rozetleri, kapı ayrıntıları · önizleme · ada göre arayarak yeni A→B yolu ya da çevre diyagramı ekleme | M | M |
| US-1.9 | Bir **kullanıcı** olarak her ekrandan hızlı arama istiyorum; böylece tabloya ya da job'a iki tuşla ulaşırım. | Ctrl+K · tablo, süreç, doküman, ayar araması · şemasız ad da bulur | S | S |

### Faz 2 — Ayarlar (2 hafta)

Amaç: koddaki kuralları **arayüzden** değiştirilebilir kılmak (§3.3).

| ID | Story | Kabul kriterleri | Ön. | Büy. |
|---|---|---|---|---|
| US-2.1 | Bir **kullanıcı** olarak ayarların varsayılan/kullanıcı/proje düzeyinde tutulmasını ve etkin değerin nereden geldiğini görmek istiyorum; böylece bir projeye özel kural diğerlerini bozmaz. | Her ayarın yanında kaynak etiketi · "varsayılana dön" · YAML dosyası elle düzenlense de ekran doğru gösterir | M | M |
| US-2.2 | Bir **analist** olarak kurumumuzun STTM başlıklarını ("Hdf Tbl", "Kaynak Nesne") eş anlamlı olarak tanıtmak istiyorum; böylece o Excel'ler LLM'siz okunur. | Diyelim ki "Hdf Tbl" tanınmıyor · Eğer onu hedef tablo rolüne eklersem · O zaman kaydetmeden önce "2 dokümanda 38 yeni satır tanınacak" önizlemesi görünür ve kaydedince model güncellenir | M | M |
| US-2.3 | Bir **analist** olarak ad → katman/tür kurallarını düzenlemek istiyorum; böylece kurumumuzun adlandırma standardı (ör. `TMP_`, `_HIST`) doğru katmana düşer. | Sıralı kural listesi (önek, sonek, düzenli ifade) · sürükleyerek sırala · test kutusu: ad yaz → katman + tür + hangi kuralın eşleştiği | M | M |
| US-2.4 | Bir **analist** olarak kaynak sistem sözlüğü istiyorum; böylece "CB", "Core Banking" ve "Ana Bankacılık" tek sistem sayılır. | Sistem + eş anlamlılar · okuma sırasında eşleştirme · sistem başına tablo sayısı | S | S |
| US-2.5 | Bir **analist** olarak diyagram ayarlarını (bütçe, otomatik türler, kalite) değiştirmek istiyorum; böylece çok büyük ya da çok küçük diyagramlardan kaçınırım. | Tablo bütçesi 8–40 aralığında · otomatik diyagram türleri tek tek aç/kapa · değişiklik bir sonraki üretimde geçerli | S | S |
| US-2.6 | Bir **kullanıcı** olarak birden fazla LLM bağlantı profili istiyorum; böylece yerel Ollama ile kurum içi sunucu arasında geçiş yaparım. | Profil: ad, adres, model, anahtarın ortam değişkeni adı, zaman aşımı · her profile "bağlantıyı test et" · dış adres yine açık izin ister | S | M |
| US-2.7 | Bir **sistem yöneticisi** olarak motor ve sistem durumunu ekranda görmek istiyorum; böylece kurulum sorununu komut satırına inmeden çözerim. | `doctor` çıktısı ekranda · Node/Chrome yolu elle verilebilir · disk kullanımı ve eski çalıştırmaları temizleme | S | S |
| US-2.8 | Bir **sistem yöneticisi** olarak ayar profillerini dışa/içe aktarmak istiyorum; böylece ekip aynı kurallarla çalışır. | Tek YAML indir/yükle · yüklemeden önce fark önizlemesi · parola/anahtar hiçbir zaman dosyaya girmez | S | S |

### Faz 3 — Elle müdahale (3 hafta)

Amaç: §3.4'teki düzeltme katmanı ve onay kuyruğu. **MVP'nin son fazı.**

| ID | Story | Kabul kriterleri | Ön. | Büy. |
|---|---|---|---|---|
| US-3.1 | Bir **analist** olarak elle yaptığım her değişikliğin yeniden okumadan sonra da kalmasını istiyorum; böylece aynı düzeltmeyi tekrar tekrar yapmam. | Diyelim ki STG_MUSTERI'nin katmanını ODS yaptım · Eğer tüm dokümanları yeniden okursam · O zaman katman ODS kalır ve kaynağında "elle: kullanıcı, tarih" görünür | M | L |
| US-3.2 | Bir **analist** olarak bir nesnenin adını, türünü, katmanını, sistemini, açıklamasını ve hassasiyetini düzenlemek istiyorum; böylece yanlış okunan bilgiyi düzeltirim. | Detay panelinde düzenle · neden alanı isteğe bağlı · değişiklik diyagramlara bir sonraki üretimde yansır | M | M |
| US-3.3 | Bir **analist** olarak aynı tablonun farklı yazımlarını birleştirmek istiyorum; böylece akış kopmaz. | Benzer ad önerileri listesi (ör. `MUSTERI_TBL` ~ `STG_MUSTERI`) · birleştirince kaynaklar ve bağlar tek nesnede · **ayır** ile geri alınabilir | M | M |
| US-3.4 | Bir **analist** olarak bağ eklemek, silmek ve yönünü çevirmek istiyorum; böylece dokümanda eksik ya da ters yazılmış akışı düzeltirim. | "Yukarı/aşağı akışı düzenle" penceresi: ada göre ara, seç, süreç ata, kaydet · elle bağlar diyagramda ayırt edilebilir | M | M |
| US-3.5 | Bir **analist** olarak dokümanda olmayan bir sistemi, dosyayı ya da raporu elle eklemek istiyorum; böylece zincirin eksik halkasını tamamlarım. | Ad, tür, katman, sistem · elle nesne onaylı sayılır ve "elle" etiketi taşır | M | S |
| US-3.6 | Bir **analist** olarak taslakları, LLM önerilerini ve birleştirme önerilerini tek bir onay kuyruğunda görmek istiyorum; böylece modele ne girdiğini ben belirlerim. | Her öğe kanıtıyla · kabul / reddet / düzenleyerek kabul · toplu seçim · reddedilen öğe tekrar okumada yeniden sorulmaz | M | L |
| US-3.7 | Bir **analist** olarak onaylamadan önce etkisini görmek istiyorum; böylece yanlış bir onay diyagramı bozmaz. | Seçili öğeler için fark önizlemesi: eklenecek/silinecek bağlar, değişen katmanlar · isteğe bağlı küçük diyagram önizlemesi | S | M |
| US-3.8 | Bir **yönetişim** sorumlusu olarak her elle değişikliğin günlüğünü istiyorum; böylece denetimde kim neyi neden değiştirdi gösterebilirim. | Günlük ekranı: işlem, hedef, eski → yeni, neden, kim, ne zaman · filtre · dışa aktar | M | S |
| US-3.9 | Bir **analist** olarak geri al/yinele istiyorum; böylece yanlış bir düzenlemeden korkmam. | Ctrl+Z / Ctrl+Y · günlükten tek işlemi geri alma | M | S |
| US-3.10 | Bir **analist** olarak hedefini kaybetmiş düzeltmeleri görmek istiyorum; böylece doküman değişince neyin boşa düştüğünü bilirim. | Sahipsiz düzeltmeler panoda sayılır · yeniden bağla ya da sil | S | S |
| US-3.11 | Bir **yönetişim** sorumlusu olarak bir bulguyu gerekçeyle yok saymak istiyorum; böylece bilinen istisnalar listeyi kirletmez. | Neden zorunlu · diyagram kartında görünmez ama raporda "yok sayıldı" diye durur | S | S |
| US-3.12 | Bir **analist** olarak bir diyagramda nesne gizlemek, sabitlemek ve başlığı değiştirmek istiyorum; böylece sunuma uygun diyagram elde ederim. | Diyagram başına ayar · model değişmez, yalnız o diyagram | S | M |
| US-3.13 | Bir **ETL geliştiricisi** olarak kolon eşlemelerini STTM ekranında satır satır düzenlemek istiyorum; böylece dönüşüm kurallarını tek yerde tutarım. | Kaynak tablo.kolon → hedef tablo.kolon + dönüşüm · satır ekle/sil · değişiklikler düzeltme olarak saklanır | C | L |

### Faz 4 — Evrensel okuma I: ofis, e-posta, arşiv, diyagram, kurum biçimi (3 hafta)

| ID | Story | Kabul kriterleri | Ön. | Büy. |
|---|---|---|---|---|
| US-4.1 | Bir **analist** olarak eski `.doc` ve `.xls` dosyalarını okutmak istiyorum; böylece arşivdeki eski dokümanları dönüştürmek zorunda kalmam. | `.xls` tablo olarak okunur · `.doc` için LibreOffice varsa tam, yoksa düz metin + "biçim kaybı olabilir" notu | M | M |
| US-4.2 | Bir **analist** olarak sunum, HTML (Confluence dışa aktarımı), ODF ve RTF dosyalarını okutmak istiyorum; böylece bilgi hangi biçimdeyse kullanılır. | `.pptx` slayt metni + tablolar + notlar · HTML tabloları ve başlıkları konum olarak · her biri için örnek ve altın test | M | M |
| US-4.3 | Bir **analist** olarak e-postaları (`.eml`, `.msg`) ve eklerini okutmak istiyorum; böylece "şu tablo da şuradan besleniyor" yazışmaları kaybolmaz. | Gövde metin kurallarından geçer · ekler zincire girer · kaynakta "e-posta: konu, tarih" | S | M |
| US-4.4 | Bir **analist** olarak bir klasörü ya da zip'i tek seferde yüklemek istiyorum; böylece yüzlerce dosyayı tek tek seçmem. | İç içe zip · boyut, dosya sayısı ve derinlik sınırı · desteklenmeyenler raporda listelenir | M | S |
| US-4.5 | Bir **analist** olarak Visio (`.vsdx`) ve draw.io akış çizimlerini okutmak istiyorum; böylece elle çizilmiş mimari şemalar modele girer. | Şekil metni → nesne, bağlayıcı yönü → akış · hangi şeklin tablo/job olduğu ayarlanabilir · örnek `.vsdx` ve `.drawio` | M | L |
| US-4.6 | Bir **ETL geliştiricisi** olarak Mermaid, Graphviz, PlantUML ve BPMN dosyalarını okutmak istiyorum; böylece "kod olarak diyagram"lar da kaynak olur. | Kutu-ok → nesne-akış · katman ad kurallarından | C | M |
| US-4.7 | Bir **sistem yöneticisi** olarak kuruma özel XML/JSON/CSV biçimi için ekrandan okuma şablonu tanımlamak istiyorum; böylece geliştirici beklemeden yeni biçim okutulur. | XPath / JSONPath / sütun → kaynak, hedef, job alanları · örnek dosyayla canlı test · şablon ayar profiline girer | S | L |
| US-4.8 | Bir **analist** olarak özel okuyucusu olmayan bir biçimin de en azından metin olarak okunmasını istiyorum; böylece hiçbir doküman sessizce atlanmaz. | İsteğe bağlı `[evrensel]` eki (MarkItDown) → metin kuralları → (LLM) · ek yoksa "şu eki kurun" notu | M | M |
| US-4.9 | Bir **analist** olarak dosyanın uzantısı yanlış olsa da doğru okuyucunun seçilmesini istiyorum; böylece `.txt` uzantılı JIL de okunur. | İçerik tespiti (sihirli bayt, XML kökü, anahtar kelime) · raporda "içerikten tespit edildi" | S | S |

### Faz 5 — Evrensel okuma II: ETL, scheduler, mainframe, betik (4 hafta)

Sıra, kurumda gerçekten kullanılan araçlara göre belirlenir (§10 soru 1). Önerilen sıra: ODI → Informatica → SSIS → Control-M → diğerleri.

| ID | Story | Kabul kriterleri | Ön. | Büy. |
|---|---|---|---|---|
| US-5.1 | Bir **ETL geliştiricisi** olarak ODI dışa aktarımlarını okutmak istiyorum; böylece mapping'lerin kaynak/hedefi elle STTM yazmadan modele girer. | Smart Export XML: mapping, kaynak/hedef datastore, filtre/join ifadeleri, paket/senaryo adım sırası → `precedes` | M | L |
| US-5.2 | Bir **ETL geliştiricisi** olarak Informatica PowerCenter XML'ini okutmak istiyorum. | SOURCE, TARGET, MAPPING, CONNECTOR, Source Qualifier SQL (sqlglot), SESSION, WORKFLOW bağlantı sırası · kolon eşlemeleri | M | L |
| US-5.3 | Bir **ETL geliştiricisi** olarak SSIS `.dtsx` paketlerini okutmak istiyorum. | Bağlantı yöneticileri, Data Flow kaynak/hedef bileşenleri, SQL komutları, Control Flow öncelik kısıtları | S | L |
| US-5.4 | Bir **ETL geliştiricisi** olarak DataStage, Talend ve Pentaho exportlarını okutmak istiyorum. | `.dsx/.isx`, `.item`, `.ktr/.kjb` için stage/step → süreç, bağlantı → akış | C | L |
| US-5.5 | Bir **batch operasyon** üyesi olarak Control-M, AutoSys (box, file watcher), TWS ve cron tanımlarını okutmak istiyorum; böylece gece zincirinin tamamı tek diyagramda görünür. | INCOND/OUTCOND, `condition`, box üyeliği → `precedes` · başlama saatleri · takvimler | M | M |
| US-5.6 | Bir **ETL geliştiricisi** olarak Airflow DAG'larını ve dbt `manifest.json`'u okutmak istiyorum. | Airflow: Python **AST ile, çalıştırmadan**; `>>` bağımlılıkları, operatör SQL'i · dbt: `ref`/`source` grafiği | S | M |
| US-5.7 | Bir **analist** olarak JCL ve COBOL copybook okutmak istiyorum; böylece mainframe'den gelen dosyaların yapısı ve üretildiği adım görünür. | JCL: adım sırası, `DD DSN` → dosya · copybook: alan düzeni → kolonlar (PIC, COMP-3) → dosya nesnesine bağlanır | S | M |
| US-5.8 | Bir **ETL geliştiricisi** olarak shell/TPT betiklerini ve PL/SQL, T-SQL prosedürlerini okutmak istiyorum; böylece prosedür çağrı zinciri görünür. | bteq/tpt/fastload/sqlplus/sftp çağrıları → süreç + dosya · prosedür çağrı grafı · her ifade ayrı süreç (bugünkü SQL davranışı) | S | M |
| US-5.9 | Bir **geliştirici** olarak yeni okuyucuyu tek Python dosyasıyla eklemek istiyorum; böylece kuruma özel biçimler çekirdeği değiştirmeden eklenir. | Eklenti arayüzü (giriş noktası) · Ayarlar'da yüklü okuyucular listesi · örnek eklenti | S | M |

### Faz 6 — LLM v2 (3 hafta)

| ID | Story | Kabul kriterleri | Ön. | Büy. |
|---|---|---|---|---|
| US-6.1 | Bir **analist** olarak LLM'in akışın yanında tabloları, süreçleri, kolon eşlemelerini ve bağımlılıkları da çıkarmasını istiyorum; böylece serbest metinden daha zengin model elde ederim. | DAF `extract/2` (§3.6) · v1 çıktıları geçerliliğini korur · her öğe kanıtlı, taslak | M | L |
| US-6.2 | Bir **analist** olarak tanınmayan tablo başlıklarını LLM'in eşlemesini istiyorum; böylece kurum özel STTM'leri kural yazmadan okunur. | `table-roles/1` · onaylanan eşleme okuma kurallarına eklenir, aynı başlık bir daha LLM'e gitmez | M | M |
| US-6.3 | Bir **analist** olarak istemleri ekrandan görüp yeni sürüm oluşturmak istiyorum; böylece kendi dokümanlarıma uyarlarım. | İstem kütüphanesi: sistem metni, few-shot örnekler · düzenleme yeni sürüm açar · her çıktı hangi istem sürümünden geldiğini taşır | S | M |
| US-6.4 | Bir **analist** olarak bir paragrafı yapıştırıp LLM sonucunu anında görmek istiyorum; böylece istemi denerken tüm projeyi yeniden okutmam. | Test konsolu: ham yanıt, doğrulama sonucu (kabul/ret nedeni), DAF görünümü, süre | S | S |
| US-6.5 | Bir **geliştirici** olarak model ve istem kalitesini ölçmek istiyorum; böylece "qwen3:8b yeterli mi" sorusunu veriyle cevaplarım. | Örnek korpus üzerinde kesinlik/duyarlılık · model × istem karşılaştırma tablosu · sonuçlar dosyaya | S | M |
| US-6.6 | Bir **analist** olarak uzun dokümanların parçalanarak okunmasını istiyorum; böylece 200 sayfalık doküman zaman aşımına uğramaz. | Başlık yolu bağlamıyla parçalama · paralel · ilerleme ve iptal · tekrar eden öğeler birleşir | M | M |
| US-6.7 | Bir **analist** olarak taranmış PDF ve ekran görüntülerini okutmak istiyorum. | Tesseract (Türkçe dil paketi) varsa OCR · yoksa görsel-dil modeli profili · kanıt = OCR metni, "görselden" etiketi | C | L |
| US-6.8 | Bir **yönetişim** sorumlusu olarak LLM'e gidecek metni görmek ve maskelemek istiyorum; böylece kişisel veri dışarı çıkmaz. | Gönderim önizlemesi · maskeleme kuralları (TCKN, IBAN, kart no deseni) · dış adreste maskeleme zorunlu | M | M |

### Faz 7 — Canlı metadata ve karşılaştırma (3 hafta)

| ID | Story | Kabul kriterleri | Ön. | Büy. |
|---|---|---|---|---|
| US-7.1 | Bir **sistem yöneticisi** olarak salt okunur ODBC bağlantısı tanımlamak istiyorum. | DSN ya da bağlantı dizesi · parola saklanmaz (ortam değişkeni ya da oturum başına sorulur) · bağlantı testi | M | M |
| US-7.2 | Bir **yönetişim** sorumlusu olarak dokümandaki tabloların veritabanında gerçekten var olup olmadığını görmek istiyorum. | Şema okuma · "dokümanda var, veritabanında yok" ve tersi bulgular · kolon farkları | M | M |
| US-7.3 | Bir **batch operasyon** üyesi olarak job log/kontrol tablolarından çalışma sürelerini görmek istiyorum. | Son N çalışma, ortalama süre, son başarılı · süreç detayına · SLA kuralına gerçek süreler | S | M |
| US-7.4 | Bir **analist** olarak iki çalıştırma arasındaki farkı görmek istiyorum; böylece doküman güncellemesinin neyi değiştirdiğini bilirim. | Eklenen/silinen tablo ve bağlar listesi · renkli fark diyagramı (Archify architecture delta) | S | M |

### Faz 8 — Dışa aktarım ve paylaşım (2 hafta)

| ID | Story | Kabul kriterleri | Ön. | Büy. |
|---|---|---|---|---|
| US-8.1 | Bir **analist** olarak düzeltilmiş modeli STTM Excel olarak dışa aktarmak istiyorum; böylece "temizlenmiş tek gerçek" ekiple paylaşılır. | Şablonla aynı sütunlar · elle ve taslak öğeler ayrı sütunda işaretli · tekrar okutulunca aynı model çıkar (gidiş-dönüş testi) | M | M |
| US-8.2 | Bir **yönetişim** sorumlusu olarak modeli OpenLineage ve Atlas/Purview uyumlu JSON olarak dışa aktarmak istiyorum; böylece kurumsal kataloğa aktarılır. | Şemaya uygun çıktı · kolon soyu dahil | S | M |
| US-8.3 | Bir **analist** olarak rapor paketini (galeri, diyagramlar, model, PDF özet) tek zip olarak almak istiyorum. | Çevrimdışı açılır · e-postayla gönderilebilir boyut uyarısı | S | S |
| US-8.4 | Bir **ETL geliştiricisi** olarak diyagramı Mermaid ya da draw.io olarak dışa aktarmak istiyorum; böylece başka araçlarda düzenlerim. | Katmanlar ve etiketler korunur | C | S |
| US-8.5 | Bir **sistem yöneticisi** olarak projeyi yedekleyip başka bilgisayarda geri yüklemek istiyorum. | Tek dosya · dokümanlar, sürümler, ayarlar, düzeltmeler, çalıştırmalar · sürüm uyumluluk denetimi | M | S |

---

## 6. Teknik kararlar (öneri)

| Konu | Öneri | Gerekçe | Alternatif |
|---|---|---|---|
| Arayüz | **Pakete gömülü Preact + htm** (derleme adımı yok, ~15 KB, MIT), ES modülleri, hash yönlendirici | 14 ekranı ve durum yönetimini düz JS ile sürdürmek zor; npm/derleme istemeden bileşen yapısı; CDN yok | Düz JS modülleri (bugünkü) · React + Vite (derleme adımı ister) |
| Bağ düzenleme | Önce **tablo/pencere tabanlı** (ada göre ara-seç); görsel sürükle-bağla sonra | Güvenilir, erişilebilir, klavyeyle çalışır; Archify görüntüleyicisi salt okunur | Cytoscape.js (MIT) ile sürükle-bağla (Faz 3 "olabilir") |
| Kalıcılık | Proje klasöründe YAML (model, ayarlar, düzeltmeler) + JSONL günlük + okuma önbelleği | Git'e konabilir, elle okunabilir, taşınabilir | SQLite (arama hızı gerekirse Faz 7'de) |
| API | Ekran başına REST uçları; değişiklik yapan her istek `X-DWHFlow` başlığı ve `Host` denetimi (bugünkü güvenlik) | Tutarlılık | — |
| Ek paketler | `[ofis]`, `[evrensel]`, `[ocr]`, `[db]` ekleri; `paketle.py` seçilen ekleri çevrimdışı pakete alır | Varsayılan kurulum hafif ve taşınabilir kalır | Hepsi varsayılan (kurulum büyür, Tesseract/LibreOffice gibi sistem bağımlılıkları kırılır) |
| XML güvenliği | `defusedxml` | ETL exportları güvenilmeyen girdi | — |

---

## 7. Yol haritası

| Faz | Kapsam | Süre | Bağımlılık | Birikimli |
|---|---|---|---|---|
| 0 | Proje, DAF, örnek korpus, şablonlar | 2 hf | — | 2 hf |
| 1 | Uygulama kabuğu + inceleme ekranları | 3 hf | 0 | 5 hf |
| 2 | Ayarlar | 2 hf | 1 | 7 hf |
| 3 | Elle müdahale, onay kuyruğu, günlük | 3 hf | 1 (2 önerilir) | **10 hf — MVP** |
| 4 | Evrensel okuma I | 3 hf | 0 (1'den bağımsız yürüyebilir) | 13 hf |
| 5 | Evrensel okuma II (ETL, scheduler, mainframe) | 4 hf | 0, 4 | 17 hf |
| 6 | LLM v2 | 3 hf | 0, 3 (onay kuyruğu) | 20 hf |
| 7 | Canlı metadata + karşılaştırma | 3 hf | 1 | 23 hf |
| 8 | Dışa aktarım ve paylaşım | 2 hf | 3 | 25 hf |

İki geliştiriciyle Faz 4–5 (okuyucular) ekranlardan bağımsız paralel yürüyebilir: toplam ~15–16 hafta.
DAF sözleşmesi Faz 0'da sabitlendiği için okuyucu ve ekran işleri birbirini beklemez.

---

## 8. Başarı ölçütleri

| Ölçüt | Hedef |
|---|---|
| Sessiz atlama | 0 — her dosya "okundu" ya da "okunamadı + neden" |
| Biçim kapsamı | Korpustaki biçimlerin tamamı; 14 → ~45 uzantı |
| Kural tabanlı akış kesinliği / duyarlılığı (altın set) | ≥ %95 / ≥ %80 |
| LLM eklenince | duyarlılık +10 puan, kesinlik ≥ %90 |
| Elle düzeltmelerin yeniden okumada korunması | %100 (sahipsizler listelenir) |
| İlk diyagrama süre (yeni kullanıcı, örneklerle) | < 10 dakika, kurulum dahil |
| Büyük model | 5.000 tablo, 20.000 bağ: listeler < 1 sn, okuma önbellekle < 1 dk |

---

## 9. Riskler

| Risk | Etki | Önlem |
|---|---|---|
| Kapsam (25 hafta) | Gecikme | MVP kesimi (Faz 0–3); her faz kendi başına kullanılabilir çıktı verir |
| Tescilli biçimlerin sürüm çeşitliliği (PowerCenter, ODI export sürümleri) | Okuyucu gerçek dosyada kırılır | Kurumdan **anonimleştirilmiş gerçek örnekler**; bilinmeyen elemanlar yok sayılmaz, raporlanır |
| 8B modelin karmaşık JSON'da hata yapması | Düşük LLM verimi | Görevleri küçük tut (tablo rolleri ayrı), JSON modu, tek yeniden deneme, ölçüm (US-6.5) |
| Türkçe OCR kalitesi | Taranmış belgede yanlış ad | Tesseract Türkçe paketi; OCR'dan gelen her öğe taslak |
| Düzeltme–doküman çakışması | Kafa karışıklığı | Düzeltme kazanır + bulgu üretir; sahipsiz düzeltme ekranı |
| Arayüz karmaşıklığı | Bakım zorluğu | Preact + htm kararı; ekran başına bileşen ve API testleri |
| Güvenilmeyen girdiler (XXE, zip bombası, makro, istem enjeksiyonu) | Güvenlik | defusedxml, sınırlar, hiçbir şeyi çalıştırmama, "belge veridir" kuralı + korpusta saldırı örnekleri |
| Ek paketlerin taşınabilirliği | Kapalı ağda kurulum sorunu | Ekler isteğe bağlı; `paketle.py` ekleri dahil eder; `doctor` eksik eki söyler |

---

## 10. Karar gerektiren konular

1. **Kurumda hangi ETL/scheduler araçları kullanılıyor?** (ODI, Informatica, SSIS, DataStage, Control-M, AutoSys, TWS…) — Faz 5'in sırası buna göre belirlenir.
2. **Tek kullanıcı mı, ekip mi?** Plan tek kullanıcılı yerel uygulama varsayıyor. Ortak sunucu gerekiyorsa kimlik doğrulama ve yetki ayrı bir faz olur.
3. **Sistem bağımlılıkları kurulabilir mi?** Tesseract (OCR) ve LibreOffice (`.doc`) kurum politikasına takılabilir.
4. **Arayüz teknolojisi:** gömülü Preact + htm (önerim) mi, düz JS mi?
5. **MVP kesimi:** Önce Faz 0–3 (ekranlar + ayarlar + elle müdahale) mi, yoksa okuma kapsamı (Faz 4–5) öncelikli mi?
6. **Gerçek örnek:** Anonimleştirilmiş gerçek STTM/ODI/Control-M dosyaları korpusa eklenebilir mi?

---

## 11. Kaynaklar

- IBM Manta: https://www.ibm.com/docs/en/manta-data-lineage?topic=storage-teradata · https://datastackindex.com/data-observability/tools/ibm-manta/
- Cloudera Data Lineage (Octopai): https://docs.cloudera.com/octopai/latest/howto/topics/oct-cross-system-lineage.html · https://www.octopai.com/data-lineage-xd/
- Solidatus: https://www.solidatus.com/product/ai-lineage-assistant/ · https://www.solidatus.com/product/data-lineage/
- erwin Data Intelligence: https://bookshelf.erwin.com/bookshelf/13.0DIBookshelf/Content/Getting%20Started/Using%20DI/Running%20Lineage%20Analysis.html · https://www.sandhillconsultants.com/products/mapping-manager/
- Gudu SQLFlow: https://www.gudusoft.com/ · https://docs.gudusoft.com/1.-introduction/readme
- Dataedo: https://docs.dataedo.com/docs/data-lineage/manual-lineage/portal/ · https://docs.dataedo.com/data-sources/other/open-lineage/
- Microsoft Purview: https://learn.microsoft.com/en-us/purview/legacy/how-to-purview-custom-lineage-api-user-guide
- OpenMetadata: https://docs.open-metadata.org/v2.0.x/how-to-guides/data-lineage
- DataHub: https://docs.datahub.com/docs/features/feature-guides/ui-lineage
- GitHub depoları (yıldız sayıları 29 Eylül 2026): §2.2 ve §2.3'teki bağlantılar
