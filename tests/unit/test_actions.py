import isolation  # noqa: F401  (must precede deckforge imports)
import os
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from deckforge import actions
from deckforge.actions import ActionError, execute_action, normalize_action
from deckforge.session import select_input_backend, session_type, socket_ready, ydotool_socket

GNOME = {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME", "WAYLAND_DISPLAY": "wayland-0",
         "XDG_RUNTIME_DIR": "/run/user/1000", "PATH": os.environ.get("PATH", "")}
TOOLS = {"ydotool": "/usr/bin/ydotool", "wtype": "/usr/bin/wtype", "xdotool": "/usr/bin/xdotool"}


def which_only(*names):
    return lambda name: TOOLS.get(name) if name in names else None


class SessionDetection(unittest.TestCase):
    def test_session_type_falls_back_to_display_variables(self):
        self.assertEqual(session_type({"XDG_SESSION_TYPE": "wayland"}), "wayland")
        self.assertEqual(session_type({"WAYLAND_DISPLAY": "wayland-0"}), "wayland")
        self.assertEqual(session_type({"DISPLAY": ":0"}), "x11")
        self.assertEqual(session_type({}), "unknown")

    def test_ydotool_socket_follows_client_lookup(self):
        self.assertEqual(ydotool_socket({"YDOTOOL_SOCKET": "/tmp/x"}), Path("/tmp/x"))
        self.assertEqual(ydotool_socket({"XDG_RUNTIME_DIR": "/run/user/1000"}), Path("/run/user/1000/.ydotool_socket"))

    def test_socket_ready_distinguishes_live_stale_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "live"
            server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            server.bind(str(live))
            try:
                self.assertTrue(socket_ready(live))
            finally:
                server.close()
            # The file stays after ydotoold dies; nothing is bound anymore.
            self.assertTrue(live.exists())
            self.assertFalse(socket_ready(live))
            regular = Path(tmp) / "regular"
            regular.write_text("x")
            self.assertFalse(socket_ready(regular))
            self.assertFalse(socket_ready(Path(tmp) / "missing"))

    def test_gnome_with_ydotoold_down_reports_service_without_pressing_keys(self):
        with patch("deckforge.session.shutil.which", side_effect=which_only("ydotool", "wtype")), \
             patch("deckforge.session.socket_ready", return_value=False), \
             patch("deckforge.actions.subprocess.run") as run:
            with self.assertRaisesRegex(ActionError, "deckforge-ydotoold"):
                execute_action({"type": "keys", "keys": ["ctrl", "c"]}, env=GNOME)
        run.assert_not_called()

    def test_gnome_with_ydotoold_up_selects_ydotool(self):
        with patch("deckforge.session.shutil.which", side_effect=which_only("ydotool", "wtype")), \
             patch("deckforge.session.socket_ready", return_value=True):
            choice = select_input_backend(GNOME)
        self.assertEqual((choice["backend"], choice["ready"]), ("ydotool", True))

    def test_x11_uses_xdotool(self):
        with patch("deckforge.session.shutil.which", side_effect=which_only("xdotool")):
            self.assertEqual(actions._shortcut_command(["ctrl", "c"], env={"XDG_SESSION_TYPE": "x11"}),
                             ["/usr/bin/xdotool", "key", "--clearmodifiers", "ctrl+c"])


class ShortcutExecution(unittest.TestCase):
    def setUp(self):
        self.patches = [patch("deckforge.session.shutil.which", side_effect=which_only("ydotool")),
                        patch("deckforge.session.socket_ready", return_value=True)]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in self.patches: p.stop()

    def test_success_passes_the_resolved_socket_to_ydotool(self):
        with patch("deckforge.actions.subprocess.run", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as run:
            message = execute_action({"type": "keys", "keys": ["ctrl", "shift", "t"]}, env=GNOME)
        self.assertEqual(message, "Atalho ctrl+shift+t enviado via ydotool")
        self.assertEqual(run.call_args.args[0], ["/usr/bin/ydotool", "key", "29:1", "42:1", "20:1", "20:0", "42:0", "29:0"])
        self.assertEqual(run.call_args.kwargs["env"]["YDOTOOL_SOCKET"], "/run/user/1000/.ydotool_socket")
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_failure_reports_stdout_detail_and_releases_every_key(self):
        failed = subprocess.CompletedProcess([], 2, b"failed to connect socket `/run/user/1000/.ydotool_socket': Connection refused\n", b"")
        with patch("deckforge.actions.subprocess.run", side_effect=[failed, subprocess.CompletedProcess([], 0)]) as run:
            with self.assertRaisesRegex(ActionError, "Connection refused"):
                execute_action({"type": "keys", "keys": ["alt", "Tab"]}, env=GNOME)
        self.assertEqual(run.call_args_list[1].args[0], ["/usr/bin/ydotool", "key", "15:0", "56:0"])

    def test_timeout_still_releases_modifiers(self):
        with patch("deckforge.actions.subprocess.run",
                   side_effect=[subprocess.TimeoutExpired("ydotool", 5), subprocess.CompletedProcess([], 0)]) as run:
            with self.assertRaises(ActionError):
                execute_action({"type": "keys", "keys": ["ctrl", "s"]}, env=GNOME)
        self.assertEqual(run.call_args_list[1].args[0], ["/usr/bin/ydotool", "key", "31:0", "29:0"])

    def test_rejects_unknown_key_names(self):
        for keys in (["ctrl", "ç"], ["hyper", "a"], [], ["a"] * 5):
            with self.subTest(keys=keys), self.assertRaises(ActionError):
                execute_action({"type": "keys", "keys": keys}, env=GNOME)


class Launching(unittest.TestCase):
    """Real processes (no mocks): exit codes decide success."""

    def test_zero_exit_is_success(self):
        self.assertEqual(execute_action({"type": "command", "argv": ["true"]}), "true concluído (código 0)")

    def test_nonzero_exit_is_a_failure_with_output(self):
        with self.assertRaisesRegex(ActionError, "código 3.*deckforge-test-output"):
            execute_action({"type": "command", "argv": [
                "python3", "-c", "import sys; print('deckforge-test-output', file=sys.stderr); sys.exit(3)"]})

    def test_long_running_program_is_reported_as_started(self):
        message = execute_action({"type": "command", "argv": ["sleep", "5"]}, launch_check=0.2)
        self.assertRegex(message, r"sleep iniciado \(PID \d+, em execução\)")
        pid = int(message.split("PID ")[1].split(",")[0])
        os.kill(pid, 15)
        os.waitpid(pid, 0)

    def test_missing_executable(self):
        with self.assertRaisesRegex(ActionError, "Executável não encontrado"):
            execute_action({"type": "app", "argv": ["deckforge-nao-existe"]})

    def test_xdg_open_failure_is_not_success(self):
        with patch("deckforge.actions.shutil.which", return_value="/usr/bin/xdg-open"), \
             patch("deckforge.actions.subprocess.Popen") as popen:
            popen.return_value.wait.return_value = 4
            with self.assertRaisesRegex(ActionError, "xdg-open terminou com código 4"):
                execute_action({"type": "app", "argv": ["xdg-open", "https://github.com"]})

    def test_bare_url_opens_through_xdg_open_with_argv(self):
        with patch("deckforge.actions.shutil.which", return_value="/usr/bin/xdg-open"), \
             patch("deckforge.actions.subprocess.Popen") as popen:
            popen.return_value.wait.return_value = 0
            execute_action({"type": "app", "argv": ["https://www.youtube.com/"]})
        self.assertEqual(popen.call_args.args[0], ["/usr/bin/xdg-open", "https://www.youtube.com/"])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_shell_command_strings_are_rejected(self):
        for argv in (["bash", "-c", "id"], ["bash", "-lc", "id"], ["sh", "--command", "id"],
                     ["env", "FOO=1", "sh", "-c", "id"], ["zsh", "-x", "-c", "id"]):
            with self.subTest(argv=argv), self.assertRaisesRegex(ActionError, "shell"):
                execute_action({"type": "command", "argv": argv})

    def test_script_argument_is_not_a_shell_command(self):
        self.assertFalse(actions._is_shell_command("/bin/bash", ["bash", "/home/u/script.sh", "-c"]))

    def test_user_text_is_never_split_or_interpreted(self):
        with patch("deckforge.actions.shutil.which", return_value="/usr/bin/xdg-open"), \
             patch("deckforge.actions.subprocess.Popen") as popen:
            popen.return_value.wait.return_value = 0
            execute_action({"type": "app", "argv": ["xdg-open", "https://example.com/?q=a b;$(id)"]})
        self.assertEqual(popen.call_args.args[0], ["/usr/bin/xdg-open", "https://example.com/?q=a b;$(id)"])


class Normalization(unittest.TestCase):
    def test_drops_fields_of_other_types_and_prefixes_urls(self):
        self.assertEqual(normalize_action({"type": "app", "title": "GitHub", "argv": ["https://github.com"], "keys": ["ctrl", "c"]}),
                         {"type": "app", "title": "GitHub", "argv": ["xdg-open", "https://github.com"]})
        self.assertEqual(normalize_action({"type": "keys", "keys": ["ctrl", "c"], "argv": ["x"]}),
                         {"type": "keys", "keys": ["ctrl", "c"]})

    def test_rejects_invalid(self):
        for config in ({"type": "keys", "keys": ["nope"]}, {"type": "app", "argv": []}, {"type": "shell"}):
            with self.subTest(config=config), self.assertRaises(ActionError):
                normalize_action(config)


if __name__ == "__main__":
    unittest.main()
