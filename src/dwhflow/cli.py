"""DWHFlow komut satırı arayüzü.

Ana komutlar: ``convert`` (dokümanları akışa çevir), ``serve`` (tarayıcı arayüzü),
``doctor`` (kurulum denetimi). Model yardımcıları: ``info``, ``validate``, ``show``,
``paths``, ``impact``, ``order``, ``columns``, ``export-dot``, ``roundtrip``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from . import __version__
from .graph import ModelGraph
from .model import COLUMN_SEP, CONTROL_EDGES, DATA_EDGES, EdgeKind, Severity
from .store import StoreError, load_model, save_model
from .validate import summarize, validate

# Türkçe çıktı Windows konsol kod sayfasına (cp1254) yazılırsa dosyaya
# yönlendirildiğinde bozuk görünür. Araç her yerde aynı davransın diye UTF-8'e
# sabitliyoruz; konsol desteklemiyorsa karakter kaybetmek yerine değiştiriyoruz.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # pragma: no cover - exotic stream
        pass

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Veri ambarı dokümanlarını uçtan uca akış diyagramlarına çevirir.",
)

def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"dwhflow {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", "-V", help="Sürümü yazdır ve çık", callback=_version_callback,
                     is_eager=True),
    ] = False,
) -> None:
    """Veri ambarı dokümanlarını uçtan uca akış diyagramlarına çevirir.

    Kurulumu denetle:   dwhflow doctor
    Örneklerle dene:    dwhflow convert --ornek --open
    Tarayıcı arayüzü:   dwhflow serve
    """


ModelPath = Annotated[
    Path | None,
    typer.Argument(
        help="Model dizini ya da YAML dosyası. Verilmezse örnek model kullanılır.",
    ),
]


def _demo_path() -> Path | None:
    """Örnek modeli bulur.

    Öncelik paketin içi: böylece ``pip install dwhflow`` sonrası, depo olmasa da
    ``dwhflow serve`` dolu bir ekranla açılır.
    """
    candidates = [
        Path(__file__).resolve().parent / "examples" / "demo_bank_dwh",
        Path.cwd() / "examples" / "demo_bank_dwh",
    ]
    return next((c for c in candidates if c.exists()), None)


def _ref(model, value: str) -> str:
    """Node kimliği ya da adı → kimlik. Dokümandan üretilen modelde kullanıcı kimliği
    değil tablo/job adını yazar (``DWH.F_KART_ISLEM``). Eşleşme yoksa değer aynen döner
    ve komutun kendi "bulunamadı" mesajı çalışır; birden fazlaysa seçenekler gösterilir."""
    if value in model.nodes:
        return value
    needle = value.strip().casefold()
    matches = sorted(node_id for node_id, node in model.nodes.items()
                     if needle in (node.name.casefold(), node_id.casefold()))
    if not matches:  # şemasız ad: F_KART_ISLEM → DWH.F_KART_ISLEM
        matches = sorted(node_id for node_id, node in model.nodes.items()
                         if node.name.casefold().rsplit(".", 1)[-1] == needle)
    if len(matches) > 1:
        typer.secho(f"'{value}' birden fazla nesneyle eşleşiyor: {', '.join(matches[:8])}",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    return matches[0] if matches else value


def _load(path: Path | None):
    target = path or _demo_path()
    if target is None:
        typer.secho(
            "Model yolu verilmedi ve örnek model bulunamadı. "
            "Bir model dizini belirt: dwhflow info ./model",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    if not target.exists():
        typer.secho(f"Model bulunamadı: {target}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    try:
        return load_model(target)
    except StoreError as exc:
        typer.secho(f"Model yüklenemedi:\n{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc


def _edge_kinds(scope: str) -> set[EdgeKind] | None:
    """``--scope`` seçeneğini kenar tipi kümesine çevirir."""
    if scope == "data":
        return set(DATA_EDGES)
    if scope == "control":
        return set(CONTROL_EDGES)
    if scope == "all":
        return set(DATA_EDGES) | set(CONTROL_EDGES)
    typer.secho(f"Geçersiz --scope: {scope} (data|control|all)", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=2)


@app.command()
def info(path: ModelPath = None) -> None:
    """Modelin özetini yazdırır."""
    model = _load(path)
    stats = model.stats()
    typer.echo(f"Model: {model.name}")
    for key, value in stats.items():
        typer.echo(f"  {key:10} {value}")

    by_layer: dict[str, int] = {}
    for node in model.nodes.values():
        by_layer[node.layer.value] = by_layer.get(node.layer.value, 0) + 1
    typer.echo("\nKatmana göre node sayısı:")
    for layer, count in by_layer.items():
        typer.echo(f"  {layer:12} {count}")

    by_edge: dict[str, int] = {}
    for edge in model.edges:
        by_edge[edge.kind.value] = by_edge.get(edge.kind.value, 0) + 1
    typer.echo("\nKenar tipine göre:")
    for kind, count in sorted(by_edge.items()):
        typer.echo(f"  {kind:16} {count}")


@app.command(name="validate")
def validate_cmd(
    path: ModelPath = None,
    severity: Annotated[
        str, typer.Option(help="En düşük gösterilecek şiddet: error|warning|info")
    ] = "info",
) -> None:
    """Model tutarlılık kurallarını çalıştırır.

    Çıkış kodu: 0 = hata yok, 1 = en az bir ERROR bulundu.
    """
    model = _load(path)
    findings = validate(model)

    threshold = {"error": 0, "warning": 1, "info": 2}.get(severity)
    if threshold is None:
        typer.secho(f"Geçersiz --severity: {severity}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
    shown = [f for f in findings if order[f.severity] <= threshold]

    colors = {
        Severity.ERROR: typer.colors.RED,
        Severity.WARNING: typer.colors.YELLOW,
        Severity.INFO: typer.colors.CYAN,
    }
    for finding in shown:
        typer.secho(str(finding), fg=colors[finding.severity])

    counts = summarize(findings)
    typer.echo(
        f"\n{len(model.nodes)} node, {len(model.edges)} kenar denetlendi — "
        f"{counts['error']} hata, {counts['warning']} uyarı, {counts['info']} bilgi"
    )
    if counts["error"]:
        raise typer.Exit(code=1)


@app.command()
def show(
    node_id: Annotated[str, typer.Argument(help="Node id")],
    path: ModelPath = None,
) -> None:
    """Bir node'un detay kartını yazdırır (kimlik ya da ad)."""
    model = _load(path)
    node_id = _ref(model, node_id)
    node = model.node(node_id)
    if node is None:
        typer.secho(f"Node bulunamadı: {node_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    graph = ModelGraph(model)

    typer.secho(f"{node.name}  ({node_id})", bold=True)
    typer.echo(f"  tip        {node.node_type.value} / {node.kind.value}")
    typer.echo(f"  katman     {node.layer.value}")
    typer.echo(f"  sistem     {node.system or '-'}")
    typer.echo(f"  kritiklik  {node.criticality}")
    typer.echo(f"  durum      {node.status.value}")
    if node.description:
        typer.echo(f"  açıklama   {node.description}")

    if node_id in model.datasets:
        dataset = model.datasets[node_id]
        if dataset.columns:
            typer.echo(f"  kolonlar   {len(dataset.columns)}")
            for column in dataset.columns:
                flags = " PK" if column.pk else ""
                typer.echo(f"    - {column.name:22} {column.data_type or '?':18}{flags}")
        if dataset.file_spec:
            spec = dataset.file_spec.model_dump(exclude_none=True)
            typer.echo(f"  dosya      {spec}")
        typer.echo(f"  yazanlar   {graph.writers(node_id) or '-'}")
        typer.echo(f"  okuyanlar  {graph.readers(node_id) or '-'}")
        typer.echo(f"  kontroller {graph.checks_on(node_id) or '-'}")
    else:
        process = model.processes[node_id]
        typer.echo(f"  araç       {process.tool or '-'}")
        if process.load_strategy:
            typer.echo(f"  yükleme    {process.load_strategy.value}")
        if process.schedule:
            typer.echo(f"  zamanlama  {process.schedule.model_dump(exclude_none=True)}")
        if process.runtime:
            typer.echo(f"  süre       {process.runtime.model_dump(exclude_none=True)}")
        if process.sla:
            typer.echo(f"  SLA        {process.sla.model_dump(exclude_none=True)}")
        typer.echo(f"  restart    {process.restartable}  idempotent: {process.idempotent}")

    for source in node.sources:
        typer.echo(f"  kaynak     {source.extractor} ← {source.file or '?'} {source.locator or ''}")


@app.command()
def paths(
    source: Annotated[str, typer.Option("--from", help="Kaynak (kimlik ya da ad)")],
    target: Annotated[str, typer.Option("--to", help="Hedef (kimlik ya da ad)")],
    path: ModelPath = None,
    limit: Annotated[int, typer.Option(help="Gösterilecek yol sayısı")] = 5,
) -> None:
    """A'dan B'ye veri akışı yollarını listeler (A→B simülasyonunun temeli)."""
    model = _load(path)
    graph = ModelGraph(model)
    source, target = _ref(model, source), _ref(model, target)
    try:
        found = graph.paths(source, target)
    except KeyError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    if not found:
        typer.secho(f"{source} → {target} arasında veri akışı yolu yok.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    typer.echo(f"{len(found)} yol bulundu ({source} → {target}):\n")
    for index, route in enumerate(found[:limit], start=1):
        typer.secho(f"Yol {index}  ({len(route)} adım)", bold=True)
        edges = graph.path_edges(route)
        for step, node_id in enumerate(route):
            node = model.node(node_id)
            arrow = "" if step == 0 else f"  --{edges[step - 1].kind.value}-->"
            typer.echo(f"{arrow}\n  {step + 1}. {node.name} [{node.layer.value}/{node.kind.value}]")
        typer.echo("")


@app.command()
def impact(
    node_id: Annotated[str, typer.Argument(help="Fail eden node (kimlik ya da ad)")],
    path: ModelPath = None,
) -> None:
    """Bir node fail ederse etkilenenler (blast radius, PLAN.md 8.5)."""
    model = _load(path)
    graph = ModelGraph(model)
    node_id = _ref(model, node_id)
    try:
        radius = graph.blast_radius(node_id)
    except KeyError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    node = model.node(node_id)
    typer.secho(f"{node.name} ({node_id}) fail ederse:\n", bold=True)
    typer.echo(f"Etkilenen toplam: {len(radius['all'])}")
    for label, key in (
        ("Bekleyen süreçler", "processes"),
        ("Etkilenen veri nesneleri", "datasets"),
        ("Tüketim katmanı (rapor/çıktı)", "consumption"),
    ):
        members = sorted(radius[key])
        typer.echo(f"\n{label} ({len(members)}):")
        for member in members:
            target = model.node(member)
            typer.echo(f"  - {target.name} [{target.kind.value}] ({member})")


@app.command()
def order(path: ModelPath = None) -> None:
    """Job'ların topolojik yürütme sırası."""
    model = _load(path)
    graph = ModelGraph(model)
    try:
        sequence = graph.execution_order()
    except Exception as exc:  # nx.NetworkXUnfeasible
        typer.secho(f"Sıra hesaplanamadı (döngü olabilir): {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    for index, process_id in enumerate(sequence, start=1):
        process = model.processes[process_id]
        schedule = ""
        if process.schedule and process.schedule.start_times:
            schedule = f"  @{','.join(process.schedule.start_times)}"
        typer.echo(f"{index:3}. {process.name} ({process_id}){schedule}")


@app.command(name="export-dot")
def export_dot(
    path: ModelPath = None,
    out: Annotated[Path | None, typer.Option(help="Çıktı dosyası; verilmezse ekrana yazar")] = None,
    scope: Annotated[str, typer.Option(help="data|control|all")] = "all",
) -> None:
    """Graphviz DOT üretir (``dot -Tpng model.dot -o model.png``)."""
    model = _load(path)
    graph = ModelGraph(model)
    dot = graph.to_dot(_edge_kinds(scope))
    if out:
        out.write_text(dot, encoding="utf-8")
        typer.echo(f"Yazıldı: {out}")
    else:
        typer.echo(dot)


@app.command()
def columns(
    ref: Annotated[str, typer.Argument(help=f"Kolon referansı: node{COLUMN_SEP}KOLON")],
    path: ModelPath = None,
    direction: Annotated[str, typer.Option(help="upstream|downstream")] = "upstream",
) -> None:
    """Kolon seviyesi lineage izler."""
    model = _load(path)
    graph = ModelGraph(model)
    if COLUMN_SEP in ref:
        node_part, column = ref.split(COLUMN_SEP, 1)
        ref = f"{_ref(model, node_part)}{COLUMN_SEP}{column}"
    try:
        related = graph.column_lineage(ref, direction)
    except KeyError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"{ref} — {direction} ({len(related)}):")
    for item in sorted(related):
        typer.echo(f"  - {item}")


#: (modül, ne sağlar, olmazsa ne olur) — `doctor` ve hata mesajları bunu kullanır.
_REQUIREMENTS: list[tuple[str, str, str]] = [
    ("pydantic", "model doğrulama", "hiçbir şey çalışmaz"),
    ("yaml", "YAML depo", "hiçbir şey çalışmaz"),
    ("networkx", "graf motoru", "hiçbir şey çalışmaz"),
    ("typer", "komut satırı", "hiçbir şey çalışmaz"),
    ("openpyxl", "Excel/CSV okuma", "Excel dokümanları okunamaz"),
    ("pypdf", "PDF okuma", "PDF dokümanları okunamaz"),
    ("sqlglot", "SQL soy analizi", "SQL dosyaları okunamaz"),
    ("httpx", "LLM istemcisi", "yapay zekâ kullanılamaz"),
    ("fastapi", "web uygulaması", "`serve` çalışmaz"),
    ("uvicorn", "web sunucusu", "`serve` çalışmaz"),
    ("python_multipart", "doküman yükleme", "web'den dosya yüklenemez"),
]


@app.command()
def doctor() -> None:
    """Kurulumu denetler: Python, bağımlılıklar, Node.js, diyagram motoru, örnekler.

    Yeni bir makinede ilk çalıştırılacak komut. Eksik varsa çıkış kodu 1 döner.
    """
    import importlib
    import sys as _sys

    from .render.engine import Engine, EngineError, node_version

    typer.secho("DWHFlow kurulum denetimi\n", bold=True)
    typer.echo(f"  Python      {_sys.version.split()[0]}  ({_sys.executable})")
    typer.echo(f"  Paket       dwhflow {__version__}  ({Path(__file__).resolve().parent})\n")
    missing: list[str] = []

    typer.secho("Bağımlılıklar", bold=True)
    for module, provides, consequence in _REQUIREMENTS:
        try:
            found = importlib.import_module(module)
            version = getattr(found, "__version__", "")
            typer.secho(f"  ✓ {module:17} {version:10} {provides}", fg=typer.colors.GREEN)
        except ImportError:
            missing.append(module)
            typer.secho(f"  ✗ {module:17} {'':10} {provides} — eksik: {consequence}",
                        fg=typer.colors.RED)

    typer.secho("\nDiyagram motoru", bold=True)
    node_path, node_major = node_version()
    if node_path and node_major and node_major >= 18:
        typer.secho(f"  ✓ Node.js v{node_major}  ({node_path})", fg=typer.colors.GREEN)
    else:
        missing.append("node")
        typer.secho("  ✗ Node.js 18+ bulunamadı — diyagram üretilemez (https://nodejs.org). "
                    "Üretilmiş HTML'leri görüntülemek için gerekmez.", fg=typer.colors.RED)
    engine = Engine()
    try:
        engine.ensure_installed()
        typer.secho(f"  ✓ Archify {engine.lock['version']}  (SHA-256 doğrulandı, {engine.install_dir})",
                    fg=typer.colors.GREEN)
    except EngineError as exc:
        missing.append("archify")
        typer.secho(f"  ✗ Archify: {exc}", fg=typer.colors.RED)

    samples = Path(__file__).resolve().parent / "examples" / "ornek_dokumanlar"
    typer.secho("\nÖrnekler", bold=True)
    if samples.is_dir():
        typer.secho(f"  ✓ örnek dokümanlar  {samples}  ({len(list(samples.iterdir()))} dosya)",
                    fg=typer.colors.GREEN)
    else:
        typer.secho("  ! örnek dokümanlar bulunamadı", fg=typer.colors.YELLOW)

    if missing:
        typer.secho(f"\n{len(missing)} eksik var. Python paketleri için:  pip install .\n"
                    "Node.js için: https://nodejs.org (LTS sürümü)", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    typer.secho("\nHer şey yerinde. Denemek için:\n  dwhflow convert --ornek --open\n  dwhflow serve",
                fg=typer.colors.GREEN)


def _collect(inputs: list[Path]) -> list[Path]:
    """Dosya ve klasörleri doküman listesine açar (klasörler özyinelemeli, gizliler hariç)."""
    from .ingest import ACCEPTED

    files: list[Path] = []
    for item in inputs:
        if item.is_dir():
            files.extend(sorted(
                p for p in item.rglob("*")
                if p.is_file() and p.suffix.lower() in ACCEPTED
                and not any(part.startswith(".") for part in p.relative_to(item).parts)
            ))
        elif item.is_file():
            files.append(item)
        else:
            typer.secho(f"bulunamadı: {item}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
    return files


def _parse_path(value: str) -> tuple[str, str]:
    for separator in ("->", "→", "=>", ">"):
        if separator in value:
            source, target = (part.strip() for part in value.split(separator, 1))
            if source and target:
                return source, target
    raise typer.BadParameter(f"'{value}' — biçim: KAYNAK>HEDEF")


@app.command()
def convert(
    inputs: Annotated[list[Path] | None, typer.Argument(
        help="Dokümanlar ya da klasörler (xlsx, csv, docx, pdf, md, txt, sql, jil, yaml)")] = None,
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Çıktı klasörü")] = None,
    title: Annotated[str | None, typer.Option(help="Başlık")] = None,
    path: Annotated[list[str] | None, typer.Option(
        "--path", "-p", help="Ek yol diyagramı: KAYNAK>HEDEF (tekrarlanabilir)")] = None,
    focus: Annotated[list[str] | None, typer.Option(
        help="Bir tablonun çevresi (tekrarlanabilir)")] = None,
    hops: Annotated[int, typer.Option(help="Çevre diyagramında adım sayısı")] = 2,
    auto: Annotated[bool, typer.Option(help="Otomatik plan: genel akış, soy ağaçları, batch")] = True,
    render: Annotated[bool, typer.Option(help="Diyagram üret (kapalıysa yalnız model + rapor)")] = True,
    quality: Annotated[str, typer.Option(help="showcase | standard")] = "showcase",
    jobs: Annotated[int, typer.Option(help="Paralel diyagram üretimi")] = 2,
    ornek: Annotated[bool, typer.Option("--ornek", help="Paketle gelen örnek dokümanları kullan")] = False,
    llm: Annotated[bool, typer.Option(help="Kuralların çözemediği paragraflar için yapay zekâ")] = False,
    llm_url: Annotated[str, typer.Option(help="OpenAI uyumlu uç")] = "http://localhost:11434/v1",
    llm_model: Annotated[str, typer.Option(help="Model adı")] = "qwen3:8b",
    llm_key_env: Annotated[str, typer.Option(help="API anahtarının ortam değişkeni")] = "DWHFLOW_AI_KEY",
    llm_allow_external: Annotated[bool, typer.Option(
        help="Yerel olmayan LLM adresine izin ver (doküman içeriği makineden çıkar)")] = False,
    open_result: Annotated[bool, typer.Option("--open", help="Bitince sonuç sayfasını aç")] = False,
) -> None:
    """Dokümanları akışa çevirir: model + doğrulama + etkileşimli Archify diyagramları.

    Örnek:  dwhflow convert ./dokumanlar -o ./cikti --path "SRC_A>RPT_B"
    """
    from datetime import datetime

    from .convert import RunOptions
    from .convert import convert as run_convert
    from .llm import LLMClient, LLMConfig, LLMError
    from .render.slices import slug

    sources = list(inputs or [])
    if ornek:
        sources.append(Path(__file__).resolve().parent / "examples" / "ornek_dokumanlar")
        if not title and not inputs:
            title = "Örnek: kredi kartı işlemleri"
    if not sources:
        typer.secho("Doküman ver ya da örneklerle dene:  dwhflow convert --ornek",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    files = _collect(sources)
    if not files:
        typer.secho("Okunabilir doküman bulunamadı.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    if quality not in ("showcase", "standard"):
        raise typer.BadParameter("kalite: showcase | standard")

    client = None
    if llm:
        config = LLMConfig(enabled=True, base_url=llm_url, model=llm_model,
                           api_key_env=llm_key_env, allow_external_api=llm_allow_external)
        try:
            config.check_allowed()
        except LLMError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2) from exc
        client = LLMClient(config)

    label = title or (files[0].stem if len(files) == 1 else "dokumanlar")
    target = out or Path.cwd() / "dwhflow-cikti" / f"{datetime.now():%Y%m%d-%H%M%S}-{slug(label)}"
    options = RunOptions(
        title=title, quality=quality, render=render, auto=auto, jobs=jobs,
        paths=[_parse_path(value) for value in (path or [])],
        focus=[(center, hops) for center in (focus or [])],
    )

    def progress(stage: str, detail: str) -> None:
        typer.echo(f"  [{stage}] {detail}", err=True)

    typer.secho(f"\n  DWHFlow → {len(files)} doküman", bold=True, err=True)
    report = run_convert(files, target, llm=client, options=options, progress=progress)

    diagrams = report["diagrams"]
    ready = [d for d in diagrams if d["html"]]
    typer.echo("")
    for doc in report["ingest"]["documents"]:
        color = typer.colors.GREEN if doc["status"] == "ok" else typer.colors.YELLOW
        typer.secho(f"  {doc['file']:36} {doc['status']:12} {doc['datasets']:3} tablo "
                    f"{doc['processes']:3} süreç {doc['flows']:3} akış", fg=color)
    typer.echo("")
    for item in diagrams:
        color = typer.colors.GREEN if item["status"] == "pass" else (
            typer.colors.YELLOW if item["html"] else typer.colors.RED)
        mark = "✓" if item["html"] else "✗"
        typer.secho(f"  {mark} {item['title'][:58]:58} {item['summary']}", fg=color)
    for note in report["notes"]:
        typer.secho(f"  ⚠ {note}", fg=typer.colors.YELLOW)
    summary = report["validation"]["summary"]
    typer.echo(f"\n  Doğrulama: {summary['error']} hata, {summary['warning']} uyarı, "
               f"{summary['info']} bilgi")
    gallery = (target / "index.html").resolve()
    typer.secho(f"  Sonuç: {gallery}", bold=True)
    if open_result:
        import webbrowser

        webbrowser.open(gallery.as_uri())
    if render and diagrams and len(ready) < len(diagrams):
        raise typer.Exit(code=1)


#: GEÇİCİ: ekip girişi şimdilik kapalı. Girişi varsayılan yapmak için True yapın;
#: tek seferlik açmak için ``serve --giris`` (calistir.bat -Giris).
GIRIS_VARSAYILAN = False


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Dinlenecek adres")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port")] = 8765,
    data_dir: Annotated[Path | None, typer.Option(
        help="Dönüştürmelerin ve ayarların tutulacağı dizin (varsayılan ./.dwhflow)")] = None,
    allow_host: Annotated[list[str] | None, typer.Option(
        help="Yerel dışında kabul edilecek Host adı (ağdan erişim için sunucu adı)")] = None,
    giris: Annotated[bool, typer.Option(
        "--giris/--girissiz",
        help="Kullanıcı adı/parola iste. Girişsizken herkes yönetici yetkisiyle çalışır.")] = GIRIS_VARSAYILAN,
) -> None:
    """Ekip arayüzünü başlatır: projeler, dokümanlar, model, düzeltmeler, diyagramlar.

    Giriş zorunludur. İlk yönetici ya bu makinedeki tarayıcıdan (http://127.0.0.1:PORT)
    ya da `dwhflow kullanici ekle ADI --rol yonetici` ile oluşturulur. Ekibe açmak için:
    --host 0.0.0.0 --allow-host SUNUCU_ADI (tercihen HTTPS sağlayan bir ters vekil arkasında).
    """
    try:
        import uvicorn

        from .api import create_app
    except ImportError as exc:  # pragma: no cover
        typer.secho(f"Web bağımlılığı eksik: {exc.name}\nGidermek için:  pip install .\n"
                    "Ayrıntı için:   dwhflow doctor", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    workspace_dir = data_dir or Path.cwd() / ".dwhflow"
    hosts = set(allow_host or [])
    from .auth import UserStore
    from .project import Workspace

    users = UserStore(Workspace(workspace_dir).users_file)
    if not giris:
        typer.secho("  Giriş KAPALI: kullanıcı adı/parola sorulmaz; bu adrese erişen herkes yönetici\n"
                    "  yetkisiyle çalışır (günlükte \"ekip\"). Açmak için:  dwhflow serve --giris",
                    fg=typer.colors.YELLOW)
    elif users.empty():
        typer.secho("  İlk kurulum: henüz kullanıcı yok. Yöneticiyi bu makinedeki tarayıcıdan oluşturun\n"
                    "  ya da:  dwhflow kullanici ekle ADI --rol yonetici", fg=typer.colors.YELLOW)
    if host not in ("127.0.0.1", "localhost", "::1"):
        typer.secho("  ⚠ Sunucu ağa açık dinleyecek. Trafik şifresizdir; kurum içinde HTTPS sağlayan bir\n"
                    "    ters vekil (IIS/nginx) arkasında çalıştırmanız önerilir.", fg=typer.colors.YELLOW)
        if not hosts:
            typer.secho("  ⚠ --allow-host verilmedi: diğer makinelerden gelen istekler reddedilecek.",
                        fg=typer.colors.YELLOW)
    shown = "127.0.0.1" if host == "0.0.0.0" else host
    typer.secho(f"\n  DWHFlow  →  http://{shown}:{port}", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"  veri:    {workspace_dir}")
    typer.echo("  API dokümanı: /api/docs\n")
    uvicorn.run(create_app(workspace_dir, allowed_hosts=hosts, auth=giris), host=host, port=port,
                log_level="warning")


user_app = typer.Typer(help="Ekip kullanıcıları (serve için).", no_args_is_help=True)
app.add_typer(user_app, name="kullanici")

_DataDir = Annotated[Path | None, typer.Option("--data-dir", help="Çalışma alanı (varsayılan ./.dwhflow)")]


def _users(data_dir: Path | None):
    from .auth import UserStore
    from .project import Workspace

    return UserStore(Workspace(data_dir or Path.cwd() / ".dwhflow").users_file)


def _ask_password() -> str:
    import getpass

    first = getpass.getpass("Parola: ")
    if first != getpass.getpass("Parola (tekrar): "):
        typer.secho("Parolalar eşleşmiyor.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    return first


@user_app.command("ekle")
def user_add(
    username: Annotated[str, typer.Argument(help="Kullanıcı adı (küçük harf)")],
    name: Annotated[str, typer.Option("--ad", help="Görünen ad")] = "",
    role: Annotated[str, typer.Option("--rol", help="izleyici | duzenleyici | yonetici")] = "duzenleyici",
    data_dir: _DataDir = None,
) -> None:
    """Kullanıcı ekler; parola sorulur (komut satırına yazılmaz)."""
    from .project import StoreError

    try:
        created = _users(data_dir).create(username, name, role, _ask_password())
    except StoreError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    typer.secho(f"Eklendi: {created['username']} ({created['role']})", fg=typer.colors.GREEN)


@user_app.command("liste")
def user_list(data_dir: _DataDir = None) -> None:
    """Kullanıcıları listeler."""
    for item in _users(data_dir).list():
        state = "" if item["active"] else "  (devre dışı)"
        typer.echo(f"{item['username']:20} {item['role']:12} {item['name']}{state}")


@user_app.command("parola")
def user_password(username: Annotated[str, typer.Argument()], data_dir: _DataDir = None) -> None:
    """Kullanıcının parolasını değiştirir (unutulan yönetici parolası için de)."""
    from .project import StoreError

    try:
        _users(data_dir).update(username, password=_ask_password())
    except (StoreError, LookupError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    typer.secho("Parola değişti. Açık oturumlar sunucu yeniden başlayınca düşer.", fg=typer.colors.GREEN)


@user_app.command("rol")
def user_role(username: Annotated[str, typer.Argument()],
              role: Annotated[str, typer.Argument(help="izleyici | duzenleyici | yonetici")],
              data_dir: _DataDir = None) -> None:
    """Kullanıcının rolünü değiştirir."""
    from .project import StoreError

    try:
        _users(data_dir).update(username, role=role)
    except (StoreError, LookupError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    typer.secho(f"{username}: {role}", fg=typer.colors.GREEN)


@user_app.command("sil")
def user_delete(username: Annotated[str, typer.Argument()], data_dir: _DataDir = None) -> None:
    """Kullanıcıyı siler."""
    from .project import StoreError

    try:
        _users(data_dir).delete(username)
    except (StoreError, LookupError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    typer.secho(f"Silindi: {username}", fg=typer.colors.GREEN)


engine_app = typer.Typer(help="Diyagram motoru (Archify) işlemleri.", no_args_is_help=True)
app.add_typer(engine_app, name="engine")


@engine_app.command("install")
def engine_install(
    archive: Annotated[Path | None, typer.Option(
        help="Başka yerden getirilmiş archify.zip (SHA-256 yine doğrulanır)")] = None,
) -> None:
    """Paketle gelen sabitlenmiş Archify sürümünü doğrulayıp kurar."""
    from .render.engine import Engine, EngineError

    engine = Engine()
    try:
        folder = engine.ensure_installed(archive)
    except EngineError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.secho(f"Archify {engine.lock['version']} hazır: {folder}", fg=typer.colors.GREEN)


@engine_app.command("doctor")
def engine_doctor() -> None:
    """Archify'ın kendi denetimini çalıştırır."""
    from .render.engine import Engine, EngineError

    try:
        ok, output = Engine().doctor()
    except EngineError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(output)
    if not ok:
        raise typer.Exit(code=1)


@app.command()
def roundtrip(
    path: ModelPath = None,
    out: Annotated[Path, typer.Option(help="Yazılacak dizin")] = Path("./model-out"),
) -> None:
    """Modeli okuyup normalize ederek yeniden yazar (depo katmanı testi)."""
    model = _load(path)
    written = save_model(model, out)
    for file in written:
        typer.echo(f"Yazıldı: {file}")


if __name__ == "__main__":  # pragma: no cover
    app()
