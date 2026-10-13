from __future__ import annotations
import argparse
import errno
import json
import sys
import time
import os
import subprocess
import tempfile
import webbrowser
import signal
from pathlib import Path
from . import build_id
from .device import capture_profile, doctor, discover
from .daemon import control_nodes, daemon_status, read_events, run_action, run_forever
from .keyboard import keyboard_nodes, read_key_events
from .processes import api_status, die_with_parent, find_processes, pid_alive, port_in_use, terminate
from .store import DATA_HOME, ProfileStore
from .transport import HidTransport

SLOTS = [f"{r}_{c}" for r in range(3) for c in range(5) if not (r == 2 and c > 2)]
# systemd user unit that starts `deckforge serve` with the graphical session
# (scripts/enable-autostart.sh). The daemon-only unit is from older installs.
SERVICE = "deckforge.service"
LEGACY_SERVICE = "deckforge-daemon.service"

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
    daemon.add_argument("--parent-pid", type=int, default=None, help=argparse.SUPPRESS)
    sub.add_parser("serve", help="iniciar editor, API local e daemon de ações")
    sub.add_parser("app", help="abrir o editor e iniciar o daemon local de ações")
    sub.add_parser("stop", help="parar somente os processos DeckForge do usuário atual")
    sub.add_parser("restart", help="parar instâncias antigas e iniciar editor, API e daemon com o código atual "
                                   "(pelo serviço systemd, se a inicialização automática estiver ativa)")
    sub.add_parser("status", help="mostrar dispositivo, captura, backend de atalhos e última ação")
    logs = sub.add_parser("logs", help="mostrar o histórico de eventos do DeckForge")
    logs.add_argument("-n", "--lines", type=int, default=30, help="quantidade de eventos recentes")
    logs.add_argument("-f", "--follow", action="store_true", help="acompanhar novos eventos até Ctrl+C")
    online = sub.add_parser("online", help="permitir ou bloquear o editor online (ulanzi-d200h-linux.vercel.app) neste computador")
    online.add_argument("state", nargs="?", choices=["on", "off", "status"], default="status")
    trigger = sub.add_parser("trigger", help="executar a ação salva de uma posição sem usar o dispositivo (teste de software)")
    trigger.add_argument("slot", choices=SLOTS, help="posição no formato linha_coluna, por exemplo 0_0")
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
        if args.keyboard and daemon_status()["running"]:
            print("Aviso: o daemon DeckForge está ativo e isola o teclado do D200H; esta captura não receberá teclas. Rode `deckforge stop` antes.", file=sys.stderr)
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
        except OSError as exc:
            if exc.errno == errno.EBUSY:
                print("Outro processo já isolou o teclado do D200H (provavelmente o daemon DeckForge). Rode `deckforge stop` e tente novamente.", file=sys.stderr)
                return 2
            raise
        return 0
    if args.command == "daemon":
        try:
            run_forever(ProfileStore(), intercept_keyboard=args.intercept_keyboard, parent_pid=args.parent_pid)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr); return 2
        return 0
    if args.command == "stop":
        return _stop()
    if args.command == "restart":
        if _stop() != 0:
            return 1
        if _service_enabled():
            return _start_service()
        return _serve(open_browser=False)
    if args.command in {"serve", "app"}:
        return _serve(open_browser=args.command == "app")
    if args.command == "status":
        from .status import collect_status
        report = collect_status(ProfileStore(), events=5)
        report.pop("devices", None)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.command == "logs":
        return _logs(args.lines, args.follow)
    if args.command == "online":
        store = ProfileStore()
        if args.state != "status":
            store.save_settings({"online_editor": args.state == "on"})
        from .api import HOSTED_ORIGINS
        sites = ", ".join(sorted(HOSTED_ORIGINS))
        if store.settings()["online_editor"]:
            print(f"Editor online PERMITIDO: {sites} pode configurar o D200H deste computador pela API local "
                  "(127.0.0.1:8765). Para bloquear: deckforge online off")
        else:
            print(f"Editor online BLOQUEADO: {sites} só vê que o DeckForge está instalado. Para permitir: deckforge online on")
        return 0
    if args.command == "trigger":
        store = ProfileStore()
        ok, message = run_action(store, args.slot, "teste via `deckforge trigger`")
        print(("OK: " if ok else "FALHOU: ") + message)
        return 0 if ok else 1


def _systemctl(*args: str, timeout: float = 10) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["systemctl", "--user", *args], stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _service_enabled() -> bool:
    result = _systemctl("is-enabled", SERVICE, timeout=5)
    return result is not None and result.stdout.strip() == "enabled"


def _start_service() -> int:
    """Start `deckforge serve` under systemd, so it outlives this terminal."""
    result = _systemctl("start", SERVICE, timeout=30)
    if result is None or result.returncode != 0:
        detail = (result.stderr.strip() if result is not None else "") or "systemctl indisponível"
        print(f"Não foi possível iniciar {SERVICE}: {detail}", file=sys.stderr)
        return 1
    deadline = time.monotonic() + 20
    running = None
    while time.monotonic() < deadline:
        running = api_status()
        if running is not None:
            break
        active = _systemctl("is-active", SERVICE, timeout=5)
        if active is not None and active.stdout.strip() == "failed":
            break
        time.sleep(.25)
    if running is None:
        print(f"{SERVICE} não respondeu em 127.0.0.1:8765. Veja `journalctl --user -u {SERVICE} -n 40`.", file=sys.stderr)
        return 1
    if running.get("build") != build_id():
        print(f"Aviso: o serviço usa outra instalação do DeckForge (build {running.get('build') or 'desconhecido'}). "
              "Rode ./scripts/enable-autostart.sh neste projeto para apontá-lo para este código.", file=sys.stderr)
    print(f"DeckForge iniciado pelo systemd ({SERVICE}); ele volta sozinho a cada login.\n"
          "Editor: http://127.0.0.1:8765 · eventos: `deckforge logs -f` · "
          f"servidor: `journalctl --user -u {SERVICE} -f`", flush=True)
    return 0


def _stop() -> int:
    """Stop every DeckForge process of this user and verify nothing is left."""
    # Through systemd first: a killed service would otherwise be restarted.
    for unit in (SERVICE, LEGACY_SERVICE):
        _systemctl("stop", unit)
    processes = find_processes()
    for item in processes:
        print(f"Encerrando PID {item['pid']} (deckforge {item['role']})", flush=True)
    result = terminate([item["pid"] for item in processes])
    for pid in result["killed"]:
        print(f"PID {pid} não respondeu ao SIGTERM em 5 s e foi finalizado com SIGKILL.", flush=True)
    problems = []
    if result["remaining"]:
        problems.append(f"PID(s) ainda ativos: {', '.join(map(str, result['remaining']))}")
    if daemon_status()["running"]:
        problems.append("o lock do daemon continua ocupado")
    deadline = time.monotonic() + 2
    while port_in_use() and time.monotonic() < deadline:
        time.sleep(.1)
    if port_in_use():
        problems.append("a porta 127.0.0.1:8765 continua ocupada por outro processo (confira com `ss -ltnp 'sport = :8765'`)")
    if problems:
        print("DeckForge não foi totalmente encerrado: " + "; ".join(problems) + ".", file=sys.stderr, flush=True)
        return 1
    print("Processos DeckForge do usuário encerrados; porta 8765 e lock do daemon livres." if processes
          else "Nenhum processo DeckForge ativo; porta 8765 e lock do daemon livres.", flush=True)
    return 0


def _serve(open_browser: bool) -> int:
    try:
        import uvicorn
    except ImportError:
        print("Instale dependências com `python -m pip install -e .`", file=sys.stderr); return 2
    current_build = build_id()
    running_api = api_status()
    if running_api is not None:
        if running_api.get("build") == current_build:
            print("DeckForge já está atendendo em http://127.0.0.1:8765.", flush=True)
            if open_browser:
                webbrowser.open("http://127.0.0.1:8765")
            return 0
        print(f"Uma instância antiga do DeckForge (PID {running_api.get('pid', 'desconhecido')}) está atendendo em 8765. "
              "Rode `deckforge restart` para substituí-la pelo código atual.", file=sys.stderr)
        return 2
    if port_in_use():
        print("A porta 127.0.0.1:8765 está ocupada por outro processo. Rode `deckforge stop` ou confira `ss -ltnp 'sport = :8765'`.", file=sys.stderr)
        return 2

    child = None
    current = daemon_status()
    if current["running"]:
        expected = capture_profile()["mode"]
        running_mode = current.get("mode")
        compatible = running_mode == "auto" or running_mode == expected
        if current.get("build") == current_build and compatible:
            print(f"Usando daemon DeckForge ativo (PID {current.get('pid')}, modo {running_mode}).", flush=True)
        else:
            # An outdated or incompatible daemon would keep the keyboard grabbed
            # with old code; replace it instead of silently reusing it.
            pid = current.get("pid")
            print(f"Substituindo daemon antigo (PID {pid}, build {current.get('build') or 'desconhecido'}).", flush=True)
            if isinstance(pid, int) and pid_alive(pid):
                terminate([pid], grace=3)
            if daemon_status()["running"]:
                print("O daemon antigo não liberou o lock. Rode `deckforge stop` e tente de novo.", file=sys.stderr)
                return 2
            current = {"running": False}
    if not current["running"]:
        log_dir = DATA_HOME / "logs"
        log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(log_dir, 0o700)
        daemon_log = (log_dir / "daemon.log").open("a", encoding="utf-8")
        try:
            # Own session: the terminal's Ctrl+C reaches only `serve`, which then
            # stops this child itself. PDEATHSIG covers a killed parent.
            child = subprocess.Popen([sys.executable, "-m", "deckforge.cli", "daemon", "--parent-pid", str(os.getpid())],
                                     stdin=subprocess.DEVNULL, stdout=daemon_log, stderr=subprocess.STDOUT,
                                     start_new_session=True, close_fds=True, preexec_fn=die_with_parent)
        except OSError as exc:
            print(f"Não foi possível iniciar o daemon de ações: {exc}", file=sys.stderr)
            return 2
        finally:
            daemon_log.close()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and child.poll() is None and daemon_status().get("pid") != child.pid:
            time.sleep(.1)
        if child.poll() is not None or daemon_status().get("pid") != child.pid:
            print(f"O daemon não iniciou (código {child.poll()}). Consulte {log_dir / 'daemon.log'}.", file=sys.stderr)
            _stop_child(child)
            return 2
        print(f"Daemon de ações iniciado (PID {child.pid}); logs em {log_dir / 'daemon.log'}.", flush=True)

    # uvicorn handles SIGTERM gracefully and then re-raises it with the handler
    # that was installed before it started. With the default handler the process
    # would die there and skip the cleanup below, orphaning the daemon.
    def exit_on_signal(signum, _frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, exit_on_signal)
    signal.signal(signal.SIGHUP, lambda *_: os.kill(os.getpid(), signal.SIGTERM))
    if open_browser:
        import threading
        threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:8765")).start()
    try:
        uvicorn.run("deckforge.api:app", host="127.0.0.1", port=8765, log_level="info", timeout_graceful_shutdown=3)
    except KeyboardInterrupt:
        pass
    finally:
        if child is not None:
            _stop_child(child)
    return 0


def _stop_child(child: subprocess.Popen) -> None:
    if child.poll() is None:
        terminate([child.pid], grace=3)
    try:
        child.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    if child.poll() is None:
        print(f"Aviso: o daemon PID {child.pid} não encerrou.", file=sys.stderr, flush=True)
    else:
        print(f"Daemon de ações PID {child.pid} encerrado.", flush=True)


def _logs(lines: int, follow: bool) -> int:
    store = ProfileStore()

    def show(event):
        print(f"{event['created_at']} UTC  {event['kind']:<7} {event['message']}", flush=True)

    events = list(reversed(store.recent_events(max(1, lines))))
    for event in events:
        show(event)
    last_id = events[-1]["id"] if events else 0
    try:
        while follow:
            time.sleep(.5)
            for event in reversed(store.recent_events(100)):
                if event["id"] > last_id:
                    show(event); last_id = event["id"]
    except KeyboardInterrupt:
        pass
    return 0

if __name__ == "__main__": raise SystemExit(main())

