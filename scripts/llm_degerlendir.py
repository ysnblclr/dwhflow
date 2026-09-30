#!/usr/bin/env python3
"""LLM akış çıkarımının kalitesini altın set üzerinde ölçer (US-6.5).

    python scripts/llm_degerlendir.py                                   # yerel Ollama, qwen3:8b, en yeni istem
    python scripts/llm_degerlendir.py --model qwen3:8b --model llama3.1:8b --prompt-version 1 --prompt-version 2
    python scripts/llm_degerlendir.py --url https://api.ornek.com/v1 --allow-external --key-env ORNEK_KEY
    python scripts/llm_degerlendir.py --cikti sonuc.json                # ayrıntılar dosyaya

Her model × istem sürümü için kesinlik, duyarlılık ve F1 tablosu basar. Varsayılan altın
set ``src/dwhflow/llm/eval/kart_paragraflar.jsonl``'dir. Dış (yerel olmayan) adreslere
yalnız ``--allow-external`` ile bağlanılır ve metin **maskelenerek** gönderilir.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _import_dwhflow() -> None:
    """Kurulu değilse depodaki ``src`` klasörünü kullanır."""
    try:
        import dwhflow  # noqa: F401
    except ImportError:
        sys.path.insert(0, str(ROOT / "src"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LLM akış çıkarımını altın set üzerinde ölçer.")
    parser.add_argument("--url", default="http://localhost:11434/v1", help="OpenAI uyumlu uç adresi")
    parser.add_argument("--model", action="append", help="model adı (birden çok verilebilir; varsayılan qwen3:8b)")
    parser.add_argument("--key-env", default="DWHFLOW_AI_KEY", help="API anahtarının ortam değişkeni")
    parser.add_argument("--allow-external", action="store_true",
                        help="yerel olmayan adrese izin ver (metin maskelenerek makineden çıkar)")
    parser.add_argument("--prompt-version", action="append", type=int,
                        help="istem sürümü (birden çok verilebilir; varsayılan en yeni)")
    parser.add_argument("--veri", type=Path, default=None, help="altın set JSONL dosyası")
    parser.add_argument("--istemler", type=Path, default=None, help="kullanıcı istem klasörü (ör. istemler/)")
    parser.add_argument("--zaman-asimi", type=float, default=180.0, help="çağrı başına zaman aşımı (sn)")
    parser.add_argument("--cikti", type=Path, default=None, help="sonuçları (ayrıntılarıyla) JSON olarak yaz")
    return parser


def format_table(results: list[dict[str, Any]]) -> str:
    headers = ["Model", "İstem", "Kesinlik", "Duyarlılık", "F1", "DP", "YP", "YN", "Ret", "Belirsiz", "Hata",
               "Süre (sn)"]
    rows = [[
        str(r["model"]), f"{r['prompt']['id']}/v{r['prompt']['version']}", f"{r['precision']:.3f}",
        f"{r['recall']:.3f}", f"{r['f1']:.3f}", str(r["tp"]), str(r["fp"]), str(r["fn"]), str(r["rejected"]),
        str(r["unresolved"]), str(r["errors"]), f"{r['duration_ms'] / 1000:.1f}",
    ] for r in results]
    widths = [max(len(row[i]) for row in [headers, *rows]) for i in range(len(headers))]

    def line(cells: list[str]) -> str:
        return " | ".join(cell.ljust(width) for cell, width in zip(cells, widths, strict=True))

    return "\n".join([line(headers), "-+-".join("-" * width for width in widths), *map(line, rows)])


def main(argv: list[str] | None = None, *, transport: Any = None) -> int:
    """``transport`` testler içindir (``httpx.MockTransport``)."""
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Türkçe karakter ve "→" her konsolda basılabilsin
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    _import_dwhflow()
    from dwhflow.llm import LLMClient, LLMConfig, LLMError, PromptError, evaluate

    results = []
    for model in args.model or ["qwen3:8b"]:
        for version in args.prompt_version or [None]:
            config = LLMConfig(enabled=True, base_url=args.url, model=model, api_key_env=args.key_env,
                               allow_external_api=args.allow_external, timeout_s=args.zaman_asimi)
            try:
                result = evaluate(LLMClient(config, transport=transport), args.veri, version=version,
                                  user_prompt_dir=args.istemler)
            except (LLMError, PromptError, ValueError, OSError) as exc:
                print(f"hata: {exc}", file=sys.stderr)
                return 2
            results.append(result)
    print(format_table(results))
    for result in results:
        wrong = [d for d in result["details"] if d["fp"] or d["fn"] or d["errors"]]
        for detail in wrong:
            print(f"  {result['model']} v{result['prompt']['version']} · {detail['id']}: "
                  f"yanlış={detail['false_positives']} eksik={detail['missed']}"
                  + (f" hata={detail['errors'][0][:80]}" if detail["errors"] else ""))
    if args.cikti:
        args.cikti.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"sonuçlar: {args.cikti}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
