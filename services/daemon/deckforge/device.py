"""Read-only Linux USB/HID discovery for the Ulanzi D200H."""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

VID, PID = "2207", "0019"


def _run(argv: list[str], timeout: float = 3) -> dict[str, Any]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        return {"available": True, "returncode": p.returncode, "stdout": p.stdout.strip(), "stderr": p.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "error": str(exc)}


def discover() -> list[dict[str, Any]]:
    """Enumerate matching USB parents and their hidraw children via sysfs."""
    found: list[dict[str, Any]] = []
    for node in Path("/sys/bus/usb/devices").glob("*"):
        try:
            if (node / "idVendor").read_text().strip().lower() != VID or (node / "idProduct").read_text().strip().lower() != PID:
                continue
        except OSError:
            continue
        interfaces = []
        for interface in node.parent.glob(node.name + ":*"):
            if not (interface / "bInterfaceNumber").exists():
                continue
            number = (interface / "bInterfaceNumber").read_text().strip()
            klass = (interface / "bInterfaceClass").read_text().strip()
            protocol = (interface / "bInterfaceProtocol").read_text().strip()
            endpoints = []
            for ep in interface.glob("ep_*"):
                try:
                    endpoints.append({name: (ep / attr).read_text().strip() for name, attr in (("address", "bEndpointAddress"), ("direction", "direction"), ("type", "type"), ("max_packet_size", "wMaxPacketSize"))})
                except OSError:
                    continue
            hidraw = []
            for candidate in interface.glob("**/hidraw/hidraw*"):
                dev = Path("/dev") / candidate.name
                try:
                    mode = dev.stat().st_mode
                    import stat
                    readable = os.access(dev, os.R_OK)
                    writable = os.access(dev, os.W_OK)
                    perms = stat.filemode(mode)
                except OSError:
                    readable = writable = False
                    perms = "missing"
                descriptor = candidate / "device/report_descriptor"
                try:
                    desc = descriptor.read_bytes()
                    descriptor_info = {"bytes": len(desc), "report_ids": list(desc[i + 1] for i, b in enumerate(desc[:-1]) if b == 0x85)}
                except OSError:
                    descriptor_info = None
                hidraw.append({"path": str(dev), "readable": readable, "writable": writable, "permissions": perms, "descriptor": descriptor_info})
            interfaces.append({"number": number, "class": klass, "protocol": protocol, "endpoints": endpoints, "hidraw": hidraw})
        try:
            bcd_device = (node / "bcdDevice").read_text().strip()
        except OSError:
            bcd_device = None
        try:
            manufacturer = (node / "manufacturer").read_text().strip()
        except OSError:
            manufacturer = None
        try:
            product = (node / "product").read_text().strip()
        except OSError:
            product = None
        try:
            serial = (node / "serial").read_text().strip()
        except OSError:
            serial = None
        found.append({"vid": VID, "pid": PID, "bus_path": node.name, "manufacturer": manufacturer, "product": product, "serial": serial, "bcd_device": bcd_device, "firmware_version": None, "interfaces": interfaces})
    return found


def d200_write_target(devices: list[dict[str, Any]] | None = None) -> tuple[str | None, str | None]:
    """Return a hidraw node only for the documented two-interface D200 profile.

    VID/PID alone is shared by revisions with different protocols. Require the
    control endpoint pair documented by the Linux D200 implementation and a
    separate boot-keyboard interface before enabling display writes.
    """
    for device in discover() if devices is None else devices:
        interfaces = device["interfaces"]
        control = next((item for item in interfaces if item["number"].lower() == "00"), None)
        keyboard = next((item for item in interfaces if item["number"].lower() == "01"), None)
        if len(interfaces) != 2 or not control or not keyboard:
            continue
        try:
            addresses = {int(endpoint["address"].lower().removeprefix("0x"), 16) for endpoint in control["endpoints"]}
            keyboard_protocol = int(keyboard["protocol"], 16)
        except (TypeError, ValueError):
            continue
        if control["class"] != "03" or not {0x82, 0x01}.issubset(addresses):
            continue
        if keyboard["class"] != "03" or keyboard_protocol != 1:
            continue
        node = next((item for item in control["hidraw"] if item["writable"]), None)
        if node:
            return node["path"], None
        return None, "O perfil D200 é compatível, mas o usuário não tem permissão de escrita no hidraw da interface 0."
    return None, "Protocolo de escrita bloqueado: a topologia USB não corresponde ao perfil D200 de duas interfaces validado."


def capture_profile(devices: list[dict[str, Any]] | None = None) -> dict[str, str]:
    """Choose the physical input channel from the observed USB topology.

    D200H revisions with three interfaces publish slot IDs on vendor HID
    interface 0. Earlier two-interface D200 units only publish firmware
    macros through their boot keyboard (interface 1).
    """
    devices = discover() if devices is None else devices
    for device in devices:
        interfaces = {str(item["number"]).lower().zfill(2): item for item in device["interfaces"]}
        control = interfaces.get("00")
        keyboard = interfaces.get("02")
        if control and keyboard and control.get("class") == "03" and keyboard.get("class") == "03":
            try:
                endpoints = {int(ep["address"].lower().removeprefix("0x"), 16) for ep in control["endpoints"]}
            except (KeyError, TypeError, ValueError):
                endpoints = set()
            if {0x81, 0x02}.issubset(endpoints) and keyboard.get("protocol") == "01":
                return {"mode": "vendor_hid", "profile": "D200H 3-interface", "reason": "slots chegam pelo HID de controle (interface 0)"}

    for device in devices:
        interfaces = {str(item["number"]).lower().zfill(2): item for item in device["interfaces"]}
        control, keyboard = interfaces.get("00"), interfaces.get("01")
        if len(interfaces) == 2 and control and keyboard and control.get("class") == "03" and keyboard.get("class") == "03":
            if control.get("protocol") == "00" and keyboard.get("protocol") == "01":
                return {"mode": "keyboard_macros", "profile": "D200 2-interface", "reason": "cliques chegam como eventos HID por slot (interface 0) em modo host e como macros de teclado (interface 1) no modo de fábrica"}

    return {"mode": "unavailable", "profile": "desconhecido", "reason": "topologia USB não corresponde a um perfil de entrada validado"}


def environment() -> dict[str, Any]:
    return {
        "session_type": os.environ.get("XDG_SESSION_TYPE", "unknown"),
        "desktop": os.environ.get("XDG_CURRENT_DESKTOP", "unknown"),
        "display": bool(os.environ.get("DISPLAY")),
        "wayland_display": bool(os.environ.get("WAYLAND_DISPLAY")),
    }


def doctor() -> dict[str, Any]:
    devices = discover()
    lsusb = _run(["lsusb", "-d", f"{VID}:{PID}"])
    tree = _run(["lsusb", "-t"])
    verbose = _run(["lsusb", "-v", "-d", f"{VID}:{PID}"], 5)
    control_path = next((h["path"] for d in devices for i in d["interfaces"] if i["number"].lower() == "00" for h in i["hidraw"]), None)
    udev = _run(["udevadm", "info", "--query=all", "--name=" + control_path]) if control_path else {"available": False, "error": "Interface HID de controle do D200H não localizada"}
    session = environment()
    dmesg = _run(["dmesg", "--ctime", "--level=warn,err"], 3)
    conflict_processes = []
    for process_name in ("deckforge", "d200h", "opendeck", "ulanzi-linux"):
        result = _run(["pgrep", "-ax", process_name], 2)
        if result.get("available"):
            for line in result.get("stdout", "").splitlines():
                try: pid = int(line.split(None, 1)[0])
                except (ValueError, IndexError): continue
                if pid != os.getpid(): conflict_processes.append(line)
    permissions = any(h["readable"] for d in devices for i in d["interfaces"] if i["number"].lower() == "00" for h in i["hidraw"])
    dependencies = {name: shutil.which(name) is not None for name in ("lsusb", "udevadm", "xdotool", "wtype", "ydotool")}
    from .keyboard import keyboard_nodes
    from .session import input_backend_status
    keyboards = keyboard_nodes()
    capture = capture_profile(devices)
    backend = input_backend_status()
    recommendations = []
    if not devices:
        recommendations.append("Conecte o D200H diretamente ou confira `lsusb`; VID:PID esperado 2207:0019.")
    elif not permissions:
        recommendations.append("O usuário atual não pode ler os nós hidraw. Revise a regra udev documentada em installers/udev; nenhuma permissão foi alterada.")
    if devices and capture["mode"] == "keyboard_macros" and not any(n["readable"] for n in keyboards):
        recommendations.append("A interface de teclado (interface 01) do D200H não pode ser lida; instale a regra udev 99-deckforge-d200h.rules e reconecte.")
    if not backend["ready"]:
        recommendations.append(f"Atalhos de teclado indisponíveis: {backend['detail']}")
    return {"device_found": bool(devices), "devices": devices, "control_accessible": permissions,
            "input_capture": capture, "keyboard_nodes": keyboards, "input_backend": backend,
            "dependencies": dependencies, "conflicting_processes": conflict_processes, "commands": {"lsusb": lsusb, "lsusb_tree": tree, "lsusb_verbose": verbose, "udev": udev, "dmesg_warnings": dmesg}, "session": session, "recommendations": recommendations}


def parse_button_report(report: bytes, strict: bool = True) -> dict[str, Any] | None:
    """Parse only documented D200H button events; ignore all other input.

    ``strict`` also checks the first payload byte (1 for keys, 0 for the clock
    area, per the D200H reference). The two-interface D200 reference calls it
    firmware-internal state, so that path parses with ``strict=False``.
    """
    if len(report) < 12 or report[:4] != b"||\x01\x01":
        return None
    category, slot, valid, pressed = report[8:12]
    if valid != 1 or slot > 13 or pressed not in (0, 1):
        return None
    if strict and ((slot == 13 and category != 0) or (slot < 13 and category != 1)):
        return None
    # Slot 13 is the wide display (2_3 + 2_4); it is configurable like a key.
    return {"slot": slot, "category": category, "pressed": pressed == 1, "key": "2_3" if slot == 13 else f"{slot // 5}_{slot % 5}"}

