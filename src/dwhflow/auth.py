"""Ekip kullanımı: kullanıcılar, roller, oturumlar.

* Parolalar yalnız PBKDF2-SHA256 özeti olarak saklanır (tuzlu, yüksek yinelemeli);
  düz parola hiçbir yere yazılmaz ve günlüğe girmez.
* Roller: ``izleyici`` (okur), ``duzenleyici`` (doküman yükler, düzeltir, onaylar,
  proje ayarı yapar), ``yonetici`` (kullanıcılar ve genel ayarlar dahil her şey).
* Oturumlar sunucu belleğindedir; çerez yalnız rastgele bir anahtar taşır
  (HttpOnly, SameSite=Strict). Sunucu yeniden başlarsa herkes yeniden giriş yapar.
* Art arda hatalı girişler kullanıcı adı ve adres başına geçici olarak kısıtlanır.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .project.store import StoreError, lock_for, read_yaml, write_yaml

ROLES = ("izleyici", "duzenleyici", "yonetici")
ROLE_LABELS = {"izleyici": "İzleyici", "duzenleyici": "Düzenleyici", "yonetici": "Yönetici"}
PBKDF2_ITERATIONS = 390_000
MIN_PASSWORD = 10
_USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")


def role_at_least(role: str, required: str) -> bool:
    return ROLES.index(role) >= ROLES.index(required)


def hash_password(password: str, *, iterations: int | None = None) -> str:
    iterations = iterations or PBKDF2_ITERATIONS
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt, digest = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


#: Kullanıcı yokken de aynı süreyi harcamak için (kullanıcı adı tahminini zorlaştırır).
_DUMMY_HASH = hash_password("dwhflow-yok-boyle-bir-kullanici", iterations=PBKDF2_ITERATIONS)


def check_password_policy(password: str, username: str = "") -> None:
    if len(password or "") < MIN_PASSWORD:
        raise StoreError(f"parola en az {MIN_PASSWORD} karakter olmalı")
    if username and username.lower() in password.lower():
        raise StoreError("parola kullanıcı adını içermemeli")
    classes = sum(bool(re.search(p, password)) for p in (r"[a-zçğıöşü]", r"[A-ZÇĞİÖŞÜ]", r"\d", r"[^\w\s]"))
    if classes < 2:
        raise StoreError("parola en az iki karakter türü içermeli (küçük/büyük harf, rakam, işaret)")


class UserStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        # Her istekte oturum sahibini doğrulamak için okunur; dosya değişmedikçe bellekten.
        self._cache: tuple[float, int, dict[str, dict[str, Any]]] | None = None

    @property
    def _lock(self) -> threading.RLock:
        return lock_for(str(self.path))

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return {}
        if self._cache and self._cache[:2] == (stat.st_mtime, stat.st_size):
            return {name: dict(record) for name, record in self._cache[2].items()}
        data = read_yaml(self.path, {}) or {}
        users = data.get("kullanicilar", {}) if isinstance(data, dict) else {}
        self._cache = (stat.st_mtime, stat.st_size, users)
        return {name: dict(record) for name, record in users.items()}

    def _save(self, users: dict[str, dict[str, Any]]) -> None:
        write_yaml(self.path, {"kullanicilar": users})
        self._cache = None

    @staticmethod
    def public(username: str, record: dict[str, Any]) -> dict[str, Any]:
        return {"username": username, "name": record.get("ad") or username, "role": record.get("rol"),
                "active": record.get("aktif", True), "created_at": record.get("olusturma"),
                "last_login": record.get("son_giris")}

    def empty(self) -> bool:
        return not self._load()

    def list(self) -> list[dict[str, Any]]:
        return [self.public(u, r) for u, r in sorted(self._load().items())]

    def get(self, username: str) -> dict[str, Any] | None:
        record = self._load().get(username)
        return self.public(username, record) if record else None

    def create(self, username: str, name: str, role: str, password: str) -> dict[str, Any]:
        username = (username or "").strip().lower()
        if not _USERNAME.match(username):
            raise StoreError("kullanıcı adı 3–32 karakter; küçük harf, rakam, nokta, tire, alt çizgi")
        if role not in ROLES:
            raise StoreError(f"rol şunlardan biri olmalı: {', '.join(ROLES)}")
        check_password_policy(password, username)
        with self._lock:
            users = self._load()
            if username in users:
                raise StoreError("bu kullanıcı adı zaten var")
            users[username] = {"ad": (name or username).strip()[:80], "rol": role,
                               "parola": hash_password(password), "aktif": True,
                               "olusturma": datetime.now().isoformat(timespec="seconds")}
            self._save(users)
            return self.public(username, users[username])

    def update(self, username: str, *, name: str | None = None, role: str | None = None,
               active: bool | None = None, password: str | None = None) -> dict[str, Any]:
        with self._lock:
            users = self._load()
            record = users.get(username)
            if record is None:
                raise LookupError("kullanıcı bulunamadı")
            if role is not None:
                if role not in ROLES:
                    raise StoreError(f"rol şunlardan biri olmalı: {', '.join(ROLES)}")
                if record["rol"] == "yonetici" and role != "yonetici" and self._admins(users) == 1:
                    raise StoreError("son yöneticinin rolü değiştirilemez")
                record["rol"] = role
            if active is not None:
                if not active and record["rol"] == "yonetici" and self._admins(users) == 1:
                    raise StoreError("son yönetici devre dışı bırakılamaz")
                record["aktif"] = bool(active)
            if name is not None:
                record["ad"] = name.strip()[:80] or username
            if password is not None:
                check_password_policy(password, username)
                record["parola"] = hash_password(password)
            self._save(users)
            return self.public(username, record)

    def delete(self, username: str) -> None:
        with self._lock:
            users = self._load()
            record = users.get(username)
            if record is None:
                raise LookupError("kullanıcı bulunamadı")
            if record["rol"] == "yonetici" and self._admins(users) == 1:
                raise StoreError("son yönetici silinemez")
            del users[username]
            self._save(users)

    @staticmethod
    def _admins(users: dict[str, dict[str, Any]]) -> int:
        return sum(1 for r in users.values() if r.get("rol") == "yonetici" and r.get("aktif", True))

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        username = (username or "").strip().lower()
        record = self._load().get(username)
        stored = record["parola"] if record else _DUMMY_HASH
        valid = verify_password(password or "", stored)
        if not (record and valid and record.get("aktif", True)):
            return None
        with self._lock:
            users = self._load()
            if username in users:
                users[username]["son_giris"] = datetime.now().isoformat(timespec="seconds")
                self._save(users)
        return self.public(username, record)


@dataclass
class Session:
    token: str
    username: str
    created: float
    last_seen: float


class Sessions:
    """Bellek içi oturumlar: en çok 12 saat, 2 saat hareketsizlikte düşer."""

    MAX_AGE = 12 * 3600
    IDLE = 2 * 3600

    def __init__(self) -> None:
        self._items: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._items[token] = Session(token, username, now, now)
        return token

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        now = time.time()
        with self._lock:
            session = self._items.get(token)
            if session is None:
                return None
            if now - session.created > self.MAX_AGE or now - session.last_seen > self.IDLE:
                self._items.pop(token, None)
                return None
            session.last_seen = now
            return session

    def revoke(self, token: str) -> None:
        with self._lock:
            self._items.pop(token, None)

    def revoke_user(self, username: str, *, keep: str | None = None) -> None:
        with self._lock:
            for token in [t for t, s in self._items.items() if s.username == username and t != keep]:
                self._items.pop(token, None)


class LoginThrottle:
    """10 dakikada 5 hatalı giriş → 5 dakika bekletme (kullanıcı adı ve adres başına ayrı)."""

    WINDOW, LIMIT, BLOCK = 600, 5, 300

    def __init__(self) -> None:
        self._failures: dict[str, list[float]] = {}
        self._blocked: dict[str, float] = {}
        self._lock = threading.Lock()

    def blocked_for(self, *keys: str) -> int:
        now = time.time()
        with self._lock:
            waits = [int(self._blocked[k] - now) for k in keys if self._blocked.get(k, 0) > now]
        return max(waits, default=0)

    def failure(self, *keys: str) -> None:
        now = time.time()
        with self._lock:
            for key in keys:
                recent = [t for t in self._failures.get(key, []) if now - t < self.WINDOW] + [now]
                self._failures[key] = recent
                if len(recent) >= self.LIMIT:
                    self._blocked[key] = now + self.BLOCK
                    self._failures[key] = []

    def success(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)
                self._blocked.pop(key, None)
