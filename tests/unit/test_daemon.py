import isolation  # noqa: F401  (must precede deckforge imports)
import errno
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from deckforge import daemon
from deckforge.actions import ActionError
from deckforge.daemon import ActionDispatcher, ControlChannel, LcdSyncWorker, run_action
from deckforge.lcd_sync import clock_frame, parse_frame
from deckforge.store import ProfileStore
from test_decoder import CAPTURED_ORDER, EXPECTED_SLOTS, run_macro

REPO = Path(__file__).resolve().parents[2]
D200 = {"mode": "keyboard_macros", "profile": "D200 2-interface", "reason": "teste"}
DEVICE = [{"interfaces": []}]


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.01)
    return predicate()


def make_store(tmp):
    store = ProfileStore(Path(tmp) / "profile.sqlite3")
    store.save_button("0_0", {"type": "app", "title": "GitHub", "argv": ["xdg-open", "https://github.com"]})
    return store


def events(store, kind=None):
    return [e for e in reversed(store.recent_events(200)) if kind is None or e["kind"] == kind]


def hid_button(slot, pressed=True, category=1):
    report = bytearray(1024)
    report[:8] = b"||\x01\x01" + (1014).to_bytes(4, "little")
    report[8:12] = bytes([category, slot, 1, 1 if pressed else 0])
    return bytes(report)


class FakeLcd:
    def __init__(self):
        self.requests = []

    def request(self, reason):
        self.requests.append(reason)

    def close(self, timeout=0):
        pass


class RunActionTests(unittest.TestCase):
    def test_executes_saved_action_and_records_real_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = make_store(tmp)
            calls = []
            ok, message = run_action(store, "0_0", "macro calc", execute=lambda a: calls.append(a) or "xdg-open ok")
            self.assertTrue(ok)
            self.assertEqual(calls, [store.buttons()["0_0"]])
            self.assertEqual(events(store, "action")[-1]["message"], "0_0 · GitHub: xdg-open ok")
            self.assertTrue(store.get_state()["last_action"]["ok"])

    def test_failure_is_logged_as_error_never_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = make_store(tmp)

            def fail(_action):
                raise ActionError("ydotool não conseguiu enviar")
            ok, message = run_action(store, "0_0", "macro calc", execute=fail)
            self.assertFalse(ok)
            self.assertEqual(events(store, "action"), [])
            self.assertIn("ydotool não conseguiu enviar", events(store, "error")[-1]["message"])
            self.assertFalse(store.get_state()["last_action"]["ok"])

    def test_missing_action_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = make_store(tmp)
            store.delete_button("2_2")
            ok, message = run_action(store, "2_2", "atalho Ctrl+C", execute=lambda a: "nunca")
            self.assertFalse(ok)
            self.assertIn("não tem ação configurada", message)


class DispatcherTests(unittest.TestCase):
    def run_presses(self, presses):
        with tempfile.TemporaryDirectory() as tmp:
            store = make_store(tmp)
            executed = []
            dispatcher = ActionDispatcher(store, execute=lambda a: executed.append(a["title"]) or "ok")
            try:
                accepted = [dispatcher.submit(slot, "teste", channel) for slot, channel in presses]
                wait_for(lambda: len(executed) == accepted.count(True))
            finally:
                dispatcher.close()
            return accepted, executed

    def test_same_click_on_both_channels_runs_once(self):
        self.assertEqual(self.run_presses([("0_0", "hid"), ("0_0", "keyboard")]), ([True, False], ["GitHub"]))
        self.assertEqual(self.run_presses([("0_0", "keyboard"), ("0_0", "hid")]), ([True, False], ["GitHub"]))

    def test_two_real_presses_on_one_channel_run_twice(self):
        self.assertEqual(self.run_presses([("0_0", "hid"), ("0_0", "hid")]), ([True, True], ["GitHub", "GitHub"]))

    def test_double_press_reported_by_both_channels_runs_twice(self):
        accepted, executed = self.run_presses([("0_0", "hid"), ("0_0", "hid"), ("0_0", "keyboard"), ("0_0", "keyboard")])
        self.assertEqual(accepted, [True, True, False, False])
        self.assertEqual(len(executed), 2)


class KeyboardCaptureTests(unittest.TestCase):
    """Physical key events (simulated stream) -> decoder -> slot -> saved action."""

    def test_each_captured_macro_runs_the_action_saved_for_its_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp) / "profile.sqlite3")
            for slot in EXPECTED_SLOTS:
                store.save_button(slot, {"type": "command", "title": f"acao-{slot}", "argv": ["true"]})
            stream = [e for i, token in enumerate(CAPTURED_ORDER) for e in run_macro(token, start=i * 2.0)]
            executed = []
            dispatcher = ActionDispatcher(store, execute=lambda a: executed.append(a["title"]) or "ok")
            lcd = FakeLcd()
            seen_grab = {}

            def fake_reader(path, stop, grab=False, on_ready=None):
                seen_grab["grab"] = grab
                on_ready()
                yield from stream

            try:
                with patch.object(daemon, "read_key_events", fake_reader):
                    opened = daemon._keyboard_session(store, "/dev/input/event28", D200, daemon._Reporter(store),
                                                      dispatcher, lcd, False, lambda: False)
                wait_for(lambda: len(executed) == 10)
            finally:
                dispatcher.close()
            self.assertTrue(opened)
            self.assertTrue(seen_grab["grab"])
            self.assertEqual(executed, [f"acao-{slot}" for slot in EXPECTED_SLOTS])
            self.assertEqual(lcd.requests, ["inicialização"])
            self.assertTrue(any(e["message"].startswith("Captura de macros ativa (D200 2-interface) em /dev/input/event28")
                                for e in events(store, "daemon")))
            # The stream ended (node closed): the published state no longer claims an active capture.
            self.assertEqual(store.get_state()["capture"]["state"], "waiting")

    def test_grab_conflict_is_reported_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = make_store(tmp)

            def busy(*_a, **_k):
                raise OSError(errno.EBUSY, "Device or resource busy")
                yield  # pragma: no cover

            dispatcher = ActionDispatcher(store, execute=lambda a: "ok")
            try:
                with patch.object(daemon, "read_key_events", busy):
                    daemon._keyboard_session(store, "/dev/input/event28", D200, daemon._Reporter(store),
                                             dispatcher, FakeLcd(), False, lambda: False)
            finally:
                dispatcher.close()
            self.assertIn("Outro processo já isolou", store.get_state()["capture"]["message"])


class ReconnectTests(unittest.TestCase):
    def test_unplug_and_replug_resumes_capture_and_reapplies_icons(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = make_store(tmp)
            store.save_button("1_4", {"type": "command", "title": "Arquivos", "argv": ["true"]})
            executed = []
            dispatcher = ActionDispatcher(store, execute=lambda a: executed.append(a["title"]) or "ok")
            lcd = FakeLcd()
            sessions = [run_macro("calc"), run_macro("taskmgr")]
            unplugged = {"scans": 0}
            stop = threading.Event()

            def fake_discover():
                if unplugged["scans"]:
                    unplugged["scans"] -= 1  # dock unplugged: no USB device for one scan
                    return []
                return DEVICE

            def fake_reader(path, should_stop, grab=False, on_ready=None):
                current = sessions.pop(0)
                on_ready()
                yield from current
                if not sessions:
                    stop.set()
                    return
                unplugged["scans"] = 1
                raise OSError(errno.ENODEV, "No such device")

            def fake_pause(_seconds, _should_stop):
                pass

            threads_before = threading.active_count()
            try:
                with patch.object(daemon, "discover", fake_discover), \
                     patch.object(daemon, "capture_profile", lambda devices=None: D200), \
                     patch.object(daemon, "keyboard_nodes", lambda: [{"path": "/dev/input/event28", "readable": True}]), \
                     patch.object(daemon, "read_key_events", fake_reader), \
                     patch.object(daemon, "_pause", fake_pause):
                    daemon._run_loop(store, None, stop.is_set, dispatcher, lcd)
                wait_for(lambda: len(executed) == 2)
            finally:
                dispatcher.close()
            self.assertEqual(executed, ["GitHub", "Arquivos"])
            self.assertEqual(lcd.requests, ["inicialização", "reconexão"])
            messages = [e["message"] for e in events(store)]
            self.assertTrue(any("desconectado" in m for m in messages))
            self.assertTrue(any("retomada após reconexão" in m for m in messages))
            # No extra reader/worker threads were spawned by the reconnect.
            self.assertLessEqual(threading.active_count(), threads_before)


class ControlChannelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = make_store(self.tmp.name)
        self.submitted = []

        class Recorder:
            def submit(_self, slot, source, channel="keyboard"):
                self.submitted.append((slot, channel))
                return True

        self.sent = []
        self.channel = ControlChannel(self.store, Recorder(), keepalive=lambda path, payload: self.sent.append(path))

    def tearDown(self):
        self.channel.close()
        self.tmp.cleanup()

    def test_host_mode_press_dispatches_slot_once(self):
        self.channel.handle(hid_button(7))
        self.channel.handle(hid_button(7, pressed=False))
        self.assertEqual(self.submitted, [("1_2", "hid")])

    def test_press_pending_from_before_the_connection_is_discarded(self):
        import time as _time
        self.channel._opened_at = _time.monotonic()  # interface just reopened
        self.channel.handle(hid_button(5))
        self.assertEqual(self.submitted, [])
        self.assertIn("descartado", events(self.store, "input")[-1]["message"])
        self.channel._opened_at = _time.monotonic() - 2
        self.channel.handle(hid_button(5))
        self.assertEqual(self.submitted, [("1_0", "hid")])

    def test_unknown_first_byte_is_still_accepted_on_two_interface_d200(self):
        self.channel.handle(hid_button(0, category=3))
        self.assertEqual(self.submitted, [("0_0", "hid")])

    def test_wide_display_touch_is_the_configurable_2_3_key(self):
        self.channel.handle(hid_button(13, category=0))
        self.assertEqual(self.submitted, [("2_3", "hid")])

    def test_malformed_frames_do_not_dispatch(self):
        self.channel.handle(b"||\x01\x01" + bytes(4) + bytes([1, 40, 1, 1]))
        self.channel.handle(b"not a frame")
        self.assertEqual(self.submitted, [])

    def test_device_info_is_recorded_without_serial(self):
        payload = b'{"SerialNumber": "SECRET", "Dversion": "5.3.1", "error": "0", "DeviceType": "D200", "HardwareVersion": "SSD210V100"}'
        report = b"||\x03\x03" + len(payload).to_bytes(4, "little") + payload
        self.channel.handle(report)
        info = self.store.get_state()["device_info"]
        self.assertEqual(info["Dversion"], "5.3.1")
        self.assertNotIn("SerialNumber", info)
        self.assertNotIn("SECRET", " ".join(e["message"] for e in events(self.store)))

    def test_keepalive_respects_setting_and_write_target(self):
        with patch("deckforge.device.d200_write_target", return_value=("/dev/hidraw-test", None)):
            self.channel._send_keepalive()
            self.assertEqual(self.sent, ["/dev/hidraw-test"])
            self.assertEqual(self.store.get_state()["host_link"]["keepalive"], "ok")
            self.store.save_settings({"host_mode": False})
            self.channel.settings._loaded = 0
            self.channel._send_keepalive()
            self.assertEqual(self.sent, ["/dev/hidraw-test"])
        with patch("deckforge.device.d200_write_target", return_value=(None, "sem permissão")):
            self.store.save_settings({"host_mode": True})
            self.channel.settings._loaded = 0
            self.channel._send_keepalive()
            self.assertEqual(self.store.get_state()["host_link"]["keepalive"], "unavailable")

    def test_clock_frame_matches_reference_layout(self):
        frame = clock_frame(0, {"mode": "clock", "clock_format": "24h", "renderer": "firmware"})
        command, payload = parse_frame(frame)
        self.assertEqual((len(frame), frame[:4]), (1024, b"||\x00\x06"))
        self.assertEqual(command, 0x0006)
        self.assertRegex(payload.decode(), r"^1\|0\|0\|\d\d:\d\d:\d\d\|0$")


class LcdWorkerTests(unittest.TestCase):
    def test_retries_until_the_device_accepts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = make_store(tmp)
            attempts = []

            def flaky(_store, source):
                attempts.append(source)
                if len(attempts) < 3:
                    raise PermissionError(13, "Permission denied")
                return {"slots": ["0_0"], "frames": 4}

            worker = LcdSyncWorker(store, sync=flaky, retry_delays=(.01, .01))
            try:
                worker.request("reconexão")
                wait_for(lambda: any(e["kind"] == "sync" for e in events(store)))
            finally:
                worker.close()
            self.assertEqual(attempts, ["daemon:reconexão"] * 3)
            self.assertIn("o firmware não confirma a renderização", events(store, "sync")[-1]["message"])

    def _worker_across_suspend(self, store, sync):
        """Start a worker, then advance the suspended-time clock as a 15 min sleep would."""
        asleep, reads = [0.0], []

        def suspended():
            value = asleep[0]
            reads.append(value)
            return value

        worker = LcdSyncWorker(store, sync=sync, retry_delays=(.01, .01), suspended=suspended)
        wait_for(lambda: bool(reads))
        asleep[0] = 900.0
        return worker

    def test_resume_from_suspend_sends_the_profile_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = make_store(tmp)
            calls = []
            worker = self._worker_across_suspend(store, lambda _store, source: calls.append(source) or {"slots": ["0_0"], "frames": 4})
            try:
                wait_for(lambda: bool(events(store, "sync")))
            finally:
                worker.close()
            self.assertEqual(calls, ["daemon:retomada após suspensão"])
            self.assertIn("LCD (retomada após suspensão): 4 quadros", events(store, "sync")[-1]["message"])

    def test_dock_reconnecting_after_resume_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = make_store(tmp)

            def gone(_store, source):
                raise FileNotFoundError(2, "No such file or directory")

            worker = self._worker_across_suspend(store, gone)
            try:
                wait_for(lambda: bool(events(store, "sync")))
            finally:
                worker.close()
            self.assertIn("será reenviado quando ele reconectar", events(store, "sync")[-1]["message"])
            self.assertEqual(events(store, "error"), [])

    def test_disabled_host_mode_skips_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = make_store(tmp)
            store.save_settings({"host_mode": False})
            calls = []
            worker = LcdSyncWorker(store, sync=lambda *a, **k: calls.append(1) or {})
            try:
                worker.request("inicialização")
                wait_for(lambda: bool(events(store, "sync")))
            finally:
                worker.close()
            self.assertEqual(calls, [])


class ParentDeathTests(unittest.TestCase):
    def test_daemon_exits_when_serve_dies_even_by_sigkill(self):
        """A daemon started for `serve` must not survive it (no orphan holding the keyboard)."""
        child_code = textwrap.dedent("""
            import sys
            import deckforge.daemon as d
            d.discover = lambda: []          # never touch real hardware in tests
            from deckforge.cli import main
            sys.argv = ["deckforge", "daemon", "--parent-pid", sys.argv[1]]
            raise SystemExit(main())
        """)
        parent_code = textwrap.dedent(f"""
            import os, subprocess, sys, time
            child = subprocess.Popen([sys.executable, "-c", {child_code!r}, str(os.getpid())])
            print(child.pid, flush=True)
            time.sleep(60)
        """)
        env = {**os.environ, "PYTHONPATH": str(REPO / "services/daemon")}
        parent = subprocess.Popen([sys.executable, "-c", parent_code], stdout=subprocess.PIPE, text=True, env=env)
        child_pid = int(parent.stdout.readline())
        try:
            self.assertTrue(wait_for(lambda: daemon.daemon_status().get("pid") == child_pid, 10))
            parent.send_signal(signal.SIGKILL)
            parent.wait()
            gone = wait_for(lambda: not Path(f"/proc/{child_pid}").exists() or
                            Path(f"/proc/{child_pid}/stat").read_text().split(") ")[1].startswith("Z"), 5)
            self.assertTrue(gone, "daemon continuou ativo após a morte do processo pai")
            self.assertFalse(daemon.daemon_status()["running"])
        finally:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            parent.stdout.close()


if __name__ == "__main__":
    unittest.main()
