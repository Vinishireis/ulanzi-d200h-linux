"""Small, atomic SQLite profile store."""
from __future__ import annotations
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "deckforge"
DB_PATH = DATA_HOME / "deckforge.sqlite3"

STARTER_ACTIONS = {
    "0_0": {"type": "app", "title": "Terminal", "argv": ["x-terminal-emulator"]},
    "0_1": {"type": "keys", "title": "Copiar", "keys": ["ctrl", "c"]},
    "0_2": {"type": "keys", "title": "Colar", "keys": ["ctrl", "v"]},
    "0_3": {"type": "app", "title": "Navegador", "argv": ["xdg-open", "https://www.google.com"]},
    "0_4": {"type": "keys", "title": "Alternar janela", "keys": ["alt", "Tab"]},
    "1_0": {"type": "keys", "title": "Nova aba", "keys": ["ctrl", "shift", "t"]},
    "1_1": {"type": "keys", "title": "Salvar", "keys": ["ctrl", "s"]},
    "1_2": {"type": "keys", "title": "Captura de tela", "keys": ["Print"]},
    "1_3": {"type": "keys", "title": "Mostrar área de trabalho", "keys": ["super", "d"]},
    "1_4": {"type": "app", "title": "Arquivos", "argv": ["xdg-open", str(Path.home())]},
    "2_0": {"type": "keys", "title": "Desfazer", "keys": ["ctrl", "z"]},
    "2_1": {"type": "keys", "title": "Refazer", "keys": ["ctrl", "shift", "z"]},
    "2_2": {"type": "keys", "title": "Fechar janela", "keys": ["alt", "F4"]},
}


DEFAULT_SETTINGS = {
    # Upload icons when the D200H connects and keep the firmware in host mode
    # (clock keep-alive every 2 s). Off: the device stays in its standalone
    # keyboard-macro mode with the factory screen.
    "host_mode": True,
    # Record every raw key of the D200H keyboard interface in the event log.
    "log_keys": False,
    # LCD backlight 0-100 sent to the firmware; -1 leaves the device setting alone.
    "brightness": -1,
}
# 13 keys plus the wide display (2_3 + 2_4, HID slot 13), which has an action
# and a display mode of its own.
CONFIGURABLE_KEYS = {f"{r}_{c}" for r in range(3) for c in range(5) if not (r == 2 and c > 2)} | {"2_3"}


class ProfileStore:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._init()
        try: os.chmod(self.path, 0o600)
        except OSError: pass

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _init(self):
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, active INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS buttons(profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE, key TEXT NOT NULL, config TEXT NOT NULL, PRIMARY KEY(profile_id,key));
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            if db.execute("SELECT 1 FROM profiles LIMIT 1").fetchone() is None:
                db.execute("INSERT INTO profiles(id,name,active) VALUES('default','Meu primeiro perfil',1)")
                db.executemany("INSERT INTO buttons(profile_id,key,config) VALUES('default',?,?)", [(k, json.dumps(v, ensure_ascii=False)) for k, v in STARTER_ACTIONS.items()])

    def profiles(self):
        with self._connect() as db:
            return [dict(r) for r in db.execute("SELECT id,name,created_at,active FROM profiles ORDER BY active DESC,name")]

    def active_profile(self):
        with self._connect() as db:
            row = db.execute("SELECT id,name FROM profiles WHERE active=1 LIMIT 1").fetchone()
            return dict(row) if row else None

    def buttons(self, profile_id: str | None = None):
        profile = profile_id or (self.active_profile() or {}).get("id")
        if not profile: return {}
        with self._connect() as db:
            return {r["key"]: json.loads(r["config"]) for r in db.execute("SELECT key,config FROM buttons WHERE profile_id=?", (profile,))}

    def save_button(self, key: str, config: dict[str, Any], profile_id: str | None = None):
        if key not in CONFIGURABLE_KEYS:
            raise ValueError("Posição não configurável no D200H")
        profile = profile_id or (self.active_profile() or {}).get("id")
        if not profile: raise ValueError("Nenhum perfil ativo")
        with self._connect() as db:
            db.execute("INSERT INTO buttons(profile_id,key,config) VALUES(?,?,?) ON CONFLICT(profile_id,key) DO UPDATE SET config=excluded.config", (profile, key, json.dumps(config, ensure_ascii=False)))

    def create_profile(self, name: str):
        import uuid
        pid = uuid.uuid4().hex
        with self._connect() as db:
            db.execute("INSERT INTO profiles(id,name) VALUES(?,?)", (pid, name[:80]))
        return pid

    def activate_profile(self, profile_id: str):
        with self._connect() as db:
            if not db.execute("SELECT 1 FROM profiles WHERE id=?", (profile_id,)).fetchone():
                raise ValueError("Perfil não encontrado")
            db.execute("UPDATE profiles SET active=0")
            db.execute("UPDATE profiles SET active=1 WHERE id=?", (profile_id,))

    def delete_button(self, key: str, profile_id: str | None = None):
        profile = profile_id or (self.active_profile() or {}).get("id")
        if not profile: return
        with self._connect() as db:
            db.execute("DELETE FROM buttons WHERE profile_id=? AND key=?", (profile, key))

    def log(self, kind: str, message: str):
        with self._connect() as db:
            event_id = db.execute("INSERT INTO events(kind,message) VALUES(?,?)", (kind[:32], message[:500])).lastrowid
            if event_id and event_id % 500 == 0:
                db.execute("DELETE FROM events WHERE id <= ?", (event_id - 5000,))

    def set_state(self, key: str, value: dict[str, Any]):
        """Record live daemon/API state (capture, last action, LCD upload)."""
        with self._connect() as db:
            db.execute("INSERT INTO state(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                       (key, json.dumps(value, ensure_ascii=False), time.time()))

    def get_state(self) -> dict[str, Any]:
        with self._connect() as db:
            return {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT key,value FROM state")}

    def settings(self) -> dict[str, Any]:
        with self._connect() as db:
            saved = {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT key,value FROM settings")}
        return {**DEFAULT_SETTINGS, **{k: v for k, v in saved.items() if k in DEFAULT_SETTINGS}}

    def save_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        unknown = set(values) - set(DEFAULT_SETTINGS)
        if unknown:
            raise ValueError(f"Configuração desconhecida: {', '.join(sorted(unknown))}")
        with self._connect() as db:
            for key, value in values.items():
                if type(value) is not type(DEFAULT_SETTINGS[key]):
                    raise ValueError(f"Valor inválido para {key}")
                if key == "brightness" and not -1 <= value <= 100:
                    raise ValueError("Brilho deve estar entre 0 e 100 (ou -1 para não alterar).")
                db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (key, json.dumps(value)))
        return self.settings()

    def rename_profile(self, profile_id: str, name: str):
        with self._connect() as db:
            if db.execute("UPDATE profiles SET name=? WHERE id=?", (name[:80], profile_id)).rowcount == 0:
                raise ValueError("Perfil não encontrado")

    def delete_profile(self, profile_id: str):
        with self._connect() as db:
            row = db.execute("SELECT active FROM profiles WHERE id=?", (profile_id,)).fetchone()
            if row is None:
                raise ValueError("Perfil não encontrado")
            if row["active"]:
                raise ValueError("Ative outro perfil antes de excluir o perfil ativo")
            db.execute("DELETE FROM profiles WHERE id=?", (profile_id,))

    def export_profile(self, profile_id: str | None = None) -> dict[str, Any]:
        profile = profile_id or (self.active_profile() or {}).get("id")
        with self._connect() as db:
            row = db.execute("SELECT id,name FROM profiles WHERE id=?", (profile,)).fetchone()
        if row is None:
            raise ValueError("Perfil não encontrado")
        return {"format": "deckforge-profile", "version": 1, "name": row["name"], "buttons": self.buttons(row["id"])}

    def import_profile(self, name: str, buttons: dict[str, dict[str, Any]]) -> str:
        profile_id = self.create_profile(name)
        for key, config in buttons.items():
            self.save_button(key, config, profile_id)
        return profile_id

    def recent_events(self, limit: int = 25):
        with self._connect() as db:
            return [dict(r) for r in db.execute("SELECT id,kind,message,created_at FROM events ORDER BY id DESC LIMIT ?", (limit,))]

