"""Kullanıcılar, parola özetleri, oturumlar ve giriş kısıtlaması."""

from __future__ import annotations

import time

import pytest

from dwhflow import auth
from dwhflow.auth import LoginThrottle, Sessions, UserStore, hash_password, verify_password
from dwhflow.project import StoreError


@pytest.fixture(autouse=True)
def fast_hash(monkeypatch):
    monkeypatch.setattr(auth, "PBKDF2_ITERATIONS", 1000)


@pytest.fixture
def users(tmp_path) -> UserStore:
    return UserStore(tmp_path / "kullanicilar.yaml")


class TestPasswords:
    def test_hash_roundtrip_and_salt(self):
        first, second = hash_password("Guclu-Parola1"), hash_password("Guclu-Parola1")
        assert first != second and verify_password("Guclu-Parola1", first)
        assert not verify_password("guclu-parola1", first)

    def test_malformed_hash(self):
        assert not verify_password("x", "bozuk") and not verify_password("x", "md5$1$a$b")

    @pytest.mark.parametrize("password", ["kisa1!", "sadecekucukharf", "ayse-Parola123"])
    def test_policy(self, users, password):
        with pytest.raises(StoreError):
            users.create("ayse", "Ayşe", "duzenleyici", password)


class TestUsers:
    def test_create_and_authenticate(self, users):
        users.create("Ayse.Y", "Ayşe", "duzenleyici", "Guclu-Parola1")
        assert users.authenticate("ayse.y", "Guclu-Parola1")["role"] == "duzenleyici"
        assert users.authenticate("ayse.y", "yanlis-Parola1") is None
        assert users.authenticate("yok", "Guclu-Parola1") is None
        assert users.get("ayse.y")["last_login"]

    def test_password_never_stored_plain(self, users):
        users.create("ayse", "Ayşe", "izleyici", "Guclu-Parola1")
        text = users.path.read_text(encoding="utf-8")
        assert "Guclu-Parola1" not in text and "pbkdf2_sha256$" in text
        assert "parola" not in users.get("ayse")

    def test_bad_input(self, users):
        with pytest.raises(StoreError):
            users.create("a", "A", "izleyici", "Guclu-Parola1")
        with pytest.raises(StoreError):
            users.create("ayse", "A", "patron", "Guclu-Parola1")
        users.create("ayse", "A", "izleyici", "Guclu-Parola1")
        with pytest.raises(StoreError, match="zaten"):
            users.create("ayse", "A", "izleyici", "Guclu-Parola1")

    def test_last_admin_protected(self, users):
        users.create("admin", "Y", "yonetici", "Guclu-Parola1")
        with pytest.raises(StoreError):
            users.update("admin", role="izleyici")
        with pytest.raises(StoreError):
            users.update("admin", active=False)
        with pytest.raises(StoreError):
            users.delete("admin")
        users.create("admin2", "Y", "yonetici", "Guclu-Parola1")
        users.update("admin", role="duzenleyici")
        assert users.get("admin")["role"] == "duzenleyici"

    def test_inactive_cannot_login(self, users):
        users.create("admin", "Y", "yonetici", "Guclu-Parola1")
        users.create("ayse", "A", "izleyici", "Guclu-Parola1")
        users.update("ayse", active=False)
        assert users.authenticate("ayse", "Guclu-Parola1") is None

    def test_cache_sees_external_change(self, users):
        users.create("ayse", "A", "izleyici", "Guclu-Parola1")
        assert users.get("ayse")
        other = UserStore(users.path)
        other.create("mehmet", "M", "izleyici", "Guclu-Parola1")
        assert users.get("mehmet")


class TestSessions:
    def test_lifecycle(self):
        sessions = Sessions()
        token = sessions.create("ayse")
        assert sessions.get(token).username == "ayse"
        sessions.revoke(token)
        assert sessions.get(token) is None and sessions.get(None) is None

    def test_expiry(self, monkeypatch):
        sessions = Sessions()
        token = sessions.create("ayse")
        monkeypatch.setattr(time, "time", lambda: sessions._items[token].last_seen + Sessions.IDLE + 1)
        assert sessions.get(token) is None

    def test_revoke_user_keeps_current(self):
        sessions = Sessions()
        keep, other = sessions.create("ayse"), sessions.create("ayse")
        sessions.revoke_user("ayse", keep=keep)
        assert sessions.get(keep) and sessions.get(other) is None


def test_throttle():
    throttle = LoginThrottle()
    for _ in range(LoginThrottle.LIMIT - 1):
        throttle.failure("u:ayse")
    assert throttle.blocked_for("u:ayse") == 0
    throttle.failure("u:ayse")
    assert throttle.blocked_for("u:ayse") > 0
    throttle.success("u:ayse")
    assert throttle.blocked_for("u:ayse") == 0
