import isolation  # noqa: F401  (must precede deckforge imports)
import tempfile
import io
import json
import subprocess
import zipfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deckforge.actions import ActionError, _shortcut_command, execute_action
from deckforge.device import capture_profile, parse_button_report
from deckforge.keyboard import KeyboardMacroDecoder
from deckforge.lcd_sync import _build_archive, _send, sync_icons
from deckforge.store import ProfileStore
from deckforge.transport import MockTransport, RecordingTransport


class ProtocolTests(unittest.TestCase):
    def test_parses_regular_press_release_and_wide_slot(self):
        report = bytearray(1024)
        report[:4] = b"||\x01\x01"
        report[8:12] = bytes([1, 7, 1, 1])
        self.assertEqual(parse_button_report(report), {"slot": 7, "category": 1, "pressed": True, "key": "1_2"})
        report[11] = 0
        self.assertFalse(parse_button_report(report)["pressed"])
        report[8:12] = bytes([0, 13, 1, 1])
        self.assertEqual(parse_button_report(report)["key"], "2_3")

    def test_ignores_other_or_malformed_reports(self):
        self.assertIsNone(parse_button_report(b"short"))
        self.assertIsNone(parse_button_report(b"||\x01\x03" + bytes(20)))
        report = bytearray(1024); report[:4] = b"||\x01\x01"; report[8:12] = bytes([0, 1, 1, 1])
        self.assertIsNone(parse_button_report(report))

    def test_icon_archive_has_matching_manifest_paths_and_valid_zip(self):
        archive = _build_archive({"1_0": ({"icon": "test.svg"}, b"fake-png")})
        with zipfile.ZipFile(io.BytesIO(archive)) as package:
            manifest = json.loads(package.read("manifest.json"))
            self.assertEqual(manifest["1_0"]["ViewParam"][0]["Icon"], "icons/1_0.png")
            self.assertEqual(package.read("icons/1_0.png"), b"fake-png")
            self.assertEqual(package.testzip(), None)

    def test_empty_profile_creates_a_valid_snapshot_to_clear_old_icons(self):
        archive = _build_archive({})
        with zipfile.ZipFile(io.BytesIO(archive)) as package:
            self.assertEqual(json.loads(package.read("manifest.json")), {})
            self.assertEqual(package.testzip(), None)

    def test_hid_send_uses_full_profile_command_and_report_id_prefix(self):
        blob = bytes(range(256)) * 8 + b"end"
        writes = []

        def record_write(_fd, packet):
            writes.append(packet)
            return len(packet)

        with patch("deckforge.lcd_sync.os.open", return_value=42), \
             patch("deckforge.lcd_sync.os.write", side_effect=record_write), \
             patch("deckforge.lcd_sync.os.close"):
            frames = _send("/dev/hidraw-test", blob)

        self.assertEqual(frames, len(writes))
        self.assertEqual(writes[0][0], 0)  # Linux hidraw Report ID
        self.assertEqual(writes[0][1:5], b"||\x00\x01")  # SET_BUTTONS, not additive update
        self.assertEqual(int.from_bytes(writes[0][5:9], "little"), len(blob))
        self.assertEqual(len(writes[0]), 1025)
        self.assertEqual(writes[1][1:], blob[1016:2040])

    def test_single_key_sync_sends_full_profile_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp) / "profile.sqlite3")
            store.activate_profile(store.create_profile("Vazio"))
            store.save_button("0_0", {"icon": "first.svg"})
            store.save_button("1_1", {"icon": "second.svg"})
            with patch("deckforge.lcd_sync.d200_write_target", return_value=("/dev/hidraw-test", None)), \
                 patch("deckforge.lcd_sync._render_icon", side_effect=lambda config: config["icon"].encode()) as render, \
                 patch("deckforge.lcd_sync._send", return_value=2) as send:
                result = sync_icons(store, "0_0")
            self.assertEqual(set(result["keys"]), {"0_0", "1_1"})
            self.assertEqual(render.call_count, 2)
            # Full snapshot, then the DeckForge-drawn wide display (clock) as an additive update.
            self.assertEqual([c.args[2] if len(c.args) > 2 else 0x0001 for c in send.call_args_list], [0x0001, 0x000D])

    def test_profile_without_icons_sends_empty_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp) / "profile.sqlite3")
            store.activate_profile(store.create_profile("Vazio"))
            store.save_button("0_0", {"type": "keys", "keys": ["ctrl", "c"]})  # no icon, no title
            with patch("deckforge.lcd_sync.d200_write_target", return_value=("/dev/hidraw-test", None)), \
                 patch("deckforge.lcd_sync._send", return_value=1) as send:
                result = sync_icons(store)
            self.assertEqual(result["keys"], [])
            self.assertEqual(send.call_count, 2)  # empty snapshot + wide display clock

    def test_titled_keys_without_icon_are_rendered_as_text_tiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp) / "profile.sqlite3")  # starter profile: titles, no icons
            with patch("deckforge.lcd_sync.d200_write_target", return_value=("/dev/hidraw-test", None)), \
                 patch("deckforge.lcd_sync._send", return_value=1):
                result = sync_icons(store)
            self.assertEqual(len(result["slots"]), 14)  # 13 keys + the DeckForge clock on 2_3
            self.assertIn("2_3", result["slots"])


class ProfileTests(unittest.TestCase):
    def test_persists_buttons_and_rejects_reserved_positions(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp) / "profile.sqlite3")
            store.save_button("0_0", {"type": "keys", "title": "Copiar", "keys": ["ctrl", "c"]})
            self.assertEqual(store.buttons()["0_0"]["title"], "Copiar")
            with self.assertRaises(ValueError):
                store.save_button("2_4", {"type": "keys", "keys": ["ctrl", "c"]})

    def test_profile_can_be_switched_and_button_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp) / "profile.sqlite3")
            second = store.create_profile("Multimídia")
            store.activate_profile(second)
            self.assertEqual(store.active_profile()["name"], "Multimídia")
            self.assertEqual(store.buttons(), {})
            store.delete_button("0_0")

    def test_icon_and_action_survive_store_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.sqlite3"
            store = ProfileStore(path)
            saved = {"type": "app", "title": "GitHub", "argv": ["xdg-open", "https://github.com"],
                     "icon": "streamline-logos-solid/github.svg", "color": "#252936"}
            store.save_button("0_0", saved)
            reopened = ProfileStore(path)
            self.assertEqual(reopened.buttons()["0_0"], saved)


class KeyboardDecoderTests(unittest.TestCase):
    @staticmethod
    def press(decoder, code):
        return decoder.feed({"code": code, "value": 1})

    @staticmethod
    def release(decoder, code):
        return decoder.feed({"code": code, "value": 0})

    def test_windows_run_macro_maps_to_saved_slot(self):
        decoder = KeyboardMacroDecoder()
        self.press(decoder, 125)  # Super
        self.press(decoder, 19)   # R
        self.release(decoder, 125)
        self.release(decoder, 19)
        for code in (46, 30, 38, 46):  # calc
            self.press(decoder, code)
            self.release(decoder, code)
        self.assertEqual(self.press(decoder, 28), "0_0")

    def test_plain_firmware_launcher_tokens_map_with_or_without_leading_r(self):
        for codes in ([46, 30, 38, 46], [19, 46, 30, 38, 46]):
            decoder = KeyboardMacroDecoder()
            for code in codes:
                self.press(decoder, code)
                self.release(decoder, code)
            self.assertEqual(self.press(decoder, 28), "0_0")

    def test_confirmed_bottom_shortcuts_map_to_three_slots(self):
        for letter, expected in ((31, "2_0"), (44, "2_1"), (46, "2_2")):
            decoder = KeyboardMacroDecoder()
            self.press(decoder, 29)
            self.assertEqual(self.press(decoder, letter), expected)

    def test_mac_command_shortcuts_map_to_the_same_bottom_slots(self):
        for letter, expected in ((31, "2_0"), (44, "2_1"), (46, "2_2")):
            decoder = KeyboardMacroDecoder()
            self.press(decoder, 125)
            self.assertEqual(self.press(decoder, letter), expected)


class CaptureProfileTests(unittest.TestCase):
    def test_three_interface_device_uses_vendor_slot_reports(self):
        device = {"interfaces": [
            {"number": "00", "class": "03", "protocol": "00", "endpoints": [{"address": "81"}, {"address": "02"}]},
            {"number": "01", "class": "ff", "protocol": "00", "endpoints": []},
            {"number": "02", "class": "03", "protocol": "01", "endpoints": []},
        ]}
        self.assertEqual(capture_profile([device])["mode"], "vendor_hid")

    def test_two_interface_device_uses_keyboard_macro_capture(self):
        device = {"interfaces": [
            {"number": "00", "class": "03", "protocol": "00", "endpoints": [{"address": "82"}, {"address": "01"}]},
            {"number": "01", "class": "03", "protocol": "01", "endpoints": [{"address": "83"}]},
        ]}
        self.assertEqual(capture_profile([device])["mode"], "keyboard_macros")

class TransportTests(unittest.TestCase):
    def test_mock_and_recording_transport_are_read_only_streams(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = MockTransport([b"first", b"second"])
            source.close()
            recording = RecordingTransport(source, Path(tmp) / "capture.bin")
            self.assertEqual(list(recording.read_reports()), [b"first", b"second"])
            self.assertEqual((Path(tmp) / "capture.bin").read_bytes(), b"\x05\x00first\x06\x00second")


class ActionTests(unittest.TestCase):
    def test_rejects_untyped_shell_actions(self):
        with self.assertRaises(ActionError):
            execute_action({"type": "shell", "command": "touch /tmp/nope"})
        with self.assertRaises(ActionError):
            execute_action({"type": "command", "argv": []})
        with self.assertRaises(ActionError):
            execute_action({"type": "command", "argv": ["/bin/sh", "-c", "touch /tmp/nope"]})

    def test_wayland_shortcut_uses_wtype_with_modifier_release(self):
        with patch("deckforge.actions.shutil.which", side_effect=lambda name: {"wtype": "/usr/bin/wtype"}.get(name)), \
             patch.dict("deckforge.actions.os.environ", {"XDG_CURRENT_DESKTOP": "sway"}):
            self.assertEqual(_shortcut_command(["ctrl", "shift", "t"], "wayland"),
                             ["/usr/bin/wtype", "-M", "ctrl", "-M", "shift", "-P", "t", "-p", "t", "-m", "shift", "-m", "ctrl"])

    def test_wayland_shortcut_reports_missing_backend(self):
        with patch("deckforge.actions.shutil.which", return_value=None):
            with self.assertRaisesRegex(ActionError, "wtype ou ydotool"):
                _shortcut_command(["ctrl", "c"], "wayland")

    def test_wayland_special_keys_use_xkb_names(self):
        with patch("deckforge.actions.shutil.which", side_effect=lambda name: {"wtype": "/usr/bin/wtype"}.get(name)):
            self.assertEqual(_shortcut_command(["alt", "Tab"], "wayland"),
                             ["/usr/bin/wtype", "-M", "alt", "-P", "Tab", "-p", "Tab", "-m", "alt"])

    def test_wayland_super_modifier_uses_wtype_logo_name(self):
        with patch("deckforge.actions.shutil.which", side_effect=lambda name: {"wtype": "/usr/bin/wtype"}.get(name)):
            self.assertEqual(_shortcut_command(["super", "d"], "wayland"),
                             ["/usr/bin/wtype", "-M", "logo", "-P", "d", "-p", "d", "-m", "logo"])

    def test_gnome_wayland_prefers_ydotool_evdev_sequence(self):
        paths = {"ydotool": "/usr/bin/ydotool", "wtype": "/usr/bin/wtype"}
        with patch("deckforge.actions.shutil.which", side_effect=lambda name: paths.get(name)), \
             patch("deckforge.session.socket_ready", return_value=True), \
             patch.dict("deckforge.actions.os.environ", {"XDG_CURRENT_DESKTOP": "GNOME"}):
            self.assertEqual(_shortcut_command(["ctrl", "shift", "t"], "wayland"),
                             ["/usr/bin/ydotool", "key", "29:1", "42:1", "20:1", "20:0", "42:0", "29:0"])

    def test_ydotool_maps_names_and_releases_modifiers(self):
        paths = {"ydotool": "/usr/bin/ydotool"}
        with patch("deckforge.actions.shutil.which", side_effect=lambda name: paths.get(name)), \
             patch("deckforge.session.socket_ready", return_value=True), \
             patch.dict("deckforge.actions.os.environ", {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME"}), \
             patch("deckforge.actions.subprocess.run", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as run:
            result = execute_action({"type": "keys", "keys": ["super", "d"]})
        self.assertEqual(result, "Atalho super+d enviado via ydotool")
        self.assertEqual(run.call_args.args[0], ["/usr/bin/ydotool", "key", "125:1", "32:1", "32:0", "125:0"])

    def test_gnome_wtype_rejection_explains_required_backend(self):
        from subprocess import CalledProcessError
        with patch("deckforge.actions.shutil.which", side_effect=lambda name: {"wtype": "/usr/bin/wtype"}.get(name)), \
             patch.dict("deckforge.actions.os.environ", {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME"}), \
             patch("deckforge.actions.subprocess.run", side_effect=CalledProcessError(1, "wtype", stderr=b"Compositor does not support the virtual keyboard protocol")):
            with self.assertRaisesRegex(ActionError, "setup-wayland-input.sh"):
                execute_action({"type": "keys", "keys": ["ctrl", "c"]})

    def test_app_action_starts_selected_argv_without_shell(self):
        with patch("deckforge.actions.shutil.which", return_value="/usr/bin/xdg-open"), \
             patch("deckforge.actions.subprocess.Popen") as popen:
            popen.return_value.wait.return_value = 0
            result = execute_action({"type": "app", "title": "GitHub",
                                     "argv": ["xdg-open", "https://github.com"]})
        self.assertEqual(result, "xdg-open entregou https://github.com ao aplicativo padrão (código 0)")
        self.assertNotIn("shell", popen.call_args.kwargs)
        self.assertEqual(popen.call_args.args[0], ["/usr/bin/xdg-open", "https://github.com"])


if __name__ == "__main__":
    unittest.main()
