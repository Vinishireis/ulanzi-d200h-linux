"""Small, atomic SQLite profile store."""
from __future__ import annotations
import json
import os
import re
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
    # Let the hosted editor (HOSTED_ORIGINS in api.py) use this computer's API.
    # Off until the user allows it locally: `deckforge online on` or Configurações.
    "online_editor": False,
}
# 13 keys plus the wide display (2_3 + 2_4, HID slot 13), which has an action
# and a display mode of its own.
CONFIGURABLE_KEYS = {f"{r}_{c}" for r in range(3) for c in range(5) if not (r == 2 and c > 2)} | {"2_3"}
# Every profile has a home page plus up to MAX_PAGES extra pages, each with its
# own 13 keys. The wide display (clock/statistics) is shared by all pages, so
# it is always stored on the home page.
HOME_PAGE = "home"
HOME_PAGE_NAME = "Início"
WIDE_KEY = "2_3"
MAX_PAGES = 30
_HISTORY_LIMIT = 20
PAGE_TARGETS = {"back", "home", "next", "previous"}
# Placed on 0_0 of a new page so it never becomes a dead end on the device.
BACK_KEY = {"type": "page", "title": "Voltar", "page": "back", "color": "#252936",
            "icon": "pack:streamline:arrow-round-left-solid"}


def _history(entries: list[str], current: str) -> list[str]:
    """Bounded back-history that never ends on the page being shown."""
    entries = entries[-_HISTORY_LIMIT:]
    while entries and entries[-1] == current:
        entries.pop()
    return entries


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
                CREATE TABLE IF NOT EXISTS buttons(profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE, page_id TEXT NOT NULL DEFAULT 'home', key TEXT NOT NULL, config TEXT NOT NULL, PRIMARY KEY(profile_id,page_id,key));
                CREATE TABLE IF NOT EXISTS pages(profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE, id TEXT NOT NULL, name TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(profile_id,id));
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            self._migrate(db)
            if db.execute("SELECT 1 FROM profiles LIMIT 1").fetchone() is None:
                db.execute("INSERT INTO profiles(id,name,active) VALUES('default','Meu primeiro perfil',1)")
                db.executemany("INSERT INTO buttons(profile_id,key,config) VALUES('default',?,?)", [(k, json.dumps(v, ensure_ascii=False)) for k, v in STARTER_ACTIONS.items()])

    @staticmethod
    def _migrate(db):
        """Upgrade databases created before pages existed; their keys become the home page."""
        if "page_id" not in {row["name"] for row in db.execute("PRAGMA table_info(buttons)")}:
            db.executescript("""
                CREATE TABLE buttons_paged(profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE, page_id TEXT NOT NULL DEFAULT 'home', key TEXT NOT NULL, config TEXT NOT NULL, PRIMARY KEY(profile_id,page_id,key));
                INSERT INTO buttons_paged(profile_id,page_id,key,config) SELECT profile_id,'home',key,config FROM buttons;
                DROP TABLE buttons;
                ALTER TABLE buttons_paged RENAME TO buttons;
            """)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(profiles)")}
        if "page" not in columns:
            db.execute("ALTER TABLE profiles ADD COLUMN page TEXT NOT NULL DEFAULT 'home'")
        if "page_history" not in columns:
            db.execute("ALTER TABLE profiles ADD COLUMN page_history TEXT NOT NULL DEFAULT '[]'")

    def profiles(self):
        with self._connect() as db:
            return [dict(r) for r in db.execute("SELECT id,name,created_at,active FROM profiles ORDER BY active DESC,name")]

    def active_profile(self):
        with self._connect() as db:
            row = db.execute("SELECT id,name FROM profiles WHERE active=1 LIMIT 1").fetchone()
            return dict(row) if row else None

    def _target(self, db, profile_id: str | None, page_id: str | None, key: str | None = None) -> tuple[str | None, str]:
        """Profile and page a read or write applies to.

        By default the page shown on the device; another profile is not on the
        device, so it defaults to its home page. The wide display lives on the
        home page. A page named explicitly must exist.
        """
        if profile_id:
            row = db.execute("SELECT active,page FROM profiles WHERE id=?", (profile_id,)).fetchone()
            profile, shown = profile_id, (row["page"] if row and row["active"] else HOME_PAGE)
        else:
            row = db.execute("SELECT id,page FROM profiles WHERE active=1 LIMIT 1").fetchone()
            if row is None:
                return None, HOME_PAGE
            profile, shown = row["id"], row["page"]
        if key == WIDE_KEY:
            return profile, HOME_PAGE
        if page_id is not None:
            if not self._page_exists(db, profile, page_id):
                raise ValueError("Página não encontrada")
            return profile, page_id
        # A page deleted by another process falls back home instead of failing.
        return profile, shown if self._page_exists(db, profile, shown) else HOME_PAGE

    @staticmethod
    def _page_exists(db, profile_id: str, page_id: str) -> bool:
        return page_id == HOME_PAGE or db.execute("SELECT 1 FROM pages WHERE profile_id=? AND id=?", (profile_id, page_id)).fetchone() is not None

    def buttons(self, profile_id: str | None = None, page_id: str | None = None):
        """Keys of one page (the one on the device by default) plus the shared wide display."""
        with self._connect() as db:
            profile, page = self._target(db, profile_id, page_id)
            if not profile: return {}
            rows = db.execute("SELECT key,config FROM buttons WHERE profile_id=? AND (page_id=? OR (page_id=? AND key=?))",
                              (profile, page, HOME_PAGE, WIDE_KEY))
            return {r["key"]: json.loads(r["config"]) for r in rows}

    def save_button(self, key: str, config: dict[str, Any], profile_id: str | None = None, page_id: str | None = None):
        if key not in CONFIGURABLE_KEYS:
            raise ValueError("Posição não configurável no D200H")
        with self._connect() as db:
            profile, page = self._target(db, profile_id, page_id, key)
            if not profile: raise ValueError("Nenhum perfil ativo")
            db.execute("INSERT INTO buttons(profile_id,page_id,key,config) VALUES(?,?,?,?) ON CONFLICT(profile_id,page_id,key) DO UPDATE SET config=excluded.config",
                       (profile, page, key, json.dumps(config, ensure_ascii=False)))

    def pages(self, profile_id: str | None = None) -> list[dict[str, Any]]:
        """Pages of a profile in navigation order, home first, with their configured key count."""
        with self._connect() as db:
            profile, _ = self._target(db, profile_id, None)
            if not profile: return []
            counts = {r["page_id"]: r["n"] for r in db.execute(
                "SELECT page_id, COUNT(*) AS n FROM buttons WHERE profile_id=? AND key<>? GROUP BY page_id", (profile, WIDE_KEY))}
            rows = db.execute("SELECT id,name FROM pages WHERE profile_id=? ORDER BY position,rowid", (profile,)).fetchall()
        return [{"id": HOME_PAGE, "name": HOME_PAGE_NAME, "keys": counts.get(HOME_PAGE, 0)},
                *({"id": r["id"], "name": r["name"], "keys": counts.get(r["id"], 0)} for r in rows)]

    def active_page(self) -> dict[str, Any]:
        """Page currently shown on the device for the active profile."""
        with self._connect() as db:
            profile, page = self._target(db, None, None)
            history = json.loads(db.execute("SELECT page_history FROM profiles WHERE id=?", (profile,)).fetchone()["page_history"]) if profile else []
        name = next((p["name"] for p in self.pages() if p["id"] == page), HOME_PAGE_NAME)
        return {"id": page, "name": name, "history": history}

    def create_page(self, name: str, profile_id: str | None = None, page_id: str | None = None,
                    back_key: bool = True) -> str:
        import uuid
        name = name.strip()[:40]
        if not name:
            raise ValueError("Informe o nome da página")
        with self._connect() as db:
            profile, _ = self._target(db, profile_id, None)
            if not profile: raise ValueError("Nenhum perfil ativo")
            if db.execute("SELECT COUNT(*) FROM pages WHERE profile_id=?", (profile,)).fetchone()[0] >= MAX_PAGES:
                raise ValueError(f"Cada perfil aceita até {MAX_PAGES} páginas além da inicial")
            pid = page_id or uuid.uuid4().hex
            if (not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", pid) or pid == HOME_PAGE or pid in PAGE_TARGETS
                    or self._page_exists(db, profile, pid)):
                raise ValueError("Identificador de página inválido ou repetido")
            position = db.execute("SELECT COALESCE(MAX(position),0)+1 FROM pages WHERE profile_id=?", (profile,)).fetchone()[0]
            db.execute("INSERT INTO pages(profile_id,id,name,position) VALUES(?,?,?,?)", (profile, pid, name, position))
            if back_key:
                db.execute("INSERT INTO buttons(profile_id,page_id,key,config) VALUES(?,?,?,?)",
                           (profile, pid, "0_0", json.dumps(BACK_KEY, ensure_ascii=False)))
        return pid

    def rename_page(self, page_id: str, name: str):
        name = name.strip()[:40]
        if page_id == HOME_PAGE:
            raise ValueError("A página inicial não pode ser renomeada")
        if not name:
            raise ValueError("Informe o nome da página")
        with self._connect() as db:
            profile, _ = self._target(db, None, None)
            if db.execute("UPDATE pages SET name=? WHERE profile_id=? AND id=?", (name, profile, page_id)).rowcount == 0:
                raise ValueError("Página não encontrada")

    def delete_page(self, page_id: str):
        """Remove a page and its keys; the device returns home if it was showing it."""
        if page_id == HOME_PAGE:
            raise ValueError("A página inicial não pode ser excluída")
        with self._connect() as db:
            profile, _ = self._target(db, None, None)
            if db.execute("DELETE FROM pages WHERE profile_id=? AND id=?", (profile, page_id)).rowcount == 0:
                raise ValueError("Página não encontrada")
            db.execute("DELETE FROM buttons WHERE profile_id=? AND page_id=?", (profile, page_id))
            row = db.execute("SELECT page,page_history FROM profiles WHERE id=?", (profile,)).fetchone()
            page = HOME_PAGE if row["page"] == page_id else row["page"]
            history = _history([p for p in json.loads(row["page_history"]) if p != page_id], page)
            db.execute("UPDATE profiles SET page=?, page_history=? WHERE id=?", (page, json.dumps(history), profile))

    def navigate_page(self, target: str) -> dict[str, Any]:
        """Show another page of the active profile: a page id, or back/home/next/previous.

        Visited pages are kept in a short history so "back" behaves like a
        folder's back key; "home" starts the history over.
        """
        with self._connect() as db:
            profile, current = self._target(db, None, None)
            if not profile: raise ValueError("Nenhum perfil ativo")
            order = [HOME_PAGE, *(r["id"] for r in db.execute("SELECT id FROM pages WHERE profile_id=? ORDER BY position,rowid", (profile,)))]
            history = [p for p in json.loads(db.execute("SELECT page_history FROM profiles WHERE id=?", (profile,)).fetchone()["page_history"]) if p in order]
            if target == "back":
                page = history.pop() if history else HOME_PAGE
            elif target == "home":
                page, history = HOME_PAGE, []
            elif target in {"next", "previous"}:
                page = order[(order.index(current) + (1 if target == "next" else -1)) % len(order)]
            elif target in order:
                page = target
            else:
                raise ValueError("A página de destino não existe mais neste perfil")
            if target not in {"back", "home"} and page != current:
                history.append(current)
            history = _history(history, page)
            db.execute("UPDATE profiles SET page=?, page_history=? WHERE id=?", (page, json.dumps(history), profile))
        return self.active_page()

    def reset_page(self):
        """Back to the home page, e.g. when the daemon starts after a boot."""
        with self._connect() as db:
            db.execute("UPDATE profiles SET page=?, page_history='[]' WHERE active=1", (HOME_PAGE,))

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
            # A profile always opens on its home page.
            db.execute("UPDATE profiles SET active=1, page=?, page_history='[]' WHERE id=?", (HOME_PAGE, profile_id))

    def delete_button(self, key: str, profile_id: str | None = None, page_id: str | None = None):
        with self._connect() as db:
            profile, page = self._target(db, profile_id, page_id, key)
            if not profile: return
            db.execute("DELETE FROM buttons WHERE profile_id=? AND page_id=? AND key=?", (profile, page, key))

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
        pages = [{"id": p["id"], "name": p["name"], "buttons": self._page_buttons(row["id"], p["id"])}
                 for p in self.pages(row["id"]) if p["id"] != HOME_PAGE]
        return {"format": "deckforge-profile", "version": 2, "name": row["name"],
                "buttons": self._page_buttons(row["id"], HOME_PAGE), "pages": pages}

    def _page_buttons(self, profile_id: str, page_id: str) -> dict[str, Any]:
        with self._connect() as db:
            return {r["key"]: json.loads(r["config"]) for r in db.execute(
                "SELECT key,config FROM buttons WHERE profile_id=? AND page_id=?", (profile_id, page_id))}

    def import_profile(self, name: str, buttons: dict[str, dict[str, Any]], pages: list[dict[str, Any]] = ()) -> str:
        """Create a profile from an export; page ids are kept so "open page" keys still point to them."""
        profile_id = self.create_profile(name)
        for key, config in buttons.items():
            self.save_button(key, config, profile_id, HOME_PAGE)
        for page in pages:
            page_id = self.create_page(page["name"], profile_id, page.get("id"), back_key=False)
            for key, config in page.get("buttons", {}).items():
                if key != WIDE_KEY:
                    self.save_button(key, config, profile_id, page_id)
        return profile_id

    def recent_events(self, limit: int = 25):
        with self._connect() as db:
            return [dict(r) for r in db.execute("SELECT id,kind,message,created_at FROM events ORDER BY id DESC LIMIT ?", (limit,))]

