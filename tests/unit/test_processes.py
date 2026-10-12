import isolation  # noqa: F401  (must precede deckforge imports)
import asyncio
import os
import signal
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
