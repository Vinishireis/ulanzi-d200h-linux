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
from pydantic import BaseModel, Field, model_validator
from .device import capture_profile, discover, d200_write_target, environment
from .daemon import daemon_status
from .app_catalog import application_icon_png, installed_applications
from .lcd_sync import sync_icons
from .store import DATA_HOME, ProfileStore

app = FastAPI(title="DeckForge Linux", version="0.1.0", docs_url=None, redoc_url=None)
# The Vite editor runs on port 1420 while the local API uses 8765. Its JSON
# requests trigger browser preflights, which must be answered before routing.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(127\.0\.0\.1|localhost)(:(1420|8765))?$",
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)
store = ProfileStore()
_requests: dict[str, tuple[float, int]] = {}

def _origin_allowed(origin: str) -> bool:
    if origin == "tauri://localhost": return True
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
    # Browser preflights and UI assets (including hundreds of local icons) are
    # not API traffic and must not consume the local API's safety budget.
    if request.url.path.startswith("/api/") and request.method != "OPTIONS":
        client = request.client.host if request.client else "unknown"
        now = time.monotonic(); window, count = _requests.get(client, (now, 0))
        if now - window >= 60: window, count = now, 0
        if count >= 600:
            retry_after = max(1, int(60 - (now - window)))
            return JSONResponse({"detail":"Limite local da API excedido; tente novamente em alguns segundos."}, status_code=429, headers={"Retry-After": str(retry_after)})
        _requests[client] = (window, count + 1)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response

class ButtonConfig(BaseModel):
    type: str
    title: str = Field(default="", max_length=48)
    argv: list[str] | None = None
    keys: list[str] | None = None
    color: str = Field(default="#252936", pattern=r"^#[0-9a-fA-F]{6}$")
    icon: str | None = Field(default=None, max_length=180)

    @model_validator(mode="after")
    def validate_action(self):
        if self.type not in {"keys", "app", "command"}: raise ValueError("Tipo de ação inválido")
        if self.type == "keys" and (not self.keys or len(self.keys) > 4): raise ValueError("Informe de uma a quatro teclas")
        if self.type in {"app", "command"} and (not self.argv or len(self.argv) > 32): raise ValueError("Informe executável e argumentos")
        if self.argv and any("\0" in item or len(item) > 2048 for item in self.argv): raise ValueError("Argumento inválido")
        return self

class ProfileCreate(BaseModel): name: str = Field(min_length=1, max_length=80)
class SyncRequest(BaseModel): key: str | None = Field(default=None, pattern=r"^[0-2]_[0-4]$")

@app.get("/api/status")
def status():
    devices = discover()
    capture = capture_profile(devices)
    accessible = any(h["readable"] for d in devices for i in d["interfaces"] for h in i["hidraw"] if i["number"].lower() == "00")
    write_node, write_reason = d200_write_target()
    return {"devices": devices, "connected": bool(devices), "accessible": accessible,
            "lcd_sync": {"available": bool(write_node), "profile": "D200 2-interface" if write_node else None, "reason": write_reason},
            "profile": store.active_profile(), "session": environment(), "configured_buttons": len(store.buttons()),
            "input_capture": capture, "daemon": daemon_status(), "events": store.recent_events(8)}

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

@app.get("/api/buttons")
def buttons(): return store.buttons()

@app.put("/api/buttons/{key}")
def update_button(key: str, data: ButtonConfig):
    try: store.save_button(key, data.model_dump(exclude_none=True))
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    return {"ok": True, "key": key}

@app.delete("/api/buttons/{key}")
def delete_button(key: str):
    try: store.delete_button(key)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    return {"ok": True}

@app.post("/api/sync/icons")
def sync_profile_icons(data: SyncRequest):
    try:
        return sync_icons(store, data.key)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, f"Falha ao enviar imagens pelo hidraw: {exc}") from exc

@app.get("/api/events")
def events(): return store.recent_events(100)

@app.websocket("/api/live")
async def live(ws: WebSocket):
    host = (ws.url.hostname or "").lower()
    origin = ws.headers.get("origin", "")
    if host not in {"127.0.0.1", "localhost"} or (origin and not _origin_allowed(origin)):
        await ws.close(code=1008); return
    await ws.accept()
    last_id = 0
    try:
        while True:
            for event in reversed(store.recent_events(20)):
                if event["id"] > last_id:
                    await ws.send_json(event); last_id = event["id"]
            await asyncio.sleep(1)
    except WebSocketDisconnect: pass

if os.environ.get("DECKFORGE_UI_DIR"):
    ui_dir = Path(os.environ["DECKFORGE_UI_DIR"])
else:
    source_ui = Path(__file__).resolve().parents[3] / "apps/desktop/dist"
    ui_dir = source_ui if source_ui.exists() else DATA_HOME / "ui"
if ui_dir.exists():
    app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="desktop-ui")

