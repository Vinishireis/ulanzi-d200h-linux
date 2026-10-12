from __future__ import annotations
import argparse
import json
import sys
import time
import os
import subprocess
import tempfile
import webbrowser
import signal
from urllib.error import URLError
from urllib.request import urlopen
from pathlib import Path
from .device import capture_profile, doctor, discover
from .daemon import control_nodes, daemon_status, read_events, run_forever
from .keyboard import keyboard_nodes, read_key_events
from .store import DATA_HOME, ProfileStore
from .transport import HidTransport

def main():
    parser = argparse.ArgumentParser(prog="deckforge", description="Gerencie o Ulanzi D200H no Linux")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="diagnóstico local somente leitura")
    sub.add_parser("devices", help="listar dispositivos USB e interfaces HID")
    listen = sub.add_parser("listen", help="capturar eventos em modo somente leitura")
    listen.add_argument("--seconds", type=int, default=0, help="parar após N segundos; 0 mantém até Ctrl+C")
    listen_mode = listen.add_mutually_exclusive_group()
    listen_mode.add_argument("--raw", action="store_true", help="mostrar relatórios HID em hexadecimal para diagnóstico do protocolo")
    listen_mode.add_argument("--keyboard", action="store_true", help="capturar eventos da interface de teclado do D200H")
    listen.add_argument("--grab", action="store_true", help="isolar temporariamente o teclado do D200H para os atalhos não chegarem aos aplicativos; use somente com --keyboard")
    diagnose = sub.add_parser("diagnose", help="criar relatório de diagnóstico")
    diagnose.add_argument("--export", nargs="?", const="deckforge-diagnose.json", help="caminho de exportação JSON")
    daemon = sub.add_parser("daemon", help="executar ações de perfil ao receber eventos físicos")
    daemon.add_argument("--intercept-keyboard", action="store_true", default=None, help="forçar captura dos macros do teclado (modo automático é recomendado)")
    sub.add_parser("serve", help="iniciar editor, API local e daemon de ações")
    sub.add_parser("app", help="abrir o editor e iniciar o daemon local de ações")
    sub.add_parser("stop", help="parar somente os processos DeckForge do usuário atual")
    args = parser.parse_args()
    if args.command == "listen" and args.grab and not args.keyboard:
        parser.error("--grab requer --keyboard")
    if args.command == "devices":
        print(json.dumps(discover(), ensure_ascii=False, indent=2)); return
    if args.command == "doctor":
        report = doctor(); print(json.dumps(report, ensure_ascii=False, indent=2))
        if report["device_found"] and not report["control_accessible"]: return 2
        return 0
    if args.command == "diagnose":
        report = doctor(); rendered = json.dumps(report, ensure_ascii=False, indent=2)
        if args.export:
            serials = [d.get("serial") for d in report.get("devices", []) if d.get("serial")]
            for serial in serials: rendered = rendered.replace(serial, "[REDACTED]")
            for d in report.get("devices", []): d["serial"] = "[REDACTED]" if d.get("serial") else None
            rendered = json.dumps(report, ensure_ascii=False, indent=2)
            path = Path(args.export).expanduser(); path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix=".deckforge-report-", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as report_file:
                    report_file.write(rendered + "\n")
                os.chmod(temp_name, 0o600)
                os.replace(temp_name, path)
            finally:
                if os.path.exists(temp_name): os.unlink(temp_name)
            print(f"Relatório salvo em {path}")
        else: print(rendered)
        return 0
    if args.command == "listen":
        nodes = [n for n in (keyboard_nodes() if args.keyboard else control_nodes()) if n["readable"]]
        if not nodes:
            interface = "de teclado" if args.keyboard else "de controle HID"
            print(f"Interface {interface} do D200H não acessível para leitura. Revise a regra udev e reconecte o dispositivo.", file=sys.stderr); return 2
        end = time.monotonic() + args.seconds if args.seconds else None
        label = "teclado" if args.keyboard else "controle HID"
        grab_note = " (isolado temporariamente)" if args.grab else ""
        print(f"Captura somente leitura da interface {label}{grab_note} em {nodes[0]['path']}; Ctrl+C para parar.", flush=True)
        if args.grab:
            print("Os atalhos do D200H não serão enviados aos aplicativos enquanto esta captura estiver aberta. Ctrl+C no teclado principal encerra e libera o dispositivo.", flush=True)
        try:
            stop = (lambda: time.monotonic() >= end) if end else None
            if args.keyboard:
                for event in read_key_events(nodes[0]["path"], stop, grab=args.grab):
                    print(json.dumps(event, ensure_ascii=False), flush=True)
            elif args.raw:
                for report in HidTransport(nodes[0]["path"]).read_reports(stop):
                    print(json.dumps({"length": len(report), "raw_hex": report.hex()}), flush=True)
            else:
                for event in read_events(nodes[0]["path"], stop):
                    print(json.dumps(event, ensure_ascii=False), flush=True)
        except KeyboardInterrupt: pass
        return 0
    if args.command == "daemon": run_forever(ProfileStore(), intercept_keyboard=args.intercept_keyboard); return 0
    if args.command == "stop":
        try:
            subprocess.run(["systemctl", "--user", "stop", "deckforge-daemon.service"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
        current_pid = os.getpid()
        targets: list[int] = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit() or int(entry.name) == current_pid:
                continue
            try:
                if entry.stat().st_uid != os.getuid():
                    continue
                argv = [part.decode(errors="replace") for part in (entry / "cmdline").read_bytes().split(b"\0") if part]
            except (OSError, ProcessLookupError):
                continue
            if "deckforge.cli" not in argv and not any(Path(part).name == "deckforge" for part in argv):
                continue
            if not any(arg in {"serve", "app", "daemon"} for arg in argv):
                continue
            targets.append(int(entry.name))
        for pid in targets:
            try: os.kill(pid, signal.SIGTERM)
            except ProcessLookupError: pass
        deadline = time.monotonic() + 3
        while targets and time.monotonic() < deadline:
            targets = [pid for pid in targets if Path(f"/proc/{pid}").exists()]
            if targets: time.sleep(.1)
        if targets:
            print(f"DeckForge recebeu SIGTERM; ainda finalizando PID(s): {', '.join(map(str, targets))}.", flush=True)
        else:
            print("Processos DeckForge do usuário encerrados.", flush=True)
        return 0
    if args.command in {"serve", "app"}:
        try:
            import uvicorn
        except ImportError:
            print("Instale dependências com `python -m pip install -e .`", file=sys.stderr); return 2
        try:
            with urlopen("http://127.0.0.1:8765/api/status", timeout=.4) as response:
                running_api = response.status == 200
        except (URLError, TimeoutError, OSError):
            running_api = False
        if running_api:
            print("DeckForge já está atendendo em http://127.0.0.1:8765.", flush=True)
            if args.command == "app":
                webbrowser.open("http://127.0.0.1:8765")
            return 0
        # Never report a working editor while a legacy daemon silently owns
        # the lock in non-intercept mode. Reuse the current daemon only when
        # its mode is known to capture physical keyboard macros.
        current = daemon_status()
        if current["running"]:
            expected = capture_profile()["mode"]
            running_mode = current.get("mode")
            if running_mode is None or (running_mode != "auto" and running_mode != expected):
                print("O daemon ativo é antigo ou usa uma captura incompatível com o dispositivo. Rode `deckforge stop` e depois `deckforge serve` para reiniciá-lo com detecção automática.", file=sys.stderr)
                return 2
        if not current["running"]:
            log_dir = DATA_HOME / "logs"
            log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(log_dir, 0o700)
            daemon_log = (log_dir / "daemon.log").open("a", encoding="utf-8")
            try:
                child = subprocess.Popen([sys.executable, "-m", "deckforge.cli", "daemon"],
                                         stdin=subprocess.DEVNULL, stdout=daemon_log, stderr=subprocess.STDOUT,
                                         start_new_session=True, close_fds=True)
            except OSError as exc:
                print(f"Não foi possível iniciar o daemon de ações: {exc}", file=sys.stderr)
                return 2
            finally:
                daemon_log.close()
            time.sleep(0.25)
            if child.poll() is not None:
                print(f"O daemon encerrou durante a inicialização (código {child.returncode}). Consulte {log_dir / 'daemon.log'}.", file=sys.stderr)
                return 2
            print(f"Daemon de ações iniciado (PID {child.pid}); logs em {log_dir / 'daemon.log'}.", flush=True)
        else:
            print(f"Usando daemon de interceptação ativo (PID {current.get('pid')}).", flush=True)
        if args.command == "app":
            import threading
            threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:8765")).start()
        uvicorn.run("deckforge.api:app", host="127.0.0.1", port=8765, log_level="info"); return 0

if __name__ == "__main__": raise SystemExit(main())

