"""D200 two-interface display image upload, isolated from read-only input."""
from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path
from threading import Lock
from typing import Any

from PIL import Image, ImageOps

from .device import d200_write_target
from .store import DATA_HOME, ProfileStore

ICON_SIZE = (196, 196)
FRAME_SIZE = 1024
MAX_FIRST_PAYLOAD = 1016
MAX_ARCHIVE_SIZE = 196 * 1024
SET_BUTTONS = 0x0001
_WRITE_LOCK = Lock()


def _icon_root() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    candidates = (
        repo_root / "apps/desktop/public/icons",
        DATA_HOME / "ui/icons",
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    raise RuntimeError("Biblioteca local de ícones não encontrada; instale os arquivos da interface DeckForge.")


def _render_icon(config: dict[str, Any]) -> bytes:
    rel = config.get("icon")
    if not isinstance(rel, str) or not rel or len(rel) > 180:
        raise ValueError("O perfil não contém um caminho de ícone válido.")
    if rel.startswith("app:"):
        from .app_catalog import application_icon_png
        try:
            raster = application_icon_png(rel[4:], ICON_SIZE[0])
        except FileNotFoundError as exc:
            raise ValueError(str(exc)) from exc
    else:
        root = _icon_root()
        path = (root / rel).resolve()
        if not path.is_relative_to(root) or path.suffix.lower() != ".svg" or not path.is_file():
            raise ValueError("O ícone precisa ser um SVG local da biblioteca DeckForge.")
        if path.stat().st_size > 512_000:
            raise ValueError("O arquivo SVG ultrapassa o limite de 512 KB.")
        svg = path.read_bytes()
        try:
            import cairosvg
        except ImportError as exc:
            raise RuntimeError("Renderizador SVG ausente. Atualize o ambiente com `python -m pip install -e .`.") from exc
        raster = cairosvg.svg2png(bytestring=svg, output_width=ICON_SIZE[0], output_height=ICON_SIZE[1])
    color = config.get("color", "#101116")
    if not isinstance(color, str) or len(color) != 7 or not color.startswith("#"):
        color = "#101116"
    background = tuple(bytes.fromhex(color[1:])) + (255,)
    with Image.open(io.BytesIO(raster)) as source:
        source = source.convert("RGBA")
        # Match the editor preview: these solid logos are shown as white
        # marks over the chosen key color, regardless of source fill color.
        white = Image.new("RGBA", source.size, (255, 255, 255, 0))
        white.putalpha(source.getchannel("A"))
        source = white
        fitted = ImageOps.contain(source, (186, 186), Image.Resampling.LANCZOS)
        tile = Image.new("RGBA", ICON_SIZE, background)
        tile.alpha_composite(fitted, ((196 - fitted.width) // 2, (196 - fitted.height) // 2))
        result = io.BytesIO()
        tile.save(result, format="PNG", optimize=True)
        return result.getvalue()


def _slot_index(key: str) -> tuple[int, str]:
    try:
        row, col = (int(value) for value in key.split("_", 1))
    except (ValueError, AttributeError):
        raise ValueError("Posição de tecla inválida.") from None
    if row not in range(3) or col not in range(5) or (row == 2 and col > 2):
        raise ValueError("A posição selecionada não é uma tecla configurável.")
    return row * 5 + col, f"{col}_{row}"


def _build_archive(entries: dict[str, tuple[dict[str, Any], bytes]]) -> bytes:
    manifest: dict[str, Any] = {}
    for wire_key, (config, _) in entries.items():
        manifest[wire_key] = {"State": 0, "ViewParam": [{"Icon": f"icons/{wire_key}.png"}]}

    def assemble(padding: int) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True, separators=(",", ":")))
            dummy = zipfile.ZipInfo("dummy.txt")
            dummy.compress_type = zipfile.ZIP_STORED
            archive.writestr(dummy, b"A" * padding)
            for wire_key, (_, png) in entries.items():
                archive.writestr(f"icons/{wire_key}.png", png)
            sentinel = zipfile.ZipInfo("sentinel.txt")
            sentinel.compress_type = zipfile.ZIP_STORED
            archive.writestr(sentinel, b"")
        return buffer.getvalue()

    for padding in range(1025):
        blob = assemble(padding)
        if all(blob[offset:offset + 1] not in (b"\x00", b"\x7c") for offset in range(1016, len(blob), FRAME_SIZE)):
            if len(blob) > MAX_ARCHIVE_SIZE:
                raise ValueError(f"O pacote de ícones excede o limite seguro do firmware ({len(blob)} bytes; máximo {MAX_ARCHIVE_SIZE}).")
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                bad_entry = archive.testzip()
                if bad_entry is not None:
                    raise ValueError(f"O pacote ZIP ficou corrompido na entrada {bad_entry}.")
            return blob
    raise RuntimeError("Não foi possível alinhar o arquivo aos limites de quadro aceitos pelo firmware D200.")


def _frame(command: int, payload_length: int, payload: bytes) -> bytes:
    return b"\x7c\x7c" + command.to_bytes(2, "big") + payload_length.to_bytes(4, "little") + payload.ljust(MAX_FIRST_PAYLOAD, b"\0")


def _write_all(fd: int, report: bytes) -> None:
    written = os.write(fd, b"\x00" + report)
    if written != FRAME_SIZE + 1:
        raise OSError(f"Escrita HID incompleta: {written}/{FRAME_SIZE + 1} bytes.")


def _send(path: str, blob: bytes) -> int:
    fd = os.open(path, os.O_WRONLY | os.O_CLOEXEC)
    try:
        # Every sync is a complete snapshot. PARTIALLY_UPDATE_BUTTONS (0x000D)
        # is additive and leaves icons from a previous profile on the device.
        _write_all(fd, _frame(SET_BUTTONS, len(blob), blob[:MAX_FIRST_PAYLOAD]))
        for offset in range(MAX_FIRST_PAYLOAD, len(blob), FRAME_SIZE):
            _write_all(fd, blob[offset:offset + FRAME_SIZE].ljust(FRAME_SIZE, b"\0"))
    finally:
        os.close(fd)
    return 1 + max(0, (len(blob) - MAX_FIRST_PAYLOAD + FRAME_SIZE - 1) // FRAME_SIZE)


def sync_icons(store: ProfileStore, key: str | None = None) -> dict[str, Any]:
    path, reason = d200_write_target()
    if not path:
        raise RuntimeError(reason or "Interface de escrita D200 indisponível.")

    buttons = store.buttons()
    # The editor may request a sync for one changed key, but the device must
    # receive the whole active profile. Partial updates are additive: they do
    # not reliably clear stale images after profile switches or USB reconnects.
    if key is not None:
        _slot_index(key)
    entries: dict[str, tuple[dict[str, Any], bytes]] = {}
    for button_key, config in buttons.items():
        if not config.get("icon"):
            continue
        _, wire_key = _slot_index(button_key)
        entries[wire_key] = (config, _render_icon(config))
    blob = _build_archive(entries)
    with _WRITE_LOCK:
        frames = _send(path, blob)
    return {"ok": True, "keys": list(entries), "bytes": len(blob), "frames": frames,
            "confirmation": "hid-write", "message": "Perfil completo gravado na interface HID. O dispositivo não confirma a renderização; confira a tela física."}
