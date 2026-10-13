"""Loopback-only editor API and live status stream."""
from __future__ import annotations
import asyncio
import os
import time
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from . import __version__
from pydantic import BaseModel, Field, model_validator
from .actions import ACTION_TYPES, ActionError, execute_action, normalize_action
from . import icon_library
from .metrics import SystemMetrics
from .render import KEY_SIZE, display_settings, render_tile, render_wide
from .app_catalog import application_icon_png, installed_applications
from .daemon import run_action
from .keyboard import MAC_RUN_SLOTS, WINDOWS_RUN_SLOTS
from .lcd_sync import sync_icons
from .status import collect_status, integrations
from .store import DATA_HOME, ProfileStore

app = FastAPI(title="DeckForge Linux", version="0.1.0", docs_url=None, redoc_url=None)
# The hosted editor (Vercel) runs in the user's browser and calls this API on
# 127.0.0.1. Only these exact origins are accepted, and only after the user
# allows it locally (setting online_editor). DECKFORGE_HOSTED_ORIGINS
# (comma-separated) replaces the list, e.g. for a custom domain.
HOSTED_ORIGINS = frozenset(o.strip().rstrip("/") for o in os.environ.get(
    "DECKFORGE_HOSTED_ORIGINS", "https://ulanzi-d200h-linux.vercel.app").split(",") if o.strip())
# The Vite editor runs on port 1420 while the local API uses 8765. Its JSON
# requests trigger browser preflights, which must be answered before routing.
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(HOSTED_ORIGINS),
    allow_origin_regex=r"^http://(127\.0\.0\.1|localhost)(:(1420|8765))?$",
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)
store = ProfileStore()
# CPU usage is a delta between samples; one sampler keeps it across preview requests.
_metrics = SystemMetrics()
_requests: dict[str, tuple[float, int]] = {}

def _origin_allowed(origin: str) -> bool:
    if origin == "tauri://localhost" or origin in HOSTED_ORIGINS: return True
    try:
        parts = urlsplit(origin)
        return parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost"} and parts.port in {None, 1420, 8765}
    except ValueError:
        return False

@app.middleware("http")
async def local_only(request: Request, call_next):
    host = (request.url.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return JSONResponse({"detail":"DeckForge API aceita apenas conexões locais"}, status_code=403)
    origin = request.headers.get("origin")
    if origin and not _origin_allowed(origin):
        return JSONResponse({"detail":"Origem não permitida"}, status_code=403)
    if origin in HOSTED_ORIGINS and request.method != "OPTIONS" and request.url.path != "/api/hello" \
            and not store.settings()["online_editor"]:
        return JSONResponse({"detail": "O editor online ainda não foi permitido neste computador. Rode `deckforge online on` "
                                       "ou ative em Configurações no editor local (http://127.0.0.1:8765).",
                             "code": "online_editor_disabled"}, status_code=403)
    # Browser preflights, UI assets and icon thumbnails (a library page shows
    # hundreds) are not API traffic and must not consume the safety budget.
    thumbnail = request.method == "GET" and (request.url.path.startswith("/api/icons/image")
                                            or request.url.path.startswith("/api/apps/"))
    if request.url.path.startswith("/api/") and request.method != "OPTIONS" and not thumbnail:
        client = request.client.host if request.client else "unknown"
        now = time.monotonic(); window, count = _requests.get(client, (now, 0))
        if now - window >= 60: window, count = now, 0
        if count >= 600:
            retry_after = max(1, int(60 - (now - window)))
            return JSONResponse({"detail":"Limite local da API excedido; tente novamente em alguns segundos."}, status_code=429, headers={"Retry-After": str(retry_after)})
        _requests[client] = (window, count + 1)
    response = await call_next(request)
    if request.method == "OPTIONS" and request.headers.get("access-control-request-private-network") == "true" and origin:
        # Chrome's Private Network Access preflight for a public site calling 127.0.0.1.
        response.headers["Access-Control-Allow-Private-Network"] = "true"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers.setdefault("Cache-Control", "no-store")
    return response

class DisplayConfig(BaseModel):
    """Wide display (2_3 + 2_4): firmware clock, system statistics or a custom image."""
    mode: str = Field(default="clock", pattern=r"^(clock|stats|image)$")
    clock_format: str = Field(default="24h", pattern=r"^(24h|12h)$")
    # deckforge: drawn by DeckForge (no Ulanzi branding); firmware: the device's own screen.
    renderer: str = Field(default="deckforge", pattern=r"^(deckforge|firmware)$")
    show_date: bool = True
    style: str = Field(default="classic", pattern=r"^(classic|dev)$")  # dev: animated code-editor clock

class ButtonConfig(BaseModel):
    type: str
    title: str = Field(default="", max_length=48)
    argv: list[str] | None = None
    keys: list[str] | None = None
    text: str | None = Field(default=None, max_length=1000)
    profile: str | None = Field(default=None, max_length=64)
    page: str | None = Field(default=None, max_length=64)
    steps: list[dict] | None = None
    color: str = Field(default="#252936", pattern=r"^#[0-9a-fA-F]{6}$")
    icon: str | None = Field(default=None, max_length=200)
    icon_color: str | None = Field(default=None, pattern=r"^(#[0-9a-fA-F]{6}|original)$")
    background: str | None = Field(default=None, pattern=r"^user:[0-9a-f]{32}\.png$")
    background_fit: str | None = Field(default=None, pattern=r"^(cover|contain)$")
    title_position: str | None = Field(default=None, pattern=r"^(none|top|middle|bottom)$")
    title_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    display: DisplayConfig | None = None
    double_tap: dict | None = None  # wide display only: action for two quick touches

    @model_validator(mode="after")
    def validate_action(self):
        if self.type not in ACTION_TYPES: raise ValueError("Tipo de ação inválido")
        if self.type == "keys" and (not self.keys or len(self.keys) > 4): raise ValueError("Informe de uma a quatro teclas")
        if self.type in {"app", "command"} and (not self.argv or len(self.argv) > 32): raise ValueError("Informe executável e argumentos")
        if self.argv and any("\0" in item or len(item) > 2048 for item in self.argv): raise ValueError("Argumento inválido")
        return self

    def stored(self) -> dict:
        """Shape persisted in SQLite: only the fields this action type uses."""
        try:
            config = normalize_action(self.model_dump(exclude_none=True))
            if "double_tap" in config:
                config["double_tap"] = _double_tap(config["double_tap"])
            return config
        except ActionError as exc:
            raise HTTPException(422, f"{exc} Teclas aceitas: ctrl, alt, shift, super, tab, return, space, escape, F1–F12, setas, page up/down, mídia (playpause, next, previous, volumeup, volumedown, mute), letras e números.") from exc

_TAP_TYPES = {"keys", "app", "command", "text", "page", "profile", "none"}

def _double_tap(action) -> dict:
    if not isinstance(action, dict) or action.get("type") not in _TAP_TYPES:
        raise ActionError("Toque duplo: escolha uma página, um perfil, um atalho, um aplicativo, um comando, um texto ou nada.")
    return normalize_action({k: v for k, v in action.items() if k in {"type", "keys", "argv", "text", "page", "profile"}})

class ProfileCreate(BaseModel): name: str = Field(min_length=1, max_length=80)
class SyncRequest(BaseModel): key: str | None = Field(default=None, pattern=r"^[0-2]_[0-4]$")
class TestRequest(BaseModel): delay: float = Field(default=0, ge=0, le=10)
class SettingsUpdate(BaseModel):
    host_mode: bool | None = None
    log_keys: bool | None = None
    brightness: int | None = Field(default=None, ge=-1, le=100)
    online_editor: bool | None = None
class IconUrl(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    kind: str = Field(default="icon", pattern=r"^(icon|background)$")
class TilePreview(BaseModel):
    key: str = Field(default="0_0", pattern=r"^[0-2]_[0-4]$")
    config: ButtonConfig
class PageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    back: bool = True  # put a "Voltar" key on 0_0 so the page is never a dead end
class PageImport(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    name: str = Field(min_length=1, max_length=40)
    buttons: dict[str, ButtonConfig] = {}
class ProfileImport(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    buttons: dict[str, ButtonConfig]
    pages: list[PageImport] = Field(default=[], max_length=30)

@app.get("/api/hello")
def hello():
    """What the hosted editor asks first: is DeckForge running here, and may it be used?"""
    return {"app": "deckforge", "version": __version__, "api": 1, "online_editor": store.settings()["online_editor"]}

@app.get("/api/status")
def status():
    return collect_status(store)

@app.get("/api/profiles")
def profiles(): return store.profiles()

@app.get("/api/apps")
def apps(): return installed_applications()

@app.get("/api/apps/{app_id}/icon")
def app_icon(app_id: str):
    try:
        return Response(application_icon_png(app_id, 96), media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(422, f"Não foi possível carregar o ícone do aplicativo: {exc}") from exc

@app.post("/api/profiles", status_code=201)
def create_profile(data: ProfileCreate):
    return {"id": store.create_profile(data.name), "name": data.name}

@app.post("/api/profiles/{profile_id}/activate")
def activate_profile(profile_id: str):
    try: store.activate_profile(profile_id)
    except ValueError as exc: raise HTTPException(404, str(exc)) from exc
    return {"ok": True}

@app.patch("/api/profiles/{profile_id}")
def rename_profile(profile_id: str, data: ProfileCreate):
    try: store.rename_profile(profile_id, data.name)
    except ValueError as exc: raise HTTPException(404, str(exc)) from exc
    return {"ok": True}

@app.delete("/api/profiles/{profile_id}")
def delete_profile(profile_id: str):
    try: store.delete_profile(profile_id)
    except ValueError as exc: raise HTTPException(409, str(exc)) from exc
    return {"ok": True}

@app.get("/api/profiles/{profile_id}/export")
def export_profile(profile_id: str):
    try: return store.export_profile(profile_id)
    except ValueError as exc: raise HTTPException(404, str(exc)) from exc

@app.post("/api/profiles/import", status_code=201)
def import_profile(data: ProfileImport):
    buttons = {key: config.stored() for key, config in data.buttons.items()}
    pages = [{"id": page.id, "name": page.name, "buttons": {key: config.stored() for key, config in page.buttons.items()}}
             for page in data.pages]
    try: profile_id = store.import_profile(data.name, buttons, pages)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    return {"id": profile_id, "name": data.name}

@app.get("/api/pages")
def pages():
    """Pages of the active profile; "active" is the one shown on the device and edited in Meu Deck."""
    return {"active": store.active_page(), "pages": store.pages()}

@app.post("/api/pages", status_code=201)
def create_page(data: PageCreate):
    try: page_id = store.create_page(data.name, back_key=data.back)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    return {"id": page_id, "name": data.name.strip()}

@app.patch("/api/pages/{page_id}")
def rename_page(page_id: str, data: PageCreate):
    try: store.rename_page(page_id, data.name)
    except ValueError as exc: raise HTTPException(404, str(exc)) from exc
    return {"ok": True}

@app.delete("/api/pages/{page_id}")
def delete_page(page_id: str):
    try: store.delete_page(page_id)
    except ValueError as exc: raise HTTPException(409, str(exc)) from exc
    return {"active": store.active_page()}

@app.post("/api/pages/{page_id}/activate")
def activate_page(page_id: str):
    """Show this page on the device (the editor then edits it); the caller resends the icons."""
    try: page = store.navigate_page(page_id)
    except ValueError as exc: raise HTTPException(404, str(exc)) from exc
    store.log("action", f"Página: {page['name']} (aberta pelo editor)")
    return page

@app.get("/api/settings")
def get_settings(): return store.settings()

@app.put("/api/settings")
def update_settings(data: SettingsUpdate):
    try: return store.save_settings(data.model_dump(exclude_none=True))
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc

@app.get("/api/integrations")
def get_integrations(): return integrations(store)

@app.post("/api/integrations/input-test")
def input_test():
    """Press and release Shift through the configured backend: proves injection works without side effects."""
    try:
        message = execute_action({"type": "keys", "keys": ["shift"]})
    except ActionError as exc:
        store.log("error", f"Teste do backend de atalhos falhou: {exc}")
        return {"ok": False, "message": str(exc)}
    store.log("action", f"Teste do backend de atalhos: {message}")
    return {"ok": True, "message": message}

@app.get("/api/macros")
def macros():
    """Firmware macro and HID slot behind each editable key (single source: the decoder tables)."""
    windows = {slot: token for token, slot in WINDOWS_RUN_SLOTS.items()}
    mac = {slot: token for token, slot in MAC_RUN_SLOTS.items()}
    shortcuts = {"2_0": ("Ctrl+S", "Command+S"), "2_1": ("Ctrl+Z", "Command+Z"), "2_2": ("Ctrl+C", "Command+C")}
    result = {}
    for row in range(3):
        for col in range(5):
            slot = f"{row}_{col}"
            if row == 2 and col > 2: continue
            win, mac_token = (f"Super+R · {windows[slot]} · Enter", f"Command+Space · {mac[slot]} · Enter") if slot in windows else shortcuts[slot]
            result[slot] = {"hid_slot": row * 5 + col, "windows": win, "mac": mac_token}
    result["2_3"] = {"hid_slot": 13, "windows": "Toque na área larga (sem macro de teclado)", "mac": "Toque na área larga (sem macro de teclado)"}
    return result

# Icons: Streamline packs, local logos, installed apps and user images.
_IMMUTABLE = {"Cache-Control": "private, max-age=86400"}

@app.get("/api/icons/catalog")
def icon_catalog(): return icon_library.catalog()

@app.get("/api/icons/search")
def icon_search(q: str = "", set: str | None = None, category: str | None = None, offset: int = 0, limit: int = 120):
    return icon_library.search(q[:80], set, category, max(0, offset), limit)

@app.get("/api/icons/image")
def icon_image(ref: str, color: str | None = None, size: int = 96):
    """Icon rendered exactly as on the LCD (same rasterizer and tint)."""
    if size not in {32, 48, 64, 96, 128, 196}:
        raise HTTPException(422, "Tamanho de prévia inválido.")
    try:
        tint = icon_library.resolve_icon_color(ref, color)
        return Response(icon_library.preview_png(ref, size, tint), media_type="image/png", headers=_IMMUTABLE)
    except icon_library.IconError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(422, f"Não foi possível renderizar o ícone: {exc}") from exc

@app.get("/api/icons/user")
def user_icons(): return icon_library.list_user_icons()

async def _read_limited(request: Request) -> bytes:
    if int(request.headers.get("content-length") or 0) > icon_library.MAX_IMAGE_BYTES:
        raise HTTPException(413, "A imagem passa de 5 MB.")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > icon_library.MAX_IMAGE_BYTES:
            raise HTTPException(413, "A imagem passa de 5 MB.")
    return bytes(data)

@app.post("/api/icons/upload", status_code=201)
async def upload_icon(request: Request, name: str = "", kind: str = "icon"):
    if kind not in {"icon", "background"}:
        raise HTTPException(422, "Tipo de imagem inválido.")
    data = await _read_limited(request)
    try: result = await asyncio.to_thread(icon_library.save_user_icon, data, name[:80], kind)
    except icon_library.IconError as exc: raise HTTPException(422, str(exc)) from exc
    store.log("daemon", f"Ícone enviado: {result['name']}")
    return result

@app.post("/api/icons/url", status_code=201)
async def icon_from_url(data: IconUrl):
    try: result = await asyncio.to_thread(icon_library.import_icon_url, data.url, data.kind)
    except icon_library.IconError as exc: raise HTTPException(422, str(exc)) from exc
    store.log("daemon", f"Ícone importado de URL: {result['name']}")
    return result

@app.delete("/api/icons/user/{file}")
def delete_user_icon(file: str):
    ref = f"user:{file}"
    used = [key for key, config in store.buttons().items() if ref in (config.get("icon"), config.get("background"))]
    if used:
        raise HTTPException(409, f"O ícone está em uso nas teclas {', '.join(sorted(used))}; troque-o antes de excluir.")
    try: icon_library.delete_user_icon(ref)
    except icon_library.IconError as exc: raise HTTPException(404, str(exc)) from exc
    return {"ok": True}

@app.post("/api/render/tile")
def render_preview(data: TilePreview):
    """PNG of a key from an unsaved editor config: exactly what the LCD would receive."""
    config = data.config.model_dump(exclude_none=True)
    try:
        return _image(render_wide(config) if data.key == "2_3" else render_tile(config, KEY_SIZE))
    except icon_library.IconError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(422, f"Não foi possível renderizar a tecla: {exc}") from exc

def _image(data: bytes) -> Response:
    """PNG, or the animated GIF of the developer clock (the browser plays it like the LCD)."""
    return Response(data, media_type="image/gif" if data[:4] == b"GIF8" else "image/png")

@app.get("/api/render/key/{key}.png")
def render_saved_key(key: str):
    config = store.buttons().get(key)
    if config is None and key == "2_3":
        config = {}  # default wide display: DeckForge clock
    if config is None:
        raise HTTPException(404, "Tecla sem configuração.")
    try:
        if key != "2_3":
            return Response(render_tile(config, KEY_SIZE), media_type="image/png")
        # Same values the daemon draws on the LCD, not a 0% placeholder.
        metrics = _metrics.sample() if display_settings(config)["mode"] == "stats" else None
        return _image(render_wide(config, metrics))
    except (icon_library.IconError, OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc

@app.get("/api/buttons")
def buttons(): return store.buttons()

@app.put("/api/buttons/{key}")
def update_button(key: str, data: ButtonConfig):
    config = data.stored()
    if key != "2_3":
        config.pop("display", None)  # only the wide display has a display mode and a double tap
        config.pop("double_tap", None)
    try: store.save_button(key, config)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    return {"ok": True, "key": key}

@app.post("/api/buttons/{key}/test")
def test_button(key: str, data: TestRequest):
    """Run the saved action of a key the same way the daemon does after a press."""
    if key not in store.buttons():
        raise HTTPException(404, f"O botão {key} não tem ação configurada no perfil ativo")
    import threading
    resync = lambda reason: threading.Thread(target=_sync_quietly, args=(f"api:{reason}",), daemon=True).start()
    if data.delay:
        threading.Timer(data.delay, run_action, args=(store, key, "teste pelo editor"), kwargs={"on_layout_change": resync}).start()
        return {"ok": None, "scheduled": True, "message": f"Ação de {key} será executada em {data.delay:g} s; o resultado aparece em Logs."}
    ok, message = run_action(store, key, "teste pelo editor", on_layout_change=resync)
    return {"ok": ok, "scheduled": False, "message": message}

def _sync_quietly(source: str):
    try: sync_icons(store, source=source)
    except (OSError, RuntimeError, ValueError) as exc: store.log("error", f"LCD não atualizado após trocar de perfil ou página: {exc}")

@app.delete("/api/buttons/{key}")
def delete_button(key: str):
    try: store.delete_button(key)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    return {"ok": True}

@app.post("/api/sync/icons")
def sync_profile_icons(data: SyncRequest):
    try:
        return sync_icons(store, data.key, source="api")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, f"Falha ao enviar imagens pelo hidraw: {exc}") from exc

@app.get("/api/events")
def events(limit: int = 100): return store.recent_events(max(1, min(limit, 1000)))

@app.websocket("/api/live")
async def live(ws: WebSocket):
    host = (ws.url.hostname or "").lower()
    origin = ws.headers.get("origin", "")
    if host not in {"127.0.0.1", "localhost"} or (origin and not _origin_allowed(origin)) \
            or (origin in HOSTED_ORIGINS and not store.settings()["online_editor"]):
        await ws.close(code=1008); return
    await ws.accept()
    # Watch for the close frame concurrently. Polling alone only notices a
    # closed socket on the next send, so a quiet event log kept this handler
    # alive forever and blocked uvicorn's shutdown (`deckforge stop` hung).
    closed = asyncio.create_task(_wait_for_disconnect(ws))
    last_id = 0
    try:
        while not closed.done():
            for event in reversed(store.recent_events(20)):
                if event["id"] > last_id:
                    await ws.send_json(event); last_id = event["id"]
            await asyncio.wait({closed}, timeout=1)
    except (WebSocketDisconnect, RuntimeError, OSError): pass
    finally:
        closed.cancel()

async def _wait_for_disconnect(ws: WebSocket):
    try:
        while (await ws.receive())["type"] != "websocket.disconnect":
            pass
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass

if os.environ.get("DECKFORGE_UI_DIR"):
    ui_dir = Path(os.environ["DECKFORGE_UI_DIR"])
else:
    source_ui = Path(__file__).resolve().parents[3] / "apps/desktop/dist"
    ui_dir = source_ui if source_ui.exists() else DATA_HOME / "ui"
if ui_dir.exists():
    app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="desktop-ui")

