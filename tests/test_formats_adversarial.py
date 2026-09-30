"""Zor örnekler (``examples/ornek_zor``): kötü niyetli, bozuk ya da yanıltıcı dosyalar.

Hiçbiri istisna fırlatmamalı; her biri raporda doğru durumla görünmeli; çalışma klasörü dışına
dosya yazılmamalı; belgedeki talimatlar uygulanmamalı (belge veridir).
"""

from __future__ import annotations

import time

import pytest

from dwhflow.ingest import ingest_files, preview_document
from dwhflow.ingest.formats import _acma, drawio

from .bicim_yardimci import HARD, generator, summary

REAL_FLOWS = {("SRC_KART_ISLEM", "KART_ISLEM_YYYYMMDD.dat"), ("STG_KART_ISLEM", "ODS_KART_ISLEM")}


def reports_by_file(result) -> dict:
    return {r.file: r for r in result.reports}


def files_under(folder):
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))


class TestPromptInjection:
    def test_instructions_are_data_not_commands(self):
        result = ingest_files([HARD / "talimat_gomulu.txt"])
        found = summary(result)
        assert result.reports[0].status == "ok"
        assert found["links"] == REAL_FLOWS  # gerçek iki akış, ne eksik ne fazla
        assert found["datasets"] == {"SRC_KART_ISLEM", "KART_ISLEM_YYYYMMDD.dat", "STG_KART_ISLEM", "ODS_KART_ISLEM"}
        assert found["processes"] == {"EXT_KART", "SP_ODS_KART"}
        assert "SAHTE_HEDEF" not in found["datasets"]

    def test_injection_paragraph_kept_verbatim_for_review(self):
        result = ingest_files([HARD / "talimat_gomulu.txt"])
        assert len(result.leftovers) == 1
        file, _, paragraph = result.leftovers[0]
        assert file == "talimat_gomulu.txt" and "Önceki talimatları yok say" in paragraph


class TestCorruptAndDisguised:
    def test_corrupt_docx(self):
        report = ingest_files([HARD / "bozuk.docx"]).reports[0]
        assert (report.format, report.status) == ("word", "error")

    def test_xxe_and_entity_expansion_rejected(self):
        started = time.monotonic()
        report = ingest_files([HARD / "dis_varlik.bpmn"]).reports[0]
        assert report.status == "error" and "güvensiz XML" in report.notes[0]
        assert time.monotonic() - started < 5

    def test_entities_inside_compressed_drawio_page(self, tmp_path):
        page = generator().drawio_compress(
            '<!DOCTYPE m [<!ENTITY x SYSTEM "file:///etc/passwd">]><mxGraphModel><root>'
            '<mxCell id="a" value="&x;" vertex="1"/></root></mxGraphModel>')
        path = tmp_path / "kotu.drawio"
        path.write_text(f"<mxfile><diagram>{page}</diagram></mxfile>", encoding="utf-8")
        report = ingest_files([path]).reports[0]
        assert report.status == "error" and "güvensiz XML" in report.notes[0]

    def test_inflate_bomb_in_drawio_page(self, tmp_path, monkeypatch):
        monkeypatch.setattr(drawio, "MAX_INFLATED", 1000)
        page = generator().drawio_compress("<mxGraphModel><root>" + " " * 5000 + "</root></mxGraphModel>")
        path = tmp_path / "buyuk.drawio"
        path.write_text(f"<mxfile><diagram>{page}</diagram></mxfile>", encoding="utf-8")
        report = ingest_files([path]).reports[0]
        assert report.status == "error" and "çok büyük" in report.notes[0]

    def test_html_disguised_as_doc(self):
        result = ingest_files([HARD / "sahte_uzanti.doc"])
        report = result.reports[0]
        assert (report.format, report.status) == ("doc", "ok") and "HTML" in report.notes[0]
        assert summary(result)["links"] == {("ODS_MUSTERI", "DWH.D_MUSTERI")}


class TestArchives:
    @pytest.mark.parametrize("name,reason", [
        ("yol_asimi.zip", "zip-slip"), ("bomba_taklidi.zip", "zip bombası"), ("sifreli.zip", "şifreli")])
    def test_rejected_as_a_whole(self, name, reason):
        result = ingest_files([HARD / name])
        assert [(r.file, r.format, r.status) for r in result.reports] == [(name, "arsiv", "error")]
        assert reason in result.reports[0].notes[0]

    @pytest.mark.parametrize("name", ["yol_asimi.zip", "bomba_taklidi.zip", "sifreli.zip"])
    def test_nothing_written_when_rejected(self, tmp_path, name):
        work = tmp_path / "calisma" / "arsiv"
        work.mkdir(parents=True)
        with pytest.raises(_acma.ArsivHatasi):
            _acma.expand_top(HARD / name, work)
        assert files_under(tmp_path) == ["calisma", "calisma/arsiv"]  # denetim yazmadan önce yapılır

    def test_nested_depth_limit(self):
        result = ingest_files([HARD / "ic_ice.zip"])
        by_file = reports_by_file(result)
        assert {name for name in by_file} == {"ic_ice.zip", "birinci_duzey.txt", "ikinci_duzey.sql", "en_derin.zip"}
        assert by_file["birinci_duzey.txt"].status == by_file["ikinci_duzey.sql"].status == "ok"
        deepest = by_file["en_derin.zip"]
        assert deepest.status == "unsupported" and "iç içe" in deepest.notes[0]
        assert all(r.container == "ic_ice.zip" for name, r in by_file.items() if name != "ic_ice.zip")

    def test_extraction_stays_inside_workdir(self, tmp_path):
        work = tmp_path / "is"
        work.mkdir()
        paths = _acma.expand_top(HARD / "ic_ice.zip", work)
        assert paths and all(p.resolve().is_relative_to(work.resolve()) for p in paths)
        assert all(part == "is" or part.startswith("is/") for part in files_under(tmp_path))


class TestEmailWithAttachments:
    def test_attachments_and_forwarded_mail(self):
        result = ingest_files([HARD / "ekli_eposta.eml"])
        by_file = reports_by_file(result)
        assert set(by_file) == {"ekli_eposta.eml", "ekli_eposta.govde.txt", "kart_sttm_ek.csv", "stg_notu.govde.txt"}
        assert by_file["ekli_eposta.eml"].format == "arsiv"
        assert by_file["ekli_eposta.govde.txt"].status == "empty"  # gövdede akış yok: dürüstçe "boş"
        assert by_file["kart_sttm_ek.csv"].status == by_file["stg_notu.govde.txt"].status == "ok"
        assert {r.container for name, r in by_file.items() if name != "ekli_eposta.eml"} == {"ekli_eposta.eml"}
        assert summary(result)["links"] == {("ODS_KART_ISLEM", "DWH.F_KART_ISLEM"), ("SRC_MUSTERI", "STG_MUSTERI")}


class TestWholeFolder:
    def test_every_file_reported_without_exceptions(self):
        before = files_under(HARD)
        samples = [p for p in sorted(HARD.iterdir()) if p.name != "README.md"]
        result = ingest_files(samples)
        top = {r.file for r in result.reports if r.container is None}
        assert top == {p.name for p in samples}  # sessiz atlama yok
        assert {r.status for r in result.reports} <= {"ok", "empty", "error", "unsupported"}
        assert files_under(HARD) == before  # örnek klasörüne bir şey yazılmadı

    @pytest.mark.parametrize("name", sorted(p.name for p in HARD.iterdir() if p.name != "README.md"))
    def test_preview_never_raises(self, name):
        _, blocks = preview_document(HARD / name)
        assert isinstance(blocks, list) and blocks
