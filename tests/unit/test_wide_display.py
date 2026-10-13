import isolation  # noqa: F401  (must precede deckforge imports)
import io
import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from deckforge import api, lcd_sync
from deckforge.daemon import ActionDispatcher, ControlChannel
from deckforge.lcd_sync import small_window_payload, sync_icons
from deckforge.render import render_wide
from deckforge.store import ProfileStore
from test_daemon import hid_button, wait_for

STATS = {"type": "none", "title": "", "color": "#252936", "display": {"mode": "stats", "renderer": "deckforge"}}


class StatisticsWithoutFlicker(unittest.TestCase):
    """Statistics are drawn by the firmware over a background sent once, never re-uploaded."""

    def test_keepalive_carries_the_values_in_deckforge_mode(self):
        payload = small_window_payload({"mode": "stats", "renderer": "deckforge"}, {"cpu": 12, "mem": 71, "gpu": 3}, now=0)
        self.assertRegex(payload.decode(), r"^0\|12\|71\|\d\d:\d\d:\d\d\|3$")
        self.assertEqual(small_window_payload({"mode": "clock", "renderer": "deckforge"}), b"2|0|0|00:00:00|0")

    def test_sync_uploads_only_the_background(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp) / "p.sqlite3")
            store.save_button("2_3", STATS)
            sent = []
            with patch("deckforge.lcd_sync.d200_write_target", return_value=("/dev/hidraw-test", None)), \
                 patch("deckforge.lcd_sync._send", side_effect=lambda path, blob, command=lcd_sync.SET_BUTTONS: sent.append((command, blob)) or 1):
                sync_icons(store)
            command, blob = sent[-1]
            self.assertEqual(command, lcd_sync.PARTIALLY_UPDATE_BUTTONS)
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                icon = json.loads(archive.read("manifest.json"))["3_2"]["ViewParam"][0]["Icon"]
                self.assertEqual(archive.read(icon), render_wide(STATS, values=False))
            self.assertNotEqual(render_wide(STATS, values=False), render_wide(STATS, {"cpu": 50, "mem": 50, "gpu": 0}))

    def test_daemon_never_reuploads_the_statistics_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp) / "p.sqlite3")
            store.save_button("2_3", STATS)

            class Dispatcher:
                def submit(self, *a, **k):
                    return True
            channel = ControlChannel(store, Dispatcher())
            uploads, frames = [], []
            try:
                with patch("deckforge.device.d200_write_target", return_value=("/dev/hidraw-test", None)), \
                     patch("deckforge.lcd_sync._send_frames", side_effect=lambda path, *f: frames.extend(f)), \
                     patch("deckforge.lcd_sync.send_wide_image", side_effect=lambda path, png: uploads.append(png)):
                    for _ in range(3):
                        channel._send_keepalive()
            finally:
                channel.close()
            self.assertEqual(uploads, [])
            self.assertTrue(all(frame[8:10] == b"0|" for frame in frames))


class DoubleTap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ProfileStore(Path(self.tmp.name) / "p.sqlite3")
        self.media = self.store.create_page("Mídia")
        self.store.save_button("2_3", STATS)
        self.dispatcher = ActionDispatcher(self.store, execute=lambda action: "executado", double_tap_seconds=.15)

    def tearDown(self):
        self.dispatcher.close()
        self.tmp.cleanup()

    def logged(self, kind):
        return [e["message"] for e in reversed(self.store.recent_events(50)) if e["kind"] == kind]

    def test_two_quick_touches_switch_to_the_next_page(self):
        self.dispatcher.submit("2_3", "toque", channel="hid")
        self.dispatcher.submit("2_3", "toque", channel="hid")
        self.assertTrue(wait_for(lambda: self.store.active_page()["id"] == self.media))
        time.sleep(.3)  # the first touch must not also run as a single tap
        self.assertEqual(self.logged("action"), ["2_3 · toque duplo: Página: Mídia"])
        self.assertEqual(self.logged("input"), [])

    def test_single_touch_runs_after_the_window(self):
        self.dispatcher.submit("2_3", "toque", channel="hid")
        self.assertTrue(wait_for(lambda: self.logged("input")))
        self.assertIn("somente exibição", self.logged("input")[0])
        self.assertEqual(self.store.active_page()["id"], "home")

    def test_double_tap_set_to_nothing_keeps_single_touch_immediate(self):
        self.store.save_button("2_3", {**STATS, "double_tap": {"type": "none"}})
        self.dispatcher.submit("2_3", "toque", channel="hid")
        self.dispatcher.submit("2_3", "toque", channel="hid")
        self.assertTrue(wait_for(lambda: len(self.logged("input")) == 2))
        self.assertEqual(self.store.active_page()["id"], "home")


class TouchReports(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ProfileStore(Path(self.tmp.name) / "p.sqlite3")
        self.submitted = []

        class Recorder:
            def submit(_self, slot, source, channel="keyboard"):
                self.submitted.append(slot)
                return True
        self.channel = ControlChannel(self.store, Recorder(), keepalive=lambda path, payload: None)

    def tearDown(self):
        self.channel.close()
        self.tmp.cleanup()

    def test_quick_second_touch_after_release_is_reported(self):
        for pressed in (True, False):
            self.channel.handle(hid_button(13, pressed=pressed, category=0))
        time.sleep(.06)
        self.channel.handle(hid_button(13, category=0))
        self.assertEqual(self.submitted, ["2_3", "2_3"])

    def test_repeated_report_of_a_held_touch_is_ignored(self):
        self.channel.handle(hid_button(13, category=0))
        time.sleep(.06)
        self.channel.handle(hid_button(13, category=0))
        self.assertEqual(self.submitted, ["2_3"])


class DoubleTapApi(unittest.TestCase):
    def setUp(self):
        api.store.activate_profile(api.store.create_profile("Toque duplo"))

    def test_double_tap_is_kept_only_on_the_wide_display(self):
        api.update_button("2_3", api.ButtonConfig(type="none", title="", double_tap={"type": "page", "page": "previous", "keys": ["x"]}))
        self.assertEqual(api.store.buttons()["2_3"]["double_tap"], {"type": "page", "page": "previous"})
        api.update_button("0_0", api.ButtonConfig(type="keys", title="C", keys=["ctrl", "c"], double_tap={"type": "none"}))
        self.assertNotIn("double_tap", api.store.buttons()["0_0"])
        with self.assertRaises(HTTPException):
            api.update_button("2_3", api.ButtonConfig(type="none", title="", double_tap={"type": "multi"}))


if __name__ == "__main__":
    unittest.main()


class DeveloperClock(unittest.TestCase):
    """Animated code-editor clock: a GIF the firmware plays by itself."""

    CONFIG = {"type": "none", "title": "", "color": "#0d1117", "display": {"mode": "clock", "style": "dev"}}

    def test_renders_an_animated_gif_of_the_wide_size(self):
        from PIL import Image
        gif = render_wide(self.CONFIG, now=0)
        with Image.open(io.BytesIO(gif)) as image:
            self.assertEqual((image.format, image.size), ("GIF", (392, 196)))
            self.assertGreater(image.n_frames, 5)
        self.assertNotEqual(gif, render_wide(self.CONFIG, now=60))  # new minute, new GIF

    def test_archive_names_the_animation_as_gif(self):
        blob = lcd_sync._build_archive({"3_2": (self.CONFIG, render_wide(self.CONFIG, now=0))})
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            self.assertRegex(json.loads(archive.read("manifest.json"))["3_2"]["ViewParam"][0]["Icon"], r"^icons/3_2-[0-9a-f]{12}\.gif$")

    def test_keepalive_keeps_the_host_image_and_preview_is_served_as_gif(self):
        self.assertEqual(small_window_payload({"mode": "clock", "renderer": "deckforge", "style": "dev"}), b"2|0|0|00:00:00|0")
        api.store.activate_profile(api.store.create_profile("Relógio dev"))
        api.update_button("2_3", api.ButtonConfig(type="none", title="", display={"mode": "clock", "style": "dev"}))
        self.assertEqual(api.render_saved_key("2_3").media_type, "image/gif")
