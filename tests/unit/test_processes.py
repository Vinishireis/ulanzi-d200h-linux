import isolation  # noqa: F401  (must precede deckforge imports)
import asyncio
import contextlib
import io
import os
import signal
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from deckforge import build_id, cli
from deckforge.processes import deckforge_role, pid_alive, terminate

REPO = Path(__file__).resolve().parents[2]


class RoleMatching(unittest.TestCase):
    def test_matches_only_deckforge_service_commands(self):
        cases = {
            ("/home/u/p/.venv/bin/python", "./.venv/bin/deckforge", "serve"): "serve",
            ("/home/u/.local/bin/deckforge", "daemon", "--intercept-keyboard"): "daemon",
            ("/usr/bin/python3", "-m", "deckforge.cli", "daemon", "--parent-pid", "12"): "daemon",
            ("/usr/bin/python3", "-m", "deckforge.cli", "app"): "app",
            ("/usr/bin/python3", "-m", "deckforge.cli", "listen", "--keyboard"): None,
            ("vim", "notes/deckforge", "serve"): None,
            ("/usr/bin/grep", "deckforge", "serve"): None,
            ("bash", "-c", "echo deckforge serve"): None,
        }
        for argv, role in cases.items():
            with self.subTest(argv=argv):
                self.assertEqual(deckforge_role(list(argv)), role)


class Termination(unittest.TestCase):
    def test_sigterm_then_sigkill(self):
        polite = subprocess.Popen(["sleep", "30"])
        stubborn = subprocess.Popen([sys.executable, "-c",
                                     "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ok', flush=True); time.sleep(30)"],
                                    stdout=subprocess.PIPE, text=True)
        stubborn.stdout.readline()
        try:
            result = terminate([polite.pid, stubborn.pid], grace=.5, kill_grace=2)
            polite.wait(2); stubborn.wait(2)
            self.assertEqual(result, {"killed": [stubborn.pid], "remaining": []})
            self.assertEqual(polite.returncode, -signal.SIGTERM)
            self.assertEqual(stubborn.returncode, -signal.SIGKILL)
        finally:
            for proc in (polite, stubborn):
                if proc.poll() is None: proc.kill()
            stubborn.stdout.close()


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@unittest.skipUnless(__import__("importlib").util.find_spec("websockets"), "websockets não instalado")
class ApiShutdown(unittest.TestCase):
    def test_api_exits_promptly_on_sigterm_with_an_editor_connected(self):
        """Regression for `deckforge serve` staying alive after `deckforge stop`."""
        import websockets
        port = _free_port()
        env = {**os.environ, "PYTHONPATH": str(REPO / "services/daemon")}
        server = subprocess.Popen([sys.executable, "-c",
                                   f"import uvicorn; uvicorn.run('deckforge.api:app', host='127.0.0.1', port={port}, log_level='warning')"],
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        async def scenario():
            for _ in range(100):
                try:
                    client = await websockets.connect(f"ws://127.0.0.1:{port}/api/live", origin="http://127.0.0.1:8765")
                    break
                except OSError:
                    await asyncio.sleep(.1)
            else:
                self.fail("API de teste não iniciou")
            await asyncio.sleep(1.2)  # the handler is idle, polling the event log
            started = time.monotonic()
            server.send_signal(signal.SIGTERM)
            while server.poll() is None and time.monotonic() - started < 6:
                await asyncio.sleep(.05)
            await client.close()
            return time.monotonic() - started

        try:
            elapsed = asyncio.run(scenario())
            self.assertIsNotNone(server.poll(), "a API continuou ativa após SIGTERM com o editor conectado")
            self.assertLess(elapsed, 3)
        finally:
            if server.poll() is None:
                server.kill()
            server.wait()


class PidState(unittest.TestCase):
    def test_zombie_is_not_alive(self):
        child = subprocess.Popen(["true"])
        deadline = time.monotonic() + 2
        while pid_alive(child.pid) and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertFalse(pid_alive(child.pid))  # exited but not yet reaped
        child.wait()


class Autostart(unittest.TestCase):
    """`restart` and `stop` with the systemd user unit that starts DeckForge at login."""

    def systemctl(self, enabled):
        calls = []

        def fake(*args, timeout=10):
            calls.append(args)
            out = {"is-enabled": "enabled" if enabled else "disabled", "is-active": "active"}.get(args[0], "")
            return subprocess.CompletedProcess(["systemctl", "--user", *args], 0, stdout=out + "\n", stderr="")
        return fake, calls

    def restart(self, enabled):
        fake, calls = self.systemctl(enabled)
        with mock.patch.object(cli, "_systemctl", fake), mock.patch.object(cli, "_stop", return_value=0), \
             mock.patch.object(cli, "_serve", return_value=0) as serve, \
             mock.patch.object(cli, "api_status", return_value={"build": build_id()}), \
             mock.patch.object(sys, "argv", ["deckforge", "restart"]), contextlib.redirect_stdout(io.StringIO()):
            code = cli.main()
        return code, calls, serve

    def test_restart_goes_through_systemd_when_autostart_is_enabled(self):
        code, calls, serve = self.restart(enabled=True)
        self.assertEqual(code, 0)
        self.assertIn(("start", cli.SERVICE), calls)
        serve.assert_not_called()  # nothing tied to this terminal

    def test_restart_without_autostart_serves_in_this_terminal(self):
        code, calls, serve = self.restart(enabled=False)
        self.assertEqual(code, 0)
        serve.assert_called_once_with(open_browser=False)
        self.assertNotIn(("start", cli.SERVICE), calls)

    def test_stop_stops_the_units_before_killing_processes(self):
        fake, calls = self.systemctl(enabled=True)
        with mock.patch.object(cli, "_systemctl", fake), mock.patch.object(cli, "find_processes", return_value=[]), \
             mock.patch.object(cli, "daemon_status", return_value={"running": False}), \
             mock.patch.object(cli, "port_in_use", return_value=False), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli._stop(), 0)
        self.assertEqual(calls, [("stop", cli.SERVICE), ("stop", cli.LEGACY_SERVICE)])

    def test_unit_starts_serve_with_the_graphical_session(self):
        unit = (REPO / "installers/systemd/deckforge.service").read_text()
        # scripts/enable-autostart.sh rewrites exactly this line to the project's .venv.
        self.assertRegex(unit, r"(?m)^ExecStart=%h/\.local/bin/deckforge serve$")
        self.assertRegex(unit, r"(?m)^WantedBy=graphical-session\.target$")
        self.assertRegex(unit, r"(?m)^PartOf=graphical-session\.target$")


if __name__ == "__main__":
    unittest.main()
