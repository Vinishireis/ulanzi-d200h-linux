"""Discover installed Linux applications and render their desktop icons."""
from __future__ import annotations

import configparser
import io
import os
import re
import shlex
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

_SIZES = ("scalable", "256x256", "192x192", "128x128", "96x96", "64x64", "48x48", "32x32", "24x24", "16x16")
_FORMATS = ("png", "svg", "xpm")
_FIELDS = re.compile(r"%[fFuUdDnNickvm]")


def _data_dirs() -> list[Path]:
    home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")).expanduser()
    configured = os.environ.get("XDG_DATA_DIRS", "").strip() or "/usr/local/share:/usr/share"
    result = [home, *(Path(item).expanduser() for item in configured.split(":") if item)]
    result.extend((home / "flatpak/exports/share", Path("/var/lib/flatpak/exports/share")))
    return list(dict.fromkeys(result))


def _desktop_files():
    seen: set[str] = set()
    for root in _data_dirs():
        directory = root / "applications"
        if not directory.is_dir():
            continue
        try:
            files = sorted(directory.rglob("*.desktop"))
        except OSError:
            continue
        for path in files:
            if path.name in seen:
                continue
            seen.add(path.name)
            yield path


def _localized(entry: configparser.SectionProxy, key: str) -> str:
    lang = os.environ.get("LANG", "").split(".", 1)[0]
    candidates = [lang, lang.replace("-", "_"), lang.split("_", 1)[0], ""]
    for locale in dict.fromkeys(candidates):
        localized_key = f"{key}[{locale}]" if locale else key
        value = entry.get(localized_key, "").strip()
        if value:
            return value
    return ""


def resolve_icon(icon: str | None) -> Path | None:
    if not icon:
        return None
    direct = Path(icon).expanduser()
    if direct.is_absolute() and direct.is_file():
        return direct.resolve()
    name = Path(icon).name
    if Path(name).suffix.lower() in {".png", ".svg", ".xpm"}:
        stem, extension = Path(name).stem, Path(name).suffix.lstrip(".").lower()
        suffixes = (extension,)
    else:
        stem, suffixes = name, _FORMATS
    dirs = _data_dirs()
    for root in dirs:
        for theme in ("hicolor", "Adwaita", "gnome"):
            for size in _SIZES:
                for extension in suffixes:
                    candidate = root / "icons" / theme / size / "apps" / f"{stem}.{extension}"
                    if candidate.is_file():
                        return candidate.resolve()
        for pixmaps in (root / "pixmaps", root / "icons"):
            for extension in suffixes:
                candidate = pixmaps / f"{stem}.{extension}"
                if candidate.is_file():
                    return candidate.resolve()
    return None


def _entry(path: Path) -> dict[str, Any] | None:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        with path.open(encoding="utf-8", errors="replace") as source:
            parser.read_file(source)
        entry = parser["Desktop Entry"]
    except (OSError, KeyError, configparser.Error):
        return None
    try:
        unavailable = (entry.get("Type", "Application") != "Application"
                       or entry.getboolean("Hidden", fallback=False)
                       or entry.getboolean("NoDisplay", fallback=False)
                       or entry.getboolean("Terminal", fallback=False))
    except ValueError:
        return None
    if unavailable:
        return None
    name = _localized(entry, "Name")
    raw_exec = entry.get("Exec", "").strip()
    if not name or not raw_exec:
        return None
    try:
        raw_argv = shlex.split(raw_exec, posix=True)
    except ValueError:
        return None
    icon_name = entry.get("Icon", "").strip()
    argv: list[str] = []
    for value in raw_argv:
        if value == "%i":
            if icon_name:
                argv.extend(("--icon", icon_name))
            continue
        escaped_percent = value.replace("%%", "\uffff")
        expanded = escaped_percent.replace("%c", name).replace("%k", str(path))
        cleaned = _FIELDS.sub("", expanded).replace("\uffff", "%")
        if cleaned:
            argv.append(cleaned)
    if not argv or not argv[0] or not shutil.which(argv[0]):
        return None
    try_exec = entry.get("TryExec", "").strip()
    if try_exec and not shutil.which(try_exec):
        return None
    icon_path = resolve_icon(icon_name)
    return {"id": path.name, "name": name, "argv": argv,
            "icon": f"app:{path.name}" if icon_path else None,
            "icon_path": str(icon_path) if icon_path else None}


_CACHE_SECONDS = 60.0
_cache: tuple[float, list[dict[str, Any]]] | None = None
_cache_lock = threading.Lock()


def _all_applications() -> list[dict[str, Any]]:
    # Parsing every .desktop file takes ~0.7 s; each icon request needs the list.
    global _cache
    with _cache_lock:
        if _cache is not None and time.monotonic() - _cache[0] < _CACHE_SECONDS:
            return _cache[1]
        apps = sorted((app for path in _desktop_files() if (app := _entry(path)) is not None),
                      key=lambda app: app["name"].casefold())
        _cache = (time.monotonic(), apps)
        return apps


def installed_applications() -> list[dict[str, Any]]:
    return [{key: app[key] for key in ("id", "name", "argv", "icon")} for app in _all_applications()]


def application_by_id(app_id: str) -> dict[str, Any] | None:
    if not app_id.endswith(".desktop") or "/" in app_id or "\\" in app_id or "\0" in app_id:
        return None
    return next((app for app in _all_applications() if app["id"] == app_id), None)


def render_icon_file(path: Path, size: int = 196) -> bytes:
    if path.suffix.lower() == ".svg":
        try:
            import cairosvg
        except ImportError as exc:
            raise RuntimeError("Renderizador SVG ausente; instale as dependências do DeckForge.") from exc
        image_bytes = cairosvg.svg2png(url=str(path), output_width=size, output_height=size)
    else:
        with Image.open(path) as source:
            source = source.convert("RGBA")
            fitted = ImageOps.contain(source, (size, size), Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            canvas.alpha_composite(fitted, ((size - fitted.width) // 2, (size - fitted.height) // 2))
            buffer = io.BytesIO()
            canvas.save(buffer, format="PNG", optimize=True)
            return buffer.getvalue()
    with Image.open(io.BytesIO(image_bytes)) as source:
        source = source.convert("RGBA")
        fitted = ImageOps.contain(source, (size, size), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.alpha_composite(fitted, ((size - fitted.width) // 2, (size - fitted.height) // 2))
        buffer = io.BytesIO()
        canvas.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()


def application_icon_png(app_id: str, size: int = 196) -> bytes:
    app = application_by_id(app_id)
    if not app or not app.get("icon_path"):
        raise FileNotFoundError("O aplicativo não possui um ícone local disponível.")
    return render_icon_file(Path(app["icon_path"]), size)
