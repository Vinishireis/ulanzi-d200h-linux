import isolation  # noqa: F401  (must precede deckforge imports)
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deckforge import api, lcd_sync
from deckforge.actions import ActionError, execute_action, normalize_action
from deckforge.daemon import ControlChannel, run_action, switch_profile
from deckforge.lcd_sync import brightness_frame, display_settings, small_window_payload, sync_icons
from deckforge.metrics import SystemMetrics
from deckforge.store import ProfileStore

GNOME = {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME", "XDG_RUNTIME_DIR": "/run/user/1000"}
OK = subprocess.CompletedProcess([], 0, b"", b"")


def ydotool_only(name):
    return "/usr/bin/ydotool" if name == "ydotool" else None


class WideDisplayProtocol(unittest.TestCase):
    def test_small_window_payload_for_each_mode(self):
        firmware = {"clock_format": "24h", "renderer": "firmware"}
        self.assertRegex(small_window_payload({"mode": "clock", **firmware}).decode(), r"^1\|0\|0\|\d\d:\d\d:\d\d\|0$")
        self.assertEqual(small_window_payload({"mode": "stats", **firmware}, {"cpu": 12, "mem": 40, "gpu": 3}, now=0).decode()[:7], "0|12|40")
        self.assertEqual(small_window_payload({"mode": "image"}), b"2|0|0|00:00:00|0")
        # Default: DeckForge draws the clock, so the firmware stays on BACKGROUND (no Ulanzi branding).
        self.assertEqual(small_window_payload(None), b"2|0|0|00:00:00|0")
        self.assertEqual(display_settings(None), {"mode": "clock", "clock_format": "24h", "renderer": "deckforge", "show_date": True, "style": "classic"})
        self.assertEqual(display_settings({"display": {"mode": "bogus"}})["mode"], "clock")

    def test_brightness_is_ascii_digits(self):
        frame = brightness_frame(55)
        self.assertEqual(frame[:4], b"||\x00\x0a")
        self.assertEqual(frame[8:10], b"55")
        with self.assertRaises(ValueError):
            brightness_frame(101)

    def test_image_mode_uploads_wide_display_as_partial_update_after_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp) / "p.sqlite3")
            store.save_button("2_3", {"type": "none", "title": "Foco", "color": "#101116", "display": {"mode": "image"}})
            sent = []
            with patch("deckforge.lcd_sync.d200_write_target", return_value=("/dev/hidraw-test", None)), \
                 patch("deckforge.lcd_sync._send", side_effect=lambda path, blob, command=lcd_sync.SET_BUTTONS: sent.append((command, blob)) or 1):
                result = sync_icons(store)
            self.assertEqual([c for c, _ in sent], [lcd_sync.SET_BUTTONS, lcd_sync.PARTIALLY_UPDATE_BUTTONS])
            import io, json, zipfile
            with zipfile.ZipFile(io.BytesIO(sent[1][1])) as archive:
                self.assertEqual(list(json.loads(archive.read("manifest.json"))), ["3_2"])
            with zipfile.ZipFile(io.BytesIO(sent[0][1])) as archive:
                self.assertNotIn("3_2", json.loads(archive.read("manifest.json")))
            self.assertIn("2_3", result["slots"])
            self.assertEqual(result["display"], "image")

    def test_firmware_clock_does_not_upload_an_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp) / "p.sqlite3")
            store.save_button("2_3", {"type": "none", "title": "Relógio", "display": {"mode": "clock", "renderer": "firmware"}})
            with patch("deckforge.lcd_sync.d200_write_target", return_value=("/dev/hidraw-test", None)), \
                 patch("deckforge.lcd_sync._send", return_value=1) as send:
                sync_icons(store)
            send.assert_called_once()


class KeepAlive(unittest.TestCase):
    def make(self, tmp):
        store = ProfileStore(Path(tmp) / "p.sqlite3")
        payloads = []

        class Dispatcher:
            def submit(self, *a, **k):
                return True
        channel = ControlChannel(store, Dispatcher(), keepalive=lambda path, payload: payloads.append(payload))
        return store, channel, payloads

    def test_keepalive_follows_the_wide_display_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, channel, payloads = self.make(tmp)
            try:
                with patch("deckforge.device.d200_write_target", return_value=("/dev/hidraw-test", None)):
                    store.save_button("2_3", {"type": "none", "display": {"mode": "clock", "renderer": "firmware"}})
                    channel._send_keepalive()
                    store.save_button("2_3", {"type": "none", "display": {"mode": "stats", "renderer": "firmware"}})
                    channel._display = (0.0, {})
                    channel._send_keepalive()
                    store.save_button("2_3", {"type": "none", "display": {"mode": "image"}})
                    channel._display = (0.0, {})
                    channel._send_keepalive()
            finally:
                channel.close()
            self.assertEqual([p[:2] for p in payloads], [b"1|", b"0|", b"2|"])
            self.assertEqual(store.get_state()["host_link"]["display"], "image")

    def test_brightness_is_sent_once_and_again_after_reconnect(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, channel, _ = self.make(tmp)
            channel._keepalive = None
            store.save_settings({"brightness": 40})
            writes = []
            try:
                with patch("deckforge.device.d200_write_target", return_value=("/dev/hidraw-test", None)), \
                     patch("deckforge.lcd_sync._send_frames", side_effect=lambda path, *frames: writes.append([f[2:4] for f in frames])):
                    channel._send_keepalive(); channel._send_keepalive()
                    channel._brightness_sent = None  # what a replug does
                    channel._send_keepalive()
            finally:
                channel.close()
            self.assertEqual(writes, [[b"\x00\x06", b"\x00\x0a"], [b"\x00\x06"], [b"\x00\x06", b"\x00\x0a"]])


class NewActions(unittest.TestCase):
    def test_media_keys_use_evdev_codes(self):
        with patch("deckforge.session.shutil.which", side_effect=ydotool_only), \
             patch("deckforge.session.socket_ready", return_value=True), \
             patch("deckforge.actions.subprocess.run", return_value=OK) as run:
            self.assertEqual(execute_action({"type": "keys", "keys": ["playpause"]}, env=GNOME), "Atalho playpause enviado via ydotool")
        self.assertEqual(run.call_args.args[0], ["/usr/bin/ydotool", "key", "164:1", "164:0"])

    def test_text_goes_through_stdin_and_ascii_is_enforced_for_ydotool(self):
        with patch("deckforge.session.shutil.which", side_effect=ydotool_only), \
             patch("deckforge.session.socket_ready", return_value=True), \
             patch("deckforge.actions.subprocess.run", return_value=OK) as run:
            execute_action({"type": "text", "text": "-c 'oi'; $(id)"}, env=GNOME)
            self.assertEqual(run.call_args.args[0], ["/usr/bin/ydotool", "type", "-d", "6", "-H", "6", "-f", "-"])
            self.assertEqual(run.call_args.kwargs["input"], b"-c 'oi'; $(id)")
            with self.assertRaisesRegex(ActionError, "ASCII"):
                execute_action({"type": "text", "text": "ação"}, env=GNOME)

    def test_multi_action_runs_in_order_and_stops_at_failure(self):
        steps = [{"action": {"type": "command", "argv": ["true"]}, "delay_ms": 0},
                 {"action": {"type": "command", "argv": ["false"]}, "delay_ms": 10},
                 {"action": {"type": "command", "argv": ["true"]}, "delay_ms": 0}]
        with self.assertRaisesRegex(ActionError, "Passo 2 de 3 falhou \\(1 executados antes\\)"):
            execute_action({"type": "multi", "steps": steps})
        self.assertIn("2 passos executados", execute_action({"type": "multi", "steps": [steps[0], steps[2]]}))

    def test_normalization_of_new_types(self):
        self.assertEqual(normalize_action({"type": "text", "text": "oi", "keys": ["a"]}), {"type": "text", "text": "oi"})
        for bad in ({"type": "multi", "steps": []}, {"type": "multi", "steps": [{"action": {"type": "profile"}}]},
                    {"type": "text", "text": ""}, {"type": "profile"}):
            with self.subTest(bad=bad), self.assertRaises(ActionError):
                normalize_action(bad)


class ProfileSwitching(unittest.TestCase):
    def test_next_previous_and_explicit_profile_with_lcd_resync(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp) / "p.sqlite3")
            second = store.create_profile("Página 2")
            store.save_button("0_4", {"type": "profile", "title": "Próxima", "profile": "next"})
            resynced = []
            ok, message = run_action(store, "0_4", "teste", on_layout_change=resynced.append)
            self.assertEqual((ok, message, store.active_profile()["id"]), (True, "Perfil ativo: Página 2", second))
            self.assertEqual(resynced, ["troca de perfil"])
            self.assertIn("Meu primeiro perfil", switch_profile(store, "previous"))
            with self.assertRaises(ActionError):
                switch_profile(store, "inexistente")

    def test_wide_display_touch_without_action_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp) / "p.sqlite3")
            ok, message = run_action(store, "2_3", "toque", execute=lambda a: self.fail("nada a executar"))
            self.assertTrue(ok)
            self.assertEqual([e["kind"] for e in store.recent_events(1)], ["input"])


class Metrics(unittest.TestCase):
    def test_cpu_memory_and_gpu_from_proc_and_sys(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, sys_root = Path(tmp) / "proc", Path(tmp) / "sys"
            proc.mkdir(); (sys_root / "class/drm/card0/device").mkdir(parents=True)
            (proc / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 250 kB\n")
            (sys_root / "class/drm/card0/device/gpu_busy_percent").write_text("37\n")
            metrics = SystemMetrics(proc, sys_root)
            (proc / "stat").write_text("cpu 100 0 100 800 0 0 0 0\n")
            self.assertEqual(metrics.cpu(), 0)  # first sample has no interval yet
            (proc / "stat").write_text("cpu 175 0 175 850 0 0 0 0\n")  # 150 busy of 200 ticks
            self.assertEqual(metrics.sample(), {"cpu": 75, "mem": 75, "gpu": 37})


class ApiShape(unittest.TestCase):
    def test_display_is_only_kept_for_the_wide_key(self):
        cfg = api.ButtonConfig(type="none", title="x", display=api.DisplayConfig(mode="stats"))
        api.update_button("0_1", cfg)
        api.update_button("2_3", cfg)
        self.assertNotIn("display", api.store.buttons()["0_1"])
        self.assertEqual(api.store.buttons()["2_3"]["display"], {"mode": "stats", "clock_format": "24h", "renderer": "deckforge", "show_date": True, "style": "classic"})
        self.assertEqual(api.macros()["2_3"]["hid_slot"], 13)

    def test_brightness_setting_is_validated(self):
        self.assertEqual(api.update_settings(api.SettingsUpdate(brightness=70))["brightness"], 70)
        with self.assertRaises(Exception):
            api.SettingsUpdate(brightness=150)
        api.update_settings(api.SettingsUpdate(brightness=-1))


if __name__ == "__main__":
    unittest.main()


class DeckForgeWideDisplay(unittest.TestCase):
    """Clock and statistics drawn by DeckForge replace the firmware's Ulanzi-branded screens."""

    def test_clock_and_stats_images_have_wide_size_and_change_with_time(self):
        import io
        from PIL import Image
        from deckforge.render import WIDE_SIZE, render_wide
        nine, ten = render_wide({}, now=9 * 3600 + 1e9), render_wide({}, now=10 * 3600 + 1e9)
        self.assertNotEqual(nine, ten)
        for png in (nine, render_wide({"display": {"mode": "stats"}, "title": "PC"}, {"cpu": 50, "mem": 20, "gpu": 0})):
            with Image.open(io.BytesIO(png)) as image:
                self.assertEqual(image.size, WIDE_SIZE)

    def test_daemon_redraws_clock_once_per_minute_and_on_config_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp) / "p.sqlite3")

            class Dispatcher:
                def submit(self, *a, **k):
                    return True
            channel = ControlChannel(store, Dispatcher())
            uploads = []
            import time as time_module
            real_strftime = time_module.strftime
            try:
                with patch("deckforge.device.d200_write_target", return_value=("/dev/hidraw-test", None)), \
                     patch("deckforge.lcd_sync._send_frames"), \
                     patch("deckforge.lcd_sync.send_wide_image", side_effect=lambda path, png: uploads.append(png)), \
                     patch("deckforge.daemon.time.strftime", side_effect=lambda fmt, *a: "202610121200" if fmt == "%Y%m%d%H%M" else real_strftime(fmt, *a)):
                    channel._send_keepalive(); channel._send_keepalive()
                    self.assertEqual(len(uploads), 1)  # same minute: no redraw
                    store.save_button("2_3", {"type": "none", "color": "#4c68ff", "display": {"mode": "clock"}})
                    channel._display = (0.0, {})
                    channel._send_keepalive()
                    self.assertEqual(len(uploads), 2)  # configuration changed
            finally:
                channel.close()
            self.assertIn("sem a marca do firmware", " ".join(e["message"] for e in store.recent_events(10)))

    def test_background_image_fills_the_wide_display(self):
        import io
        from PIL import Image
        from deckforge import icon_library
        from deckforge.render import render_tile, WIDE_SIZE
        buffer = io.BytesIO(); Image.new("RGB", (1600, 400), (10, 200, 30)).save(buffer, format="PNG")
        ref = icon_library.save_user_icon(buffer.getvalue(), "faixa", kind="background")["ref"]
        with Image.open(icon_library.user_icon_path(ref)) as stored:
            self.assertEqual(stored.size, (800, 200))  # aspect ratio kept, at most 800 px
        png = render_tile({"background": ref, "display": {"mode": "image"}}, WIDE_SIZE)
        with Image.open(io.BytesIO(png)) as image:
            self.assertEqual(image.getpixel((5, 5)), (10, 200, 30))  # cover: fills the corners
        contained = render_tile({"background": ref, "background_fit": "contain", "color": "#000000"}, WIDE_SIZE)
        with Image.open(io.BytesIO(contained)) as image:
            self.assertEqual(image.getpixel((196, 5)), (0, 0, 0))  # contain: letterboxed
