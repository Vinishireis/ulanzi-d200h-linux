import isolation  # noqa: F401  (must precede deckforge imports)
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from deckforge import api
from deckforge.actions import ActionError, normalize_action
from deckforge.daemon import run_action, switch_page
from deckforge.store import BACK_KEY, HOME_PAGE, ProfileStore


class PageStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ProfileStore(Path(self.tmp.name) / "p.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_page_has_its_own_keys_and_a_back_key(self):
        home_keys = self.store.buttons()
        media = self.store.create_page("Mídia")
        self.store.navigate_page(media)
        self.assertEqual(self.store.buttons()["0_0"], BACK_KEY)
        self.store.save_button("1_1", {"type": "keys", "title": "Play", "keys": ["playpause"]})
        self.store.navigate_page("home")
        self.assertEqual(self.store.buttons(), home_keys)  # home untouched by the page's keys
        self.assertEqual([(p["name"], p["keys"]) for p in self.store.pages()], [("Início", 13), ("Mídia", 2)])

    def test_wide_display_is_shared_by_every_page(self):
        media = self.store.create_page("Mídia")
        self.store.navigate_page(media)
        self.store.save_button("2_3", {"type": "none", "title": "", "display": {"mode": "stats"}})
        self.store.navigate_page("home")
        self.assertEqual(self.store.buttons()["2_3"]["display"], {"mode": "stats"})
        self.store.navigate_page(media)
        self.assertEqual(self.store.buttons()["2_3"]["display"], {"mode": "stats"})

    def test_back_returns_through_nested_folders_and_home_clears_history(self):
        apps = self.store.create_page("Apps")
        games = self.store.create_page("Jogos")
        self.store.navigate_page(apps)
        self.store.navigate_page(games)
        self.assertEqual(self.store.navigate_page("back")["id"], apps)
        self.assertEqual(self.store.navigate_page("back")["id"], HOME_PAGE)
        self.assertEqual(self.store.navigate_page("back")["id"], HOME_PAGE)  # nothing left: stays home
        self.store.navigate_page(games)
        self.assertEqual(self.store.navigate_page("home"), {"id": HOME_PAGE, "name": "Início", "history": []})

    def test_next_and_previous_wrap_around_in_page_order(self):
        apps = self.store.create_page("Apps")
        games = self.store.create_page("Jogos")
        self.assertEqual(self.store.navigate_page("next")["id"], apps)
        self.assertEqual(self.store.navigate_page("next")["id"], games)
        self.assertEqual(self.store.navigate_page("next")["id"], HOME_PAGE)
        self.assertEqual(self.store.navigate_page("previous")["id"], games)

    def test_deleting_the_shown_page_returns_home_and_drops_its_keys(self):
        media = self.store.create_page("Mídia")
        self.store.navigate_page(media)
        self.store.delete_page(media)
        self.assertEqual(self.store.active_page()["id"], HOME_PAGE)
        self.assertEqual([p["id"] for p in self.store.pages()], [HOME_PAGE])
        with self.assertRaises(ValueError):
            self.store.navigate_page(media)
        with self.assertRaises(ValueError):
            self.store.delete_page(HOME_PAGE)

    def test_profile_switch_and_daemon_start_open_the_home_page(self):
        media = self.store.create_page("Mídia")
        self.store.navigate_page(media)
        self.store.reset_page()
        self.assertEqual(self.store.active_page()["id"], HOME_PAGE)
        self.store.navigate_page(media)
        first = self.store.active_profile()["id"]
        other = self.store.create_profile("Outro")
        self.store.activate_profile(other)
        self.store.activate_profile(first)
        self.assertEqual(self.store.active_page()["id"], HOME_PAGE)

    def test_export_and_import_keep_pages_and_the_keys_that_open_them(self):
        media = self.store.create_page("Mídia")
        self.store.save_button("0_1", {"type": "page", "title": "Mídia", "page": media})
        self.store.navigate_page(media)
        self.store.save_button("1_1", {"type": "keys", "title": "Play", "keys": ["playpause"]})
        exported = self.store.export_profile()
        self.assertEqual(exported["version"], 2)
        copy = self.store.import_profile("Cópia", exported["buttons"], exported["pages"])
        self.store.activate_profile(copy)
        self.assertEqual(self.store.pages()[1]["id"], media)
        self.store.navigate_page(self.store.buttons()["0_1"]["page"])
        self.assertEqual(self.store.buttons()["1_1"]["title"], "Play")

    def test_database_from_before_pages_moves_its_keys_to_the_home_page(self):
        path = Path(self.tmp.name) / "old.sqlite3"
        db = sqlite3.connect(path)
        db.executescript("""
            CREATE TABLE profiles(id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, active INTEGER DEFAULT 0);
            CREATE TABLE buttons(profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE, key TEXT NOT NULL, config TEXT NOT NULL, PRIMARY KEY(profile_id,key));
            INSERT INTO profiles(id,name,active) VALUES('default','Antigo',1);
        """)
        db.execute("INSERT INTO buttons VALUES('default','0_0',?)", (json.dumps({"type": "keys", "title": "Copiar", "keys": ["ctrl", "c"]}),))
        db.commit(); db.close()
        store = ProfileStore(path)
        self.assertEqual(store.buttons()["0_0"]["title"], "Copiar")
        self.assertEqual(store.active_page()["id"], HOME_PAGE)


class PageActionTests(unittest.TestCase):
    def test_page_key_switches_page_and_redraws_the_lcd(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp) / "p.sqlite3")
            media = store.create_page("Mídia")
            store.save_button("0_4", {"type": "page", "title": "Mídia", "page": media})
            redraws = []
            self.assertEqual(run_action(store, "0_4", "teste", on_layout_change=redraws.append), (True, "Página: Mídia"))
            self.assertEqual(redraws, ["troca de página"])
            self.assertEqual(run_action(store, "0_0", "teste", on_layout_change=redraws.append), (True, "Página: Início"))
            self.assertEqual(store.active_page()["id"], HOME_PAGE)

    def test_key_pointing_to_a_deleted_page_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp) / "p.sqlite3")
            with self.assertRaisesRegex(ActionError, "não existe mais"):
                switch_page(store, "apagada")

    def test_page_action_needs_a_target(self):
        self.assertEqual(normalize_action({"type": "page", "page": "back", "keys": ["x"]}), {"type": "page", "page": "back"})
        with self.assertRaises(ActionError):
            normalize_action({"type": "page"})


class PageApiTests(unittest.TestCase):
    def setUp(self):
        self.profile = api.store.create_profile("Teste páginas")
        api.store.activate_profile(self.profile)

    def test_editor_edits_the_page_shown_on_the_device(self):
        created = api.create_page(api.PageCreate(name="Mídia"))
        self.assertEqual(api.activate_page(created["id"])["name"], "Mídia")
        api.update_button("1_1", api.ButtonConfig(type="keys", title="Play", keys=["playpause"]))
        self.assertEqual(api.pages()["active"]["id"], created["id"])
        self.assertEqual(api.status()["page"]["name"], "Mídia")
        api.activate_page(HOME_PAGE)
        self.assertNotIn("1_1", api.store.buttons())
        self.assertEqual(api.delete_page(created["id"])["active"]["id"], HOME_PAGE)
        with self.assertRaises(HTTPException):
            api.delete_page(HOME_PAGE)

    def test_page_key_is_stored_with_its_target(self):
        api.update_button("0_1", api.ButtonConfig(type="page", title="Voltar", page="back", profile="x"))
        self.assertEqual(api.store.buttons()["0_1"], {"type": "page", "title": "Voltar", "page": "back", "color": "#252936"})


if __name__ == "__main__":
    unittest.main()
