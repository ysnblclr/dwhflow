"""Çalışma alanı: dosya adları, yükleme sınırları, ayarlar, çalışma kimlikleri."""

from __future__ import annotations

import io

import pytest
import yaml

from dwhflow.api import Workspace, WorkspaceError, safe_filename
from dwhflow.api import workspace as workspace_module


@pytest.fixture
def ws(tmp_path):
    return Workspace(tmp_path / "alan")


class TestFilenames:
    @pytest.mark.parametrize("raw, expected", [
        ("STTM.xlsx", "STTM.xlsx"),
        ("../../etc/passwd.txt", "passwd.txt"),
        ("C:\\Users\\x\\tasarım.docx", "tasarım.docx"),   # Windows yolu Linux'ta da ayrılır
        ("a/b\\c.sql", "c.sql"),
        ("kötü<ad>:*?.md", "kötü_ad____.md"),
    ])
    def test_cleaned(self, raw, expected):
        assert safe_filename(raw) == expected

    @pytest.mark.parametrize("raw", ["..", ".", "...", "", "   ", "a/..", "\\.."])
    def test_dots_only_rejected(self, raw):
        """Regresyon (bulut incelemesi): '..' adı klasörün kendisine yazmaya çalışıyordu."""
        with pytest.raises(WorkspaceError):
            safe_filename(raw)

    def test_length_capped(self):
        assert len(safe_filename("a" * 500 + ".txt")) <= 180


class TestUploads:
    def test_stored_under_inputs(self, ws):
        _, folder = ws.new_run()
        path = ws.store_upload(folder, "../notlar.txt", io.BytesIO(b"STG_A -> ODS_A"))
        assert path == folder / "inputs" / "notlar.txt" and path.read_bytes() == b"STG_A -> ODS_A"

    def test_unsupported_extension(self, ws):
        _, folder = ws.new_run()
        with pytest.raises(WorkspaceError, match="desteklenmeyen"):
            ws.store_upload(folder, "zararli.exe", io.BytesIO(b"MZ"))
        with pytest.raises(WorkspaceError):
            ws.store_upload(folder, "sayfa.html", io.BytesIO(b"<script>"))

    def test_size_limit_streams_and_cleans_up(self, ws, monkeypatch):
        monkeypatch.setattr(workspace_module, "MAX_FILE_BYTES", 10)
        monkeypatch.setattr(workspace_module, "_CHUNK", 4)
        _, folder = ws.new_run()
        with pytest.raises(WorkspaceError, match="çok büyük"):
            ws.store_upload(folder, "buyuk.txt", io.BytesIO(b"x" * 11))
        assert not (folder / "inputs" / "buyuk.txt").exists()


class TestSettings:
    def test_defaults(self, ws):
        settings = ws.settings()
        assert settings["quality"] == "showcase"
        assert settings["llm"]["enabled"] is False
        assert settings["llm"]["allow_external_api"] is False

    def test_api_key_never_written(self, ws):
        ws.save_settings({"llm": {"enabled": True, "api_key": "sk-gizli", "password": "x",
                                  "api_key_env": "BENIM_ANAHTARIM"}})
        text = ws.config_file.read_text(encoding="utf-8")
        assert "sk-gizli" not in text and "password" not in text
        assert yaml.safe_load(text)["llm"]["api_key_env"] == "BENIM_ANAHTARIM"
        assert ws.llm_config().enabled

    def test_unknown_setting_rejected(self, ws):
        with pytest.raises(WorkspaceError, match="bilinmeyen"):
            ws.save_settings({"data_dir": "/etc"})

    def test_partial_llm_patch_keeps_rest(self, ws):
        ws.save_settings({"llm": {"model": "llama3.1:8b"}})
        ws.save_settings({"llm": {"enabled": True}})
        config = ws.llm_config()
        assert config.model == "llama3.1:8b" and config.enabled

    def test_corrupt_config_falls_back(self, ws):
        ws.config_file.write_text("llm: [bozuk", encoding="utf-8")
        assert ws.settings()["quality"] == "showcase"


class TestRuns:
    def test_run_id_validated(self, ws):
        for bad in ["../x", "..", "20260101-000000-zzzzzz", "abc"]:
            with pytest.raises(WorkspaceError):
                ws.run_dir(bad)

    def test_missing_run(self, ws):
        with pytest.raises(FileNotFoundError):
            ws.run_dir("20260101-000000-abcdef")

    def test_list_and_delete(self, ws):
        run_id, folder = ws.new_run()
        ws.write_status(folder, {"state": "done", "title": "Kart", "inputs": ["a.xlsx"]})
        assert [r["id"] for r in ws.list_runs()] == [run_id]
        assert ws.list_runs()[0]["title"] == "Kart"
        ws.delete_run(run_id)
        assert ws.list_runs() == []

    def test_running_run_not_deleted(self, ws):
        run_id, folder = ws.new_run()
        ws.write_status(folder, {"state": "running"})
        with pytest.raises(WorkspaceError, match="sürüyor"):
            ws.delete_run(run_id)
        ws.delete_run(run_id, force=True)

    def test_foreign_folders_ignored(self, ws):
        (ws.runs / "baska-klasor").mkdir()
        assert ws.list_runs() == []
