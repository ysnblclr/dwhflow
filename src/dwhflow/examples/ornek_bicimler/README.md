# Biçim örnekleri

Her desteklenen biçim için en az bir örnek dosya. Örnekler aynı kredi kartı
senaryosundan (`../ornek_dokumanlar/`) adlar kullanır; böylece farklı biçimlerden
okunan bilgiler aynı tablolarda birleşir.

## Altın çıktı sözleşmesi

Her örnek dosyanın yanında `<dosya adı>.beklenen.json` bulunur. `tests/test_samples_golden.py`
dosyayı **tek başına** okur ve sonucu bu dosyayla karşılaştırır. Beklenen dosyası
olmayan örnek testte hata verir.

```json
{
  "aciklama": "neyi sınadığı",
  "format": "jil",
  "datasets": ["STG_A", "ODS_A"],
  "processes": ["J1", "J2"],
  "links": [["STG_A", "ODS_A"]],
  "precedes": [["J1", "J2"]],
  "status": "ok",
  "subset": false
}
```

| Alan | Anlamı |
|---|---|
| `format` | Seçilmesi gereken biçim anahtarı (`registry.FormatSpec.key`); içerik tespitini de sınar |
| `datasets` | Modeldeki veri nesnelerinin adları (`Dataset.name`) |
| `processes` | Süreç/job adları (`Process.name`) |
| `links` | Tablo düzeyi bağlar (`render.slices.dataset_links`), `[kaynak adı, hedef adı]` |
| `precedes` | Job sırası kenarları, `[önceki, sonraki]` |
| `status` | Doküman raporunun durumu (varsayılan `ok`) |
| `subset` | `true` ise listeler "en az bunlar" demektir; `false` ise tam eşitlik aranır |

Adlar büyük/küçük harfe duyarsız karşılaştırılır.
