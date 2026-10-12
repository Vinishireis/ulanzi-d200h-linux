import isolation  # noqa: F401  (must precede deckforge imports)
import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from deckforge import api
from deckforge.store import DB_PATH, ProfileStore


def save(key, **config):
    return api.update_button(key, api.ButtonConfig(**config))


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.profile = api.store.create_profile("Teste API")
        api.store.activate_profile(self.profile)

    def test_saved_action_and_icon_survive_a_restart(self):
        save("0_0", type="app", title="GitHub", argv=["xdg-open", "https://github.com"], keys=["ctrl", "c"],
             icon="streamline-logos-solid/developer-communities/github-logo-2.svg", color="#252936")
        reopened = ProfileStore(DB_PATH)  # what a restarted daemon/API reads
        self.assertEqual(reopened.active_profile()["id"], self.profile)
        self.assertEqual(reopened.buttons()["0_0"], {
            "type": "app", "title": "GitHub", "argv": ["xdg-open", "https://github.com"],
            "icon": "streamline-logos-solid/developer-communities/github-logo-2.svg", "color": "#252936"})

    def test_bare_url_is_stored_as_xdg_open_argv(self):
        save("0_3", type="app", title="YouTube", argv=["https://www.youtube.com/"])
        self.assertEqual(api.store.buttons()["0_3"]["argv"], ["xdg-open", "https://www.youtube.com/"])

    def test_invalid_shortcut_is_rejected_at_save_time(self):
        with self.assertRaises(HTTPException) as caught:
            save("1_1", type="keys", title="?", keys=["ctrl", "salvar"])
        self.assertEqual(caught.exception.status_code, 422)
        self.assertNotIn("1_1", api.store.buttons())

    def test_profile_rename_export_import_delete(self):
        save("2_0", type="keys", title="Desfazer", keys=["ctrl", "z"])
        api.rename_profile(self.profile, api.ProfileCreate(name="Renomeado"))
        exported = api.export_profile(self.profile)
        self.assertEqual(exported["name"], "Renomeado")
        created = api.import_profile(api.ProfileImport(name="Cópia", buttons=exported["buttons"]))
        self.assertEqual(ProfileStore(DB_PATH).buttons(created["id"]), exported["buttons"])
        with self.assertRaises(HTTPException):
            api.delete_profile(self.profile)  # active profile cannot be deleted
        api.delete_profile(created["id"])
        self.assertNotIn(created["id"], [p["id"] for p in api.profiles()])

    def test_settings_validation(self):
        self.assertEqual(api.update_settings(api.SettingsUpdate(log_keys=True))["log_keys"], True)
        self.assertTrue(ProfileStore(DB_PATH).settings()["log_keys"])
        api.update_settings(api.SettingsUpdate(log_keys=False))

    def test_test_button_runs_saved_action_and_reports_real_result(self):
        save("1_2", type="command", title="Falha", argv=["false"])
        result = api.test_button("1_2", api.TestRequest())
        self.assertEqual((result["ok"], result["scheduled"]), (False, False))
        self.assertIn("false terminou com código 1", result["message"])

    def test_macro_table_matches_decoder(self):
        table = api.macros()
        self.assertEqual(len(table), 14)  # 13 keys + the wide display (2_3)
        self.assertEqual(table["0_0"]["windows"], "Super+R · calc · Enter")
        self.assertEqual(table["1_4"]["hid_slot"], 9)
        self.assertEqual(table["2_1"]["windows"], "Ctrl+Z")


class StatusTests(unittest.TestCase):
    def test_stopped_daemon_is_never_reported_as_capturing(self):
        api.store.set_state("capture", {"state": "active", "message": "velho", "pid": 999999})
        with patch("deckforge.status.discover", return_value=[]):
            status = api.status()
        self.assertFalse(status["daemon"]["running"])
        self.assertEqual(status["runtime"]["capture"]["state"], "stopped")
        self.assertIn("build", status)


class FakeSocket:
    def __init__(self, close_after):
        self.url = type("U", (), {"hostname": "127.0.0.1"})()
        self.headers = {"origin": "http://127.0.0.1:8765"}
        self.sent, self.closed_code, self.close_after = [], None, close_after

    async def accept(self): pass
    async def close(self, code=1000): self.closed_code = code
    async def send_json(self, data): self.sent.append(data)

    async def receive(self):
        await asyncio.sleep(self.close_after)
        return {"type": "websocket.disconnect", "code": 1012}


class LiveSocketTests(unittest.TestCase):
    def test_handler_returns_when_client_disconnects_even_without_new_events(self):
        """Regression: the handler used to loop forever, blocking uvicorn's shutdown."""
        socket = FakeSocket(close_after=.2)

        async def run():
            await asyncio.wait_for(api.live(socket), timeout=3)
        asyncio.run(run())
        self.assertIsNone(socket.closed_code)


if __name__ == "__main__":
    unittest.main()
