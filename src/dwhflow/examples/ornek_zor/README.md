# Zor örnekler

Okuyucuların kötü niyetli, bozuk ya da yanıltıcı dosyalarda **güvenli** davrandığını sınayan
örnekler (`tests/test_formats_adversarial.py`). Hiçbiri istisna fırlatmamalı; her biri raporda
doğru durumla ve nedeniyle görünmeli; çalışma klasörü dışına dosya yazılmamalı.

İkili dosyalar `scripts/ornek_bicim_uret.py` ile üretilir.

| Dosya | Ne sınanır | Beklenen |
|---|---|---|
| `talimat_gomulu.txt` | Metinde LLM'e yönelik gömülü talimat ("önceki talimatları yok say…") ve gerçek akış cümleleri | Talimat uygulanmaz, **veri** olarak kalır: kurallar yalnız gerçek iki akışı çıkarır; talimat paragrafı en çok onay kuyruğuna (artan paragraf) düşer |
| `bozuk.docx` | Zip başlığıyla başlayan ama zip olmayan `.docx` | `error`, not: Word dosyası okunamadı |
| `yol_asimi.zip` | `../../kacak.txt` öğesi (zip-slip) | Arşivin tamamı reddedilir (`error`), hiçbir dosya yazılmaz |
| `ic_ice.zip` | 4 düzey iç içe zip | 3 düzeye kadar açılır; 4. düzeydeki arşiv açılmadan `unsupported` raporlanır |
| `bomba_taklidi.zip` | 5 MB sıfırın ~5 KB'a sıkıştığı öğe (oran ≈ 1000) | Açılmadan reddedilir (`error`, zip bombası olabilir) |
| `sifreli.zip` | Şifreli bayraklı öğe | Reddedilir (`error`, şifresiz arşiv isteyin) |
| `ekli_eposta.eml` | Ek (CSV), iletilen e-posta (`message/rfc822`) ve satır içi imza görseli | Gövde, ek ve iletilen e-postanın gövdesi ayrı doküman (`container` = e-posta); görsel atlanır |
| `dis_varlik.bpmn` | XXE (`file:///etc/passwd`) ve varlık genişlemesi (billion laughs) | `defusedxml` reddeder (`error`); dosya okunmaz, bellek şişmez |
| `sahte_uzanti.doc` | `.doc` uzantılı HTML (web dışa aktarımı) | HTML olarak okunur (`ok`), raporda not |
