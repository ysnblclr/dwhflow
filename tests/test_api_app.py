"""Ekip web uygulaması: güvenlik korkulukları ve ekran uçlarının akışları."""

from __future__ import annotations

import io
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dwhflow import auth
from dwhflow.api import create_app
from dwhflow.api.app import _host_name

from .test_convert import FakeEngine

H = {"X-DWHFlow": "1"}
PASSWORD = "Guclu-Parola1"
SAMPLES = Path(__file__).resolve().parents[1] / "src" / "dwhflow" / "examples" / "ornek_dokumanlar"


@pytest.fixture(autouse=True)
def fast_hash(monkeypatch):
    monkeypatch.setattr(auth, "PBKDF2_ITERATIONS", 1000)


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path / "veri", engine=FakeEngine(), allowed_hosts={"testserver"})


def local(app):
    return TestClient(app, client=("127.0.0.1", 50000))


def remote(app):
    return TestClient(app, client=("10.1.2.3", 50000))


def wait(client, job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError("iş bitmedi")


@pytest.fixture
def admin(app):
    with local(app) as client:
        response = client.post("/api/auth/setup", json={"username": "yonetici", "name": "Y",
                                                        "password": PASSWORD}, headers=H)
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture
def kart(admin):
    response = admin.post("/api/projects", json={"name": "Kredi Kartı", "sample": "kart"}, headers=H)
    assert response.status_code == 200, response.text
    body = response.json()
    assert wait(admin, body["job"]["id"])["state"] == "done"
    return body["slug"]


def login_as(app, admin, username, role):
    assert admin.post("/api/users", json={"username": username, "name": username, "role": role,
                                          "password": PASSWORD}, headers=H).status_code == 200
    client = remote(app)
    client.__enter__()
    assert client.post("/api/auth/login", json={"username": username, "password": PASSWORD},
                       headers=H).status_code == 200
    return client


class TestSecurity:
    def test_foreign_host(self, app):
        with local(app) as client:
            assert client.get("/api/health", headers={"host": "kotu.example"}).status_code == 421
            assert client.get("/api/health", headers={"host": "127.0.0.1.kotu.example"}).status_code == 421
            assert client.get("/api/health", headers={"host": "[::1]:8765"}).status_code == 200

    def test_csrf_header_required(self, admin):
        assert admin.post("/api/projects", json={"name": "x"}).status_code == 403

    def test_auth_required(self, app):
        with local(app) as client:
            assert client.get("/api/projects").status_code == 401
            assert client.get("/dosya/x/y/index.html").status_code == 401
            assert client.get("/api/health").status_code == 200
            assert client.get("/").status_code == 200

    def test_setup_only_local_and_once(self, app):
        with remote(app) as client:
            assert client.get("/api/auth/state").json()["setup_allowed"] is False
            assert client.post("/api/auth/setup", json={"username": "x", "password": PASSWORD},
                               headers=H).status_code == 403
        with local(app) as client:
            assert client.post("/api/auth/setup", json={"username": "yon", "password": PASSWORD},
                               headers=H).status_code == 200
            assert client.post("/api/auth/setup", json={"username": "yon2", "password": PASSWORD},
                               headers=H).status_code == 409

    def test_cookie_flags(self, app):
        with local(app) as client:
            response = client.post("/api/auth/setup", json={"username": "yon", "password": PASSWORD}, headers=H)
            cookie = response.headers["set-cookie"].lower()
            assert "httponly" in cookie and "samesite=strict" in cookie

    def test_login_throttled(self, app, admin):
        with remote(app) as client:
            for _ in range(5):
                assert client.post("/api/auth/login", json={"username": "yonetici", "password": "yanlis"},
                                   headers=H).status_code == 401
            assert client.post("/api/auth/login", json={"username": "yonetici", "password": PASSWORD},
                               headers=H).status_code == 429

    def test_roles(self, app, admin, kart):
        viewer = login_as(app, admin, "izleyen", "izleyici")
        editor = login_as(app, admin, "duzenleyen", "duzenleyici")
        try:
            assert viewer.get(f"/api/projects/{kart}/datasets").status_code == 200
            assert viewer.post(f"/api/projects/{kart}/corrections", json={"ops": []}, headers=H).status_code == 403
            assert viewer.post("/api/projects", json={"name": "x"}, headers=H).status_code == 403
            assert editor.post("/api/projects", json={"name": "x"}, headers=H).status_code == 200
            assert editor.get("/api/users").status_code == 403
            assert editor.put("/api/settings", json={"patch": {}}, headers=H).status_code == 403
            assert editor.delete(f"/api/projects/{kart}", headers=H).status_code == 403
            assert editor.put(f"/api/projects/{kart}/settings", json={"patch": {"yz": {"profiller": {}}}},
                              headers=H).status_code == 403
        finally:
            viewer.__exit__(None, None, None)
            editor.__exit__(None, None, None)

    def test_deactivated_user_session_revoked(self, app, admin):
        client = login_as(app, admin, "gecici", "izleyici")
        try:
            assert client.get("/api/projects").status_code == 200
            admin.patch("/api/users/gecici", json={"active": False}, headers=H)
            assert client.get("/api/projects").status_code == 401
        finally:
            client.__exit__(None, None, None)

    def test_csp_on_index(self, admin):
        assert "default-src 'self'" in admin.get("/").headers["content-security-policy"]

    @pytest.mark.parametrize("header, expected", [("127.0.0.1:8765", "127.0.0.1"), ("[::1]:80", "[::1]"),
                                                  ("LOCALHOST", "localhost")])
    def test_host_parsing(self, header, expected):
        assert _host_name(header) == expected


class TestProjects:
    def test_dashboard_and_documents(self, admin, kart):
        dashboard = admin.get(f"/api/projects/{kart}/dashboard").json()
        assert dashboard["summary"]["stats"]["datasets"] == 12 and dashboard["recent"]
        documents = admin.get(f"/api/projects/{kart}/documents").json()
        assert {d["report"]["status"] for d in documents} == {"ok"}

    def test_upload_new_version_triggers_build(self, admin, kart):
        files = [("files", ("notlar.txt", "STG_YENI tablosundan ODS_YENI tablosuna yüklenir.".encode()))]
        response = admin.post(f"/api/projects/{kart}/documents", files=files, headers=H)
        assert response.status_code == 200 and response.json()["job"]
        assert wait(admin, response.json()["job"]["id"])["state"] == "done"
        names = {d["name"] for d in admin.get(f"/api/projects/{kart}/datasets").json()["items"]}
        assert {"STG_YENI", "ODS_YENI"} <= names

    def test_uploaded_html_is_only_downloaded(self, admin, kart):
        """Güvenlik: yüklenen HTML uygulamanın kökeninden sayfa olarak açılamaz (depolanmış XSS)."""
        page = b"<h1>Akis</h1><p>STG_WEB tablosundan ODS_WEB tablosuna gece aktarilir.</p><script>alert(1)</script>"
        response = admin.post(f"/api/projects/{kart}/documents", files=[("files", ("wiki.html", page))], headers=H)
        assert response.status_code == 200, response.text
        download = admin.get(f"/api/projects/{kart}/documents/wiki.html/download")
        assert download.status_code == 200 and download.content == page
        assert download.headers["content-type"] == "application/octet-stream"
        assert download.headers["content-disposition"].startswith("attachment")
        assert download.headers["x-content-type-options"] == "nosniff"
        preview = admin.get(f"/api/projects/{kart}/documents/wiki.html/preview").json()
        assert preview["format"] == "html" and all("<script" not in str(b) for b in preview["blocks"])

    def test_upload_rejects_bad_extension(self, admin, kart):
        response = admin.post(f"/api/projects/{kart}/documents", files=[("files", ("x.exe", b"MZ"))], headers=H)
        assert response.status_code == 400

    def test_preview_items_match_blocks(self, admin, kart):
        preview = admin.get(f"/api/projects/{kart}/documents/Kart_Tasarim_Dokumani.docx/preview").json()
        locators = {b["locator"] for b in preview["blocks"]}
        assert preview["format"] == "word" and preview["items"] and preview["leftovers"]
        for item in preview["items"]:
            assert item["locator"] in locators or any(item["locator"].startswith(b + " · ") for b in locators)

    def test_preview_leftovers_carry_review_key(self, admin, kart, monkeypatch):
        url = f"/api/projects/{kart}/documents/Kart_Tasarim_Dokumani.docx/preview"
        leftover = admin.get(url).json()["leftovers"][0]
        assert leftover["key"].startswith("paragraf:") and leftover["closed"] is False
        # Kuyruk sınırının ötesindeki paragraf da anahtarıyla kapatılabilir.
        from dwhflow.project import review as review_mod
        monkeypatch.setattr(review_mod, "MAX_ITEMS_PER_TYPE", 0)
        engine = admin.app.state.ctx.engine
        engine.invalidate(admin.app.state.ctx.workspace.project(kart))
        assert not admin.get(f"/api/projects/{kart}/review?type=paragraf").json()["items"]
        response = admin.post(f"/api/projects/{kart}/review/decide",
                              json={"decisions": [{"key": leftover["key"], "decision": "reject"}]}, headers=H)
        assert response.status_code == 200, response.text
        closed = next(x for x in admin.get(url).json()["leftovers"] if x["key"] == leftover["key"])
        assert closed["closed"] is True
        again = admin.post(f"/api/projects/{kart}/review/decide",
                           json={"decisions": [{"key": leftover["key"], "decision": "reject"}]}, headers=H)
        assert again.status_code == 409

    def test_force_reader(self, admin, kart):
        response = admin.put(f"/api/projects/{kart}/documents/operasyon_notlari.txt/reader",
                             json={"reader": "sql"}, headers=H)
        assert response.status_code == 200 and response.json()["forced_reader"] == "sql"
        wait(admin, response.json()["job"]["id"])
        document = next(d for d in admin.get(f"/api/projects/{kart}/documents").json()
                        if d["name"] == "operasyon_notlari.txt")
        assert document["report"]["format"] == "sql" and document["report"]["detected"] == "zorla"
        response = admin.put(f"/api/projects/{kart}/documents/operasyon_notlari.txt/reader",
                             json={"reader": None}, headers=H)
        assert response.json()["forced_reader"] is None

    def test_delete_document(self, admin, kart):
        response = admin.delete(f"/api/projects/{kart}/documents/BDDK_Bildirim_Sureci.pdf", headers=H)
        wait(admin, response.json()["job"]["id"])
        assert len(admin.get(f"/api/projects/{kart}/documents").json()) == 5

    def test_search(self, admin, kart):
        results = admin.get(f"/api/projects/{kart}/search?q=musteri").json()
        assert results[0]["type"] == "dataset" and all("MUSTERI" in r["name"].upper() for r in results)

    def test_unknown_project(self, admin):
        assert admin.get("/api/projects/yok/dashboard").status_code == 404


class TestModelAndCorrections:
    def test_lists_and_details(self, admin, kart):
        datasets = admin.get(f"/api/projects/{kart}/datasets?layer=staging").json()
        assert datasets["total"] and all(r["layer"] == "staging" for r in datasets["items"])
        detail = admin.get(f"/api/projects/{kart}/datasets/ods_kart_islem").json()
        assert detail["upstream"] and detail["downstream"] and detail["sources"]
        process = admin.get(f"/api/projects/{kart}/processes/sp_ods_kart").json()
        assert process["reads"] and process["writes"] and process["impact"]["datasets"]
        assert admin.get(f"/api/projects/{kart}/datasets/yok").status_code == 404
        assert admin.get(f"/api/projects/{kart}/sttm").json()
        assert admin.get(f"/api/projects/{kart}/links?draft=true").json()

    def test_paths_by_name(self, admin, kart):
        body = admin.get(f"/api/projects/{kart}/paths?source=SRC_KART_ISLEM&target=BDDK_PORTAL").json()
        assert body["paths"] and body["paths"][0][0]["name"] == "SRC_KART_ISLEM"

    def test_paths_resolve_processes_and_report_ambiguity(self, admin, kart):
        body = admin.get(f"/api/projects/{kart}/paths?source=SP_ODS_KART&target=BDDK_PORTAL").json()
        assert body["source"] == "sp_ods_kart" and body["paths"]
        missing = admin.get(f"/api/projects/{kart}/paths?source=YOK_BOYLE&target=BDDK_PORTAL")
        assert missing.status_code == 404 and "bulunamadı" in missing.json()["detail"]

    def test_correction_flow(self, admin, kart):
        response = admin.post(f"/api/projects/{kart}/corrections", json={"ops": [
            {"islem": "katman_degistir", "hedef": {"nesne": "STG_MUSTERI"}, "deger": {"katman": "ods"}}],
            "reason": "test"}, headers=H)
        assert response.status_code == 200
        created = response.json()["created"][0]
        assert "sonuc" in created and "sonuc" not in admin.get(f"/api/projects/{kart}/corrections").json()[-1]
        assert admin.get(f"/api/projects/{kart}/datasets/stg_musteri").json()["layer"] == "ods"
        detail = admin.get(f"/api/projects/{kart}/datasets/stg_musteri").json()
        assert detail["corrections"][0]["id"] == created["id"]
        assert admin.delete(f"/api/projects/{kart}/corrections/{created['id']}", headers=H).status_code == 200
        assert admin.get(f"/api/projects/{kart}/datasets/stg_musteri").json()["layer"] == "staging"
        assert admin.post(f"/api/projects/{kart}/corrections/{created['id']}/redo", headers=H).status_code == 200
        audit = [e["islem"] for e in admin.get(f"/api/projects/{kart}/audit").json()]
        assert audit[:3] == ["yinele", "geri_al", "duzeltme"]

    def test_invalid_correction_400(self, admin, kart):
        response = admin.post(f"/api/projects/{kart}/corrections", json={"ops": [
            {"islem": "nesne_sil", "hedef": {"nesne": "YOK"}}]}, headers=H)
        assert response.status_code == 400 and "bulunamadı" in response.json()["detail"]
        assert admin.post(f"/api/projects/{kart}/corrections", json={"ops": [{"islem": "uydurma"}]},
                          headers=H).status_code == 400

    def test_preview(self, admin, kart):
        diff = admin.post(f"/api/projects/{kart}/corrections/preview", json={"ops": [
            {"islem": "nesne_sil", "hedef": {"nesne": "BDDK_PORTAL"}}]}, headers=H).json()
        assert diff["nodes_removed"][0]["name"] == "BDDK_PORTAL"
        assert admin.get(f"/api/projects/{kart}/corrections").json() == []

    def test_export(self, admin, kart):
        assert admin.get(f"/api/projects/{kart}/model/export?format=json").json()["datasets"]
        response = admin.get(f"/api/projects/{kart}/model/export?format=yaml")
        assert response.headers["content-type"] == "application/zip"


class TestReviewAndFindings:
    def test_decide(self, admin, kart):
        review = admin.get(f"/api/projects/{kart}/review").json()
        links = [i for i in review["items"] if i["type"] == "bag"]
        response = admin.post(f"/api/projects/{kart}/review/decide", json={"decisions": [
            {"key": links[0]["key"], "decision": "accept"}, {"key": links[1]["key"], "decision": "reject"}]},
            headers=H)
        assert response.status_code == 200 and len(response.json()["created"]) >= 2
        assert admin.get(f"/api/projects/{kart}/review?type=bag").json()["counts"]["bag"] == 1

    def test_stale_key_409(self, admin, kart):
        assert admin.post(f"/api/projects/{kart}/review/decide", json={"decisions": [
            {"key": "bag:YOK>YOK", "decision": "accept"}]}, headers=H).status_code == 409

    def test_table_mapping_adds_synonyms(self, admin, kart):
        table = next(i for i in admin.get(f"/api/projects/{kart}/review").json()["items"] if i["type"] == "tablo")
        headers = table["headers"]
        response = admin.post(f"/api/projects/{kart}/review/decide", json={"decisions": [
            {"key": table["key"], "decision": "accept",
             "mapping": {headers[0]: "source_table", headers[1]: "target_table"}}]}, headers=H)
        assert response.status_code == 200 and response.json()["settings_changed"]
        wait(admin, response.json()["job"]["id"])
        settings = admin.get(f"/api/projects/{kart}/settings").json()
        assert headers[0].strip() in settings["project"]["okuma"]["es_anlamlilar"]["source_table"]

    def test_paragraph_accept_rejected(self, admin, kart):
        paragraph = next(i for i in admin.get(f"/api/projects/{kart}/review").json()["items"]
                         if i["type"] == "paragraf")
        assert admin.post(f"/api/projects/{kart}/review/decide", json={"decisions": [
            {"key": paragraph["key"], "decision": "accept"}]}, headers=H).status_code == 400

    def test_findings_ignore(self, admin, kart):
        findings = admin.get(f"/api/projects/{kart}/findings").json()
        target = findings["items"][0]
        admin.post(f"/api/projects/{kart}/corrections", json={"ops": [
            {"islem": "bulgu_yoksay", "hedef": {"kural": target["rule"], "ozne": target["subject"]},
             "neden": "biliniyor"}]}, headers=H)
        after = admin.get(f"/api/projects/{kart}/findings").json()
        ignored = [i for i in after["items"] if i["ignored"]]
        assert len(ignored) == 1 and ignored[0]["ignore"]["reason"] == "biliniyor"


class TestSettingsEndpoints:
    def test_project_settings_origin_and_rebuild(self, admin, kart):
        response = admin.put(f"/api/projects/{kart}/settings", json={"patch": {"diyagram": {"butce": 16}}},
                             headers=H).json()
        assert response["origins"]["diyagram.butce"] == "proje" and response["job"] is None
        response = admin.put(f"/api/projects/{kart}/settings", json={"patch": {"okuma": {"ad_kurallari": [
            {"tur": "onek", "deger": "STG_", "katman": "core"}]}}}, headers=H).json()
        assert response["job"]
        wait(admin, response["job"]["id"])
        assert admin.get(f"/api/projects/{kart}/datasets/stg_kart_islem").json()["layer"] == "core"

    def test_invalid_settings(self, admin, kart):
        assert admin.put(f"/api/projects/{kart}/settings", json={"patch": {"diyagram": {"butce": 1}}},
                         headers=H).status_code == 400

    def test_global_settings_admin(self, admin):
        response = admin.put("/api/settings", json={"patch": {"diyagram": {"butce": 30}}}, headers=H).json()
        assert response["origins"]["diyagram.butce"] == "genel"

    def test_effect_preview(self, admin, kart):
        preview = admin.post(f"/api/projects/{kart}/settings/preview", json={"patch": {"okuma": {
            "varsayilan_ad_kurallari": False}}}, headers=H).json()
        assert preview["model"]["nodes_changed"]
        assert admin.get(f"/api/projects/{kart}/settings").json()["project"] == {}

    def test_name_test(self, admin, kart):
        result = admin.post(f"/api/projects/{kart}/settings/name-test", json={"name": "MUSTERI_HIST", "okuma": {
            "ad_kurallari": [{"tur": "sonek", "deger": "_HIST", "katman": "core"}]}}, headers=H).json()
        assert result["layer"] == "core" and result["rule"]["origin"] == "ayar"

    def test_export_import(self, admin, kart):
        admin.put(f"/api/projects/{kart}/settings", json={"patch": {"diyagram": {"butce": 18}}}, headers=H)
        exported = admin.get(f"/api/projects/{kart}/settings/export").text
        admin.put(f"/api/projects/{kart}/settings", json={"patch": {"diyagram": {"butce": 20, "kalite": "standard"}}},
                  headers=H)
        preview = admin.post(f"/api/projects/{kart}/settings/import", json={"yaml": exported}, headers=H).json()
        assert not preview["applied"] and preview["changes"]
        applied = admin.post(f"/api/projects/{kart}/settings/import", json={"yaml": exported, "apply": True},
                             headers=H).json()
        assert applied["applied"]
        assert admin.get(f"/api/projects/{kart}/settings").json()["project"] == {"diyagram": {"butce": 18}}

    def test_guide(self, admin, kart):
        assert admin.get(f"/api/projects/{kart}/guide").json()["roles"]
        assert admin.get("/api/guide?format=md").text.startswith("# ")


class TestDiagrams:
    def test_run_files_and_compare(self, admin, kart):
        plan = admin.get(f"/api/projects/{kart}/diagrams/plan").json()
        assert plan["slices"]
        response = admin.post(f"/api/projects/{kart}/runs", json={}, headers=H).json()
        job = wait(admin, response["job"]["id"])
        assert job["state"] == "done" and job["result"]["ready"] == job["result"]["total"]
        run = response["run"]
        assert admin.get(f"/dosya/{kart}/{run}/index.html").status_code == 200
        assert admin.get(f"/dosya/{kart}/{run}/..%2F..%2Fproje.yaml").status_code == 404
        detail = admin.get(f"/api/projects/{kart}/runs/{run}").json()
        assert detail["report"]["diagrams"] and detail["status"]["state"] == "done"
        admin.post(f"/api/projects/{kart}/corrections", json={"ops": [
            {"islem": "nesne_sil", "hedef": {"nesne": "BDDK_PORTAL"}}]}, headers=H)
        diff = admin.get(f"/api/projects/{kart}/compare?a={run}").json()
        assert [r["name"] for r in diff["nodes_removed"]] == ["BDDK_PORTAL"]
        assert admin.delete(f"/api/projects/{kart}/runs/{run}", headers=H).status_code == 200

    def test_hidden_and_custom_paths(self, admin, kart):
        admin.put(f"/api/projects/{kart}/settings", json={"patch": {"diyagram": {
            "yollar": [["SRC_KART_ISLEM", "BDDK_PORTAL"]], "gizli": ["DWH.D_MUSTERI"],
            "otomatik": {"batch": False}}}}, headers=H)
        slices = admin.get(f"/api/projects/{kart}/diagrams/plan").json()["slices"]
        assert slices[0]["kind"] == "path" and all(s["kind"] != "batch" for s in slices)


class TestSystemAndSamples:
    def test_system_meta_formats(self, admin):
        assert admin.get("/api/system").json()["engine"]["ok"]
        meta = admin.get("/api/meta").json()
        assert {layer["value"] for layer in meta["layers"]} >= {"staging", "mart"}
        assert any(f["key"] == "jil" for f in admin.get("/api/formats").json()["formats"])

    def test_samples_and_templates(self, admin, kart):
        catalog = admin.get("/api/samples").json()
        assert catalog["scenarios"][0]["files"]
        name = catalog["scenarios"][0]["files"][0]["name"]
        assert admin.get(f"/api/samples/kart/{name}").status_code == 200
        assert admin.get("/api/samples/kart/..%2F..%2Fsettings.py").status_code == 404
        for template in catalog["templates"]:
            assert admin.get(f"/api/templates/{template['id']}").content
        response = admin.post(f"/api/projects/{kart}/documents/sample",
                              json={"group": "bicim", "name": catalog["formats"][0]["name"]}, headers=H)
        assert response.status_code == 200
        wait(admin, response.json()["job"]["id"])

    def test_users_admin(self, admin):
        assert admin.post("/api/users", json={"username": "ayse", "name": "Ayşe", "role": "izleyici",
                                              "password": PASSWORD}, headers=H).status_code == 200
        assert [u["username"] for u in admin.get("/api/users").json()] == ["ayse", "yonetici"]
        assert admin.patch("/api/users/yonetici", json={"role": "izleyici"}, headers=H).status_code == 400
        assert admin.delete("/api/users/yonetici", headers=H).status_code == 400
        assert admin.delete("/api/users/ayse", headers=H).status_code == 200

    def test_password_change(self, app, admin):
        assert admin.post("/api/auth/password", json={"current": "yanlis", "new": "Yeni-Parola22"},
                          headers=H).status_code == 400
        assert admin.post("/api/auth/password", json={"current": PASSWORD, "new": "Yeni-Parola22"},
                          headers=H).status_code == 200
        with remote(app) as client:
            assert client.post("/api/auth/login", json={"username": "yonetici", "password": "Yeni-Parola22"},
                               headers=H).status_code == 200

    def test_logout(self, admin):
        admin.post("/api/auth/logout", headers=H)
        assert admin.get("/api/projects").status_code == 401

    def test_delete_project(self, admin, kart):
        assert admin.delete(f"/api/projects/{kart}", headers=H).status_code == 200
        assert admin.get("/api/projects").json() == []


def test_upload_io_uses_streams(tmp_path):
    """Yükleme belleğe tümüyle alınmaz: depo akışı parça parça okur."""
    from dwhflow.project import Workspace

    project = Workspace(tmp_path).create_project("P", user="u")
    reads = []

    class Stream(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)

    project.store_upload("a.txt", Stream(b"x" * 10), user="u")
    assert all(size > 0 for size in reads)
