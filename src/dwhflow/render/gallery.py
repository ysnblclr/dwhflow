"""Galeri sayfası: bir dönüştürmenin sonuçlarını gösteren tek dosyalık HTML.

Dış kaynak yok (CDN, font, betik) — çevrimdışı açılır, e-postayla gönderilebilir.
Diyagram, doküman ve doğrulama durumlarını olduğu gibi gösterir: başarısız ya da
tarayıcı kontrolü atlanmış bir diyagram "hazır" diye gösterilmez.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any


def _when(stamp: str | None) -> str:
    """ISO zaman damgası → ``28.09.2026 22:23``."""
    try:
        return datetime.fromisoformat(stamp).strftime("%d.%m.%Y %H:%M") if stamp else ""
    except ValueError:
        return stamp or ""


_STATUS = {
    "pass": ("ok", "Tüm kapılar geçti"),
    "skipped": ("warn", "Tarayıcı kontrolü atlandı"),
    "fail": ("bad", "Üretilemedi"),
    "error": ("bad", "Motor hatası"),
}
_DOC_STATUS = {
    "ok": ("ok", "okundu"),
    "empty": ("warn", "akış bulunamadı"),
    "unsupported": ("warn", "desteklenmiyor"),
    "error": ("bad", "hata"),
}
_SEVERITY = {"error": "bad", "warning": "warn", "info": "muted"}

_CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2330;--muted:#5f6b7c;--line:#dde2ea;--ok:#1f8a4c;
--warn:#b7791f;--bad:#c53030;--accent:#2563eb}
@media (prefers-color-scheme:dark){:root{--bg:#0f1218;--card:#171b23;--ink:#e3e7ee;--muted:#8b95a7;
--line:#2a303c;--ok:#48bb78;--warn:#ecc94b;--bad:#fc8181;--accent:#63a4ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 "Segoe UI",system-ui,-apple-system,sans-serif}
main{max-width:1080px;margin:0 auto;padding:28px 18px 60px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:15px;margin:30px 0 10px;text-transform:uppercase;
letter-spacing:.5px;color:var(--muted)}
.sub{color:var(--muted);margin:0 0 18px}
.stats{display:flex;flex-wrap:wrap;gap:10px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;min-width:120px}
.stat b{display:block;font-size:20px}.stat span{color:var(--muted);font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.card h3{margin:0 0 4px;font-size:15px}.card p{margin:4px 0;color:var(--muted)}
.card a.open{display:inline-block;margin-top:8px;padding:6px 12px;border-radius:7px;
background:var(--accent);color:#fff;text-decoration:none;font-weight:600}
.pill{display:inline-block;font-size:11px;font-weight:700;padding:1px 8px;border-radius:10px;
border:1px solid currentColor;margin-right:6px}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.muted{color:var(--muted)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);
border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top;
overflow-wrap:anywhere}
.scroll{overflow-x:auto;max-width:100%}html,body{overflow-x:hidden}
th{font-size:11px;text-transform:uppercase;letter-spacing:.4px;color:var(--muted)}
td.num{text-align:right;font-variant-numeric:tabular-nums}
ul.notes{margin:4px 0 0;padding-left:18px;color:var(--muted);font-size:12.5px}
footer{margin-top:36px;color:var(--muted);font-size:12px}
.empty{color:var(--muted);font-style:italic}
"""


def _pill(kind: str, text: str) -> str:
    return f'<span class="pill {kind}">{escape(text)}</span>'


def write_gallery(report: dict[str, Any], path: Path) -> None:
    ingest = report.get("ingest", {})
    stats = ingest.get("stats", {})
    summary = report.get("validation", {}).get("summary", {})
    diagrams = report.get("diagrams", [])
    ready = sum(1 for d in diagrams if d.get("html"))

    parts = [f"<h1>{escape(str(report.get('title', 'DWHFlow')))}</h1>",
             f'<p class="sub">{escape(_when(report.get("created_at")))} · '
             f'{len(report.get("inputs", []))} doküman · {report.get("duration_s", "?")} sn</p>']

    parts.append('<div class="stats">' + "".join(
        f'<div class="stat"><b>{value}</b><span>{escape(label)}</span></div>'
        for value, label in [
            (stats.get("datasets", 0), "tablo / dosya"),
            (stats.get("processes", 0), "süreç / job"),
            (stats.get("edges", 0), "ilişki"),
            (f"{ready}/{len(diagrams)}", "diyagram hazır"),
            (summary.get("error", 0), "doğrulama hatası"),
            (summary.get("warning", 0), "uyarı"),
        ]) + "</div>")

    for note in report.get("notes", []):
        parts.append(f'<p class="warn">⚠ {escape(note)}</p>')

    parts.append("<h2>Diyagramlar</h2>")
    if diagrams:
        cards = []
        for item in diagrams:
            kind, label = _STATUS.get(item.get("status"), ("bad", item.get("status", "?")))
            if item.get("status") == "pass" and item.get("quality") != "showcase":
                label += " (standart kalite)"
            body = [f"<h3>{escape(item['title'])}</h3>", f"<p>{_pill(kind, label)}</p>"]
            if item.get("subtitle"):
                body.append(f"<p>{escape(item['subtitle'])}</p>")
            if item.get("html"):
                body.append(f'<a class="open" href="{escape(item["html"])}">Aç</a>')
            else:
                reasons = [d.get("message", "") for a in item.get("attempts", [])
                           for d in a.get("diagnostics", [])][:3]
                if reasons:
                    body.append('<ul class="notes">' + "".join(
                        f"<li>{escape(r[:200])}</li>" for r in reasons) + "</ul>")
            cards.append(f'<div class="card">{"".join(body)}</div>')
        parts.append(f'<div class="grid">{"".join(cards)}</div>')
    else:
        parts.append('<p class="empty">Diyagram yok.</p>')

    parts.append("<h2>Dokümanlar</h2>")
    rows = []
    for doc in ingest.get("documents", []):
        kind, label = _DOC_STATUS.get(doc.get("status"), ("muted", doc.get("status", "?")))
        notes = "".join(f"<li>{escape(n)}</li>" for n in doc.get("notes", []))
        llm = " · YZ kullanıldı" if doc.get("llm_used") else ""
        rows.append(
            f"<tr><td><b>{escape(doc['file'])}</b><br><span class='muted'>{escape(doc['reader'])}{llm}"
            f"</span>{f'<ul class=notes>{notes}</ul>' if notes else ''}</td>"
            f"<td>{_pill(kind, label)}</td><td class=num>{doc.get('datasets', 0)}</td>"
            f"<td class=num>{doc.get('processes', 0)}</td><td class=num>{doc.get('flows', 0)}</td></tr>")
    parts.append("<div class=scroll><table><thead><tr><th>Doküman</th><th>Durum</th><th>Tablo</th>"
                 f"<th>Süreç</th><th>Akış</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>")

    llm = ingest.get("llm")
    if llm:
        parts.append(f'<p class="muted">Yapay zekâ: {llm["calls"]} çağrı, {llm["accepted"]} akış kabul '
                     f'edildi, {llm["rejected"]} öneri kanıt denetiminden geçemediği için reddedildi. '
                     "Yapay zekâ çıkarımları taslaktır; diyagramda kesikli çizgi ve “taslak” etiketiyle görünür.</p>")
    elif ingest.get("llm_candidates"):
        parts.append(f'<p class="muted">{ingest["llm_candidates"]} paragrafta tablo adı geçiyor ama akış '
                     "yönü kuralla çıkarılamadı. Yapay zekâ açılırsa bunlar da işlenebilir.</p>")

    findings = report.get("validation", {}).get("findings", [])
    parts.append("<h2>Doğrulama</h2>")
    if findings:
        order = {"error": 0, "warning": 1, "info": 2}
        rows = [f"<tr><td>{_pill(_SEVERITY.get(f['severity'], 'muted'), f['severity'])}</td>"
                f"<td>{escape(f['rule'])}</td><td>{escape(f.get('subject') or '—')}</td>"
                f"<td>{escape(f['message'])}</td></tr>"
                for f in sorted(findings, key=lambda f: order.get(f["severity"], 3))[:60]]
        parts.append("<div class=scroll><table><thead><tr><th>Şiddet</th><th>Kural</th><th>Konu</th>"
                     f"<th>Mesaj</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>")
    else:
        parts.append('<p class="empty">Bulgu yok.</p>')

    engine = report.get("engine") or {}
    engine_text = f" · {engine.get('name', '').capitalize()} {engine.get('version', '')}" if engine else ""
    parts.append(f"<footer>{escape(report.get('tool', 'DWHFlow'))}{escape(engine_text)} · "
                 "Model: <code>model/</code> · Tam rapor: <code>dwhflow-run.json</code> · "
                 "Bu sayfa ve diyagramlar internet olmadan açılır.</footer>")

    html = ("<!DOCTYPE html><html lang='tr'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{escape(str(report.get('title', 'DWHFlow')))} — DWHFlow</title>"
            f"<style>{_CSS}</style></head><body><main>{''.join(parts)}</main></body></html>")
    path.write_text(html, encoding="utf-8")
