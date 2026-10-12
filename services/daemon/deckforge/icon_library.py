"""Icon sources for keys: Streamline packs, local logos, installed apps and user images.

An icon reference stored in a key is one of:
  pack:<set>:<name>          bundled Streamline pack (apps/desktop/public/icons/streamline)
  streamline-logos-solid/... local SVG file of the original logo library
  app:<desktop-file-id>      icon of an installed application
  user:<sha256>.png          image uploaded by the user or imported from a URL

Every source is rasterized here, so the editor preview and the LCD use the same pixels.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import threading
import unicodedata
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from .store import DATA_HOME

USER_ICON_DIR = DATA_HOME / "icons"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 40_000_000
USER_ICON_SIZE = 256
# Backgrounds keep their aspect ratio; 800 px covers the 392x196 wide display at 2x.
USER_BACKGROUND_MAX = 800
_PACK_REF = re.compile(r"^pack:([a-z0-9-]{1,40}):([a-z0-9][a-z0-9-]{0,159})$")
_USER_REF = re.compile(r"^user:([0-9a-f]{32})\.png$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_render_lock = threading.Lock()

# Streamline names are English. Common Portuguese words map to their terms so
# "casa", "música" or "configurações" find icons too.
_PT_TERMS = {
    "casa": "home house", "inicio": "home", "pasta": "folder", "arquivo": "file document", "documento": "document file",
    "musica": "music song", "som": "sound audio volume speaker", "volume": "volume speaker", "tocar": "play",
    "pausar": "pause", "parar": "stop", "proximo": "next forward", "anterior": "previous back rewind",
    "configuracoes": "setting cog gear", "configuracao": "setting cog gear", "ajustes": "setting adjust",
    "engrenagem": "cog gear setting", "camera": "camera", "microfone": "microphone", "mudo": "mute",
    "nuvem": "cloud", "email": "mail email envelope", "correio": "mail envelope", "calendario": "calendar date",
    "relogio": "clock time watch", "hora": "clock time", "usuario": "user person", "pessoa": "person user",
    "buscar": "search magnifier", "pesquisar": "search magnifier", "lixeira": "trash bin delete",
    "apagar": "delete erase trash", "editar": "edit pencil pen", "copiar": "copy", "colar": "paste clipboard",
    "salvar": "save floppy disk", "codigo": "code programming", "jogo": "game controller gaming",
    "navegador": "browser web globe", "internet": "globe web internet", "telefone": "phone call",
    "celular": "phone mobile", "mensagem": "message chat bubble", "conversa": "chat conversation",
    "coracao": "heart love", "estrela": "star", "cadeado": "lock padlock", "bloquear": "lock",
    "desbloquear": "unlock", "chave": "key", "luz": "light bulb lamp", "lampada": "bulb lamp light",
    "energia": "power energy", "desligar": "power shutdown", "bateria": "battery", "rede": "network wifi",
    "imagem": "image picture photo", "foto": "photo picture camera", "video": "video film movie",
    "filme": "film movie", "gravar": "record", "tela": "screen monitor display", "monitor": "monitor screen",
    "computador": "computer laptop desktop", "notebook": "laptop", "teclado": "keyboard", "impressora": "printer",
    "grafico": "chart graph", "dinheiro": "money cash dollar coin", "carrinho": "cart shopping",
    "compras": "shopping cart bag", "mapa": "map", "localizacao": "location pin map", "clima": "weather",
    "tempo": "weather time clock", "sol": "sun", "lua": "moon", "chuva": "rain", "seta": "arrow",
    "cima": "up", "baixo": "down", "esquerda": "left", "direita": "right", "adicionar": "add plus",
    "mais": "add plus", "remover": "remove minus", "menos": "minus subtract", "fechar": "close cross",
    "alerta": "alert warning", "aviso": "warning alert", "ajuda": "help question", "informacao": "information info",
    "livro": "book", "escola": "school education", "trabalho": "work briefcase office", "carro": "car vehicle",
    "aviao": "airplane plane flight", "comida": "food meal", "cafe": "coffee cup", "presente": "gift present",
    "sino": "bell notification", "notificacao": "notification bell", "download": "download", "upload": "upload",
    "compartilhar": "share", "link": "link chain", "fone": "headphones headset", "fones": "headphones",
    "controle": "controller remote control", "seguranca": "security shield lock", "escudo": "shield",
    "terminal": "terminal console command", "servidor": "server", "banco": "database bank", "dados": "data database",
    "robo": "robot", "inteligencia": "artificial intelligence ai", "transmissao": "stream broadcast live",
    "ao vivo": "live", "jogar": "play game", "pincel": "brush paint", "cor": "color palette", "texto": "text type",
}


class IconError(ValueError):
    pass


def icon_root() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    for candidate in (repo_root / "apps/desktop/public/icons", DATA_HOME / "ui/icons"):
        if candidate.is_dir():
            return candidate.resolve()
    raise RuntimeError("Biblioteca local de ícones não encontrada; instale os arquivos da interface DeckForge.")


@lru_cache(maxsize=1)
def _catalog() -> dict[str, Any]:
    path = icon_root() / "streamline/catalog.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"sets": [], "total": 0}


@lru_cache(maxsize=1)
def _local_logos() -> list[dict[str, str]]:
    try:
        return json.loads((icon_root() / "streamline-logos-solid/index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, RuntimeError):
        return []


@lru_cache(maxsize=8)
def _pack(prefix: str) -> dict[str, Any]:
    if not any(item["prefix"] == prefix for item in _catalog()["sets"]):
        raise IconError(f"Pacote de ícones desconhecido: {prefix}")
    return json.loads((icon_root() / f"streamline/{prefix}.json").read_text(encoding="utf-8"))


def catalog() -> dict[str, Any]:
    """Sets with counts and category names (no icon lists)."""
    sets = [{"id": item["prefix"], "name": item["name"], "total": item["total"], "palette": item["palette"],
             "license": item["license"], "author": item["author"],
             "categories": [{"name": name, "total": len(names)} for name, names in item["categories"].items()]}
            for item in _catalog()["sets"]]
    logos = _local_logos()
    local = {"id": "logos-solid", "name": "Logos Solid (arquivos locais)", "total": len(logos), "palette": False,
             "license": "CC BY 4.0", "author": "Streamline",
             "categories": [{"name": c, "total": sum(1 for i in logos if i["category"] == c)}
                            for c in sorted({i["category"] for i in logos})]}
    return {"sets": [local, *sets], "total": _catalog()["total"] + len(logos)}


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def _query_terms(query: str) -> list[set[str]]:
    words = [w for w in re.split(r"[^a-z0-9]+", _fold(query)) if w]
    return [{word, *_PT_TERMS.get(word, "").split()} for word in words]


def _matches(name: str, terms: list[set[str]]) -> bool:
    return all(any(option in name for option in options) for options in terms)


def _score(name: str, terms: list[set[str]]) -> tuple[int, int]:
    """Whole words and leading words rank first; shorter names break ties."""
    parts = name.split("-")
    score = 0
    for options in terms:
        best = 0
        for option in options:
            if parts and parts[0] == option:
                best = max(best, 3)
            elif option in parts:
                best = max(best, 2)
            elif option in name:
                best = max(best, 1)
        score += best
    return (-score, len(name))


def search(query: str = "", set_id: str | None = None, category: str | None = None,
           offset: int = 0, limit: int = 120) -> dict[str, Any]:
    """Icons whose name matches every word (Portuguese words also match English terms).

    set_id: one pack, "logos-solid" (local logos), "packs" (all Streamline packs) or None (everything).
    """
    terms = _query_terms(query)
    results: list[dict[str, Any]] = []
    if set_id in (None, "", "logos-solid"):
        for item in _local_logos():
            if category and set_id == "logos-solid" and item["category"] != category:
                continue
            if _matches(_fold(f"{item['name']} {item['category']}").replace(" ", "-"), terms):
                results.append({"ref": item["path"], "name": item["name"], "set": "logos-solid", "palette": False})
    for meta in _catalog()["sets"]:
        if set_id not in (None, "", "packs") and meta["prefix"] != set_id:
            continue
        names = meta["categories"].get(category, []) if category and set_id else meta["names"]
        for name in names:
            if _matches(name, terms):
                results.append({"ref": f"pack:{meta['prefix']}:{name}", "name": name.replace("-", " "),
                                "set": meta["prefix"], "palette": meta["palette"]})
    if terms:
        results.sort(key=lambda item: _score(item["ref"].rsplit(":", 1)[-1].rsplit("/", 1)[-1].removesuffix(".svg"), terms))
    return {"total": len(results), "items": results[offset:offset + max(1, min(limit, 300))]}


def is_monochrome(ref: str) -> bool:
    """Monochrome icons are drawn as a mask in the key's icon color (white by default)."""
    match = _PACK_REF.match(ref)
    if match:
        meta = next((m for m in _catalog()["sets"] if m["prefix"] == match.group(1)), None)
        return bool(meta) and not meta["palette"]
    return not ref.startswith(("app:", "user:"))


def pack_svg(ref: str) -> bytes:
    match = _PACK_REF.match(ref)
    if not match:
        raise IconError("Referência de ícone inválida.")
    pack = _pack(match.group(1))
    icon = pack["icons"].get(match.group(2))
    if icon is None:
        raise IconError(f"Ícone não encontrado: {match.group(2)}")
    width, height = icon.get("width", pack["width"]), icon.get("height", pack["height"])
    left, top = icon.get("left", 0), icon.get("top", 0)
    body = icon["body"].replace("currentColor", "#000000")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{left} {top} {width} {height}" '
            f'width="{width}" height="{height}">{body}</svg>').encode("utf-8")


def _rasterize_svg(svg: bytes, size: int) -> Image.Image:
    try:
        import cairosvg
    except ImportError as exc:
        raise RuntimeError("Renderizador SVG ausente. Atualize o ambiente com `python -m pip install -e .`.") from exc
    with _render_lock:  # cairo is not guaranteed thread-safe across renders
        png = cairosvg.svg2png(bytestring=svg, output_width=size, output_height=size, unsafe=False)
    with Image.open(io.BytesIO(png)) as image:
        return image.convert("RGBA")


def user_icon_path(ref: str) -> Path:
    match = _USER_REF.match(ref)
    if not match:
        raise IconError("Referência de ícone enviado inválida.")
    return USER_ICON_DIR / f"{match.group(1)}.png"


def rasterize(ref: str, size: int) -> Image.Image:
    """Icon as RGBA, fitted into size x size with transparent padding."""
    if not isinstance(ref, str) or not ref or len(ref) > 200:
        raise IconError("O perfil não contém um caminho de ícone válido.")
    if ref.startswith("pack:"):
        image = _rasterize_svg(pack_svg(ref), size)
    elif ref.startswith("app:"):
        from .app_catalog import application_icon_png
        try:
            png = application_icon_png(ref[4:], size)
        except FileNotFoundError as exc:
            raise IconError(str(exc)) from exc
        with Image.open(io.BytesIO(png)) as source:
            image = source.convert("RGBA")
    elif ref.startswith("user:"):
        path = user_icon_path(ref)
        if not path.is_file():
            raise IconError("O ícone enviado não existe mais; escolha outro.")
        with Image.open(path) as source:
            image = source.convert("RGBA")
    else:
        root = icon_root()
        path = (root / ref).resolve()
        if not path.is_relative_to(root) or path.suffix.lower() != ".svg" or not path.is_file():
            raise IconError("O ícone precisa ser um SVG local da biblioteca DeckForge.")
        if path.stat().st_size > 512_000:
            raise IconError("O arquivo SVG ultrapassa o limite de 512 KB.")
        image = _rasterize_svg(path.read_bytes(), size)
    fitted = ImageOps.contain(image, (size, size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(fitted, ((size - fitted.width) // 2, (size - fitted.height) // 2))
    return canvas


def render_background(ref: str, size: tuple[int, int], fit: str = "cover") -> Image.Image:
    """User image scaled to fill (cover, cropping) or fit (contain) a tile."""
    path = user_icon_path(ref)
    if not path.is_file():
        raise IconError("A imagem de fundo não existe mais; envie outra.")
    with Image.open(path) as source:
        image = source.convert("RGBA")
    if fit == "contain":
        fitted = ImageOps.contain(image, size, Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        canvas.alpha_composite(fitted, ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2))
        return canvas
    return ImageOps.fit(image, size, Image.Resampling.LANCZOS)


def resolve_icon_color(ref: str, setting: str | None) -> str | None:
    """Tint for an icon: explicit color, 'original', or automatic (white for monochrome)."""
    if setting == "original":
        return None
    if isinstance(setting, str) and _COLOR.match(setting):
        return setting
    return "#ffffff" if is_monochrome(ref) else None


def render_icon(ref: str, size: int, color: str | None) -> Image.Image:
    image = rasterize(ref, size)
    if color:
        tinted = Image.new("RGBA", image.size, tuple(bytes.fromhex(color[1:])) + (255,))
        tinted.putalpha(image.getchannel("A"))
        image = tinted
    return image


@lru_cache(maxsize=512)
def preview_png(ref: str, size: int, color: str | None) -> bytes:
    buffer = io.BytesIO()
    render_icon(ref, size, color).save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _decode_image(data: bytes) -> Image.Image:
    if not data:
        raise IconError("Arquivo vazio.")
    if len(data) > MAX_IMAGE_BYTES:
        raise IconError("A imagem passa de 5 MB.")
    head = data[:512].lstrip().lower()
    if head.startswith(b"<?xml") or head.startswith(b"<svg") or b"<svg" in head:
        if re.search(rb"<script|<foreignObject|\bon[a-z]+\s*=|javascript:", data, re.I):
            raise IconError("O SVG contém scripts e foi recusado.")
        try:
            return _rasterize_svg(data, USER_ICON_SIZE)
        except Exception as exc:
            raise IconError(f"SVG inválido: {exc}") from exc
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP", "GIF", "BMP", "ICO"}:
                raise IconError(f"Formato não suportado: {source.format}. Use PNG, JPG, WEBP, GIF, BMP, ICO ou SVG.")
            source.seek(0)
            return source.convert("RGBA")
    except Image.DecompressionBombError as exc:
        raise IconError("A imagem é grande demais.") from exc
    except (OSError, SyntaxError) as exc:
        raise IconError("O arquivo não é uma imagem válida.") from exc


def save_user_icon(data: bytes, name: str = "", kind: str = "icon") -> dict[str, Any]:
    """Store an uploaded image as PNG by content hash.

    kind="icon": centered in a 256 px square. kind="background": aspect ratio
    kept, at most 800 px, to fill a key or the wide display.
    """
    if kind not in {"icon", "background"}:
        raise IconError("Tipo de imagem inválido.")
    decoded = _decode_image(data)
    if kind == "background":
        canvas = ImageOps.contain(decoded, (USER_BACKGROUND_MAX, USER_BACKGROUND_MAX), Image.Resampling.LANCZOS) \
            if max(decoded.size) > USER_BACKGROUND_MAX else decoded
    else:
        image = ImageOps.contain(decoded, (USER_ICON_SIZE, USER_ICON_SIZE), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (USER_ICON_SIZE, USER_ICON_SIZE), (0, 0, 0, 0))
        canvas.alpha_composite(image, ((USER_ICON_SIZE - image.width) // 2, (USER_ICON_SIZE - image.height) // 2))
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=True)
    png = buffer.getvalue()
    digest = hashlib.sha256(png).hexdigest()[:32]
    USER_ICON_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = USER_ICON_DIR / f"{digest}.png"
    path.write_bytes(png)
    meta = USER_ICON_DIR / "index.json"
    try:
        index = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        index = {}
    index[f"{digest}.png"] = (name or index.get(f"{digest}.png") or "Ícone enviado")[:80]
    meta.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    preview_png.cache_clear()
    return {"ref": f"user:{digest}.png", "name": index[f"{digest}.png"]}


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme not in {"http", "https"}:
            raise IconError("Redirecionamento para um endereço que não é http/https.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def import_icon_url(url: str, kind: str = "icon") -> dict[str, Any]:
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise IconError("Informe um endereço http:// ou https:// de uma imagem.")
    opener = urllib.request.build_opener(_SafeRedirect)
    request = urllib.request.Request(url.strip(), headers={"User-Agent": "DeckForge/0.1 (+icon import)"})
    try:
        with opener.open(request, timeout=10) as response:
            data = response.read(MAX_IMAGE_BYTES + 1)
    except IconError:
        raise
    except Exception as exc:
        raise IconError(f"Não foi possível baixar a imagem: {exc}") from exc
    name = Path(parts.path).stem.replace("-", " ").replace("_", " ") or parts.netloc
    return save_user_icon(data, name, kind)


def list_user_icons() -> list[dict[str, str]]:
    try:
        index = json.loads((USER_ICON_DIR / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        index = {}
    files = sorted(USER_ICON_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True) if USER_ICON_DIR.is_dir() else []
    return [{"ref": f"user:{p.name}", "name": index.get(p.name, "Ícone enviado")} for p in files if _USER_REF.match(f"user:{p.name}")]


def delete_user_icon(ref: str) -> None:
    path = user_icon_path(ref)
    path.unlink(missing_ok=True)
    preview_png.cache_clear()
