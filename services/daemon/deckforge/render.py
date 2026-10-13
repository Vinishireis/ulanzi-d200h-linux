"""Compose the image of a key (196x196) or of the wide display (392x196).

The editor preview and the LCD upload both call render_tile, so what the user
sees in the browser is exactly the PNG sent to the device.
"""
from __future__ import annotations

import io
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .icon_library import render_background, render_icon, resolve_icon_color

KEY_SIZE = (196, 196)
WIDE_SIZE = (392, 196)
DEFAULT_BACKGROUND = "#101116"
TITLE_POSITIONS = {"none", "top", "middle", "bottom"}
DISPLAY_MODES = {"clock", "stats", "image"}
_WEEKDAYS = ("seg.", "ter.", "qua.", "qui.", "sex.", "sáb.", "dom.")
_MONTHS = ("jan.", "fev.", "mar.", "abr.", "mai.", "jun.", "jul.", "ago.", "set.", "out.", "nov.", "dez.")
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)
_MONO_CANDIDATES = {
    False: ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
            "/usr/share/fonts/dejavu/DejaVuSansMono.ttf", "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"),
    True: ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSansMono-Bold.ttf",
           "/usr/share/fonts/dejavu/DejaVuSansMono-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf"),
}
# Commands typed by the developer clock; one per minute, in turn.
DEV_SNIPPETS = ('git commit -m "feat: deck"', "npm run dev", "const café = await brew();", "while (vivo) { code(); }",
                "deckforge sync --lcd", "git push origin main", "docker compose up -d", "python3 -m http.server")


def _hex(color: Any, fallback: str) -> tuple[int, int, int]:
    if isinstance(color, str) and len(color) == 7 and color.startswith("#"):
        try:
            return tuple(bytes.fromhex(color[1:]))  # type: ignore[return-value]
        except ValueError:
            pass
    return tuple(bytes.fromhex(fallback[1:]))  # type: ignore[return-value]


@lru_cache(maxsize=16)
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size=size)


@lru_cache(maxsize=8)
def _mono(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in _MONO_CANDIDATES[bold]:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return _font(size)


def title_position(config: dict[str, Any]) -> str:
    """Saved position, or the default: keys with an icon show only the icon."""
    title = (config.get("title") or "").strip()
    if not title:
        return "none"
    position = config.get("title_position")
    if position in TITLE_POSITIONS:
        return position
    return "none" if config.get("icon") else "middle"


def has_visual(config: dict[str, Any]) -> bool:
    return bool(config.get("icon")) or bool(config.get("background")) or title_position(config) != "none"


def display_settings(config: dict[str, Any] | None) -> dict[str, Any]:
    """Wide display (2_3) settings. By default DeckForge draws the clock itself:
    the firmware's own clock has a Ulanzi-branded background that cannot be removed."""
    display = (config or {}).get("display") or {}
    return {"mode": display.get("mode") if display.get("mode") in DISPLAY_MODES else "clock",
            "clock_format": "12h" if display.get("clock_format") == "12h" else "24h",
            "renderer": "firmware" if display.get("renderer") == "firmware" else "deckforge",
            "show_date": display.get("show_date", True) is not False,
            # dev: animated code-editor clock (GIF, animated by the firmware itself)
            "style": "dev" if display.get("style") == "dev" else "classic"}


def host_rendered(display: dict[str, Any]) -> bool:
    """True when the wide display shows an image drawn by DeckForge (no firmware branding)."""
    return display["mode"] == "image" or display["renderer"] == "deckforge"


def firmware_draws_values(display: dict[str, Any]) -> bool:
    """True when the firmware draws the live values from the 2 s keep-alive.

    Statistics change every few seconds. Uploading a new image for each change
    makes the D200 redraw the whole wide display, so the values disappeared and
    came back. The firmware draws CPU/RAM/GPU itself as a layer over the wide
    image, so DeckForge uploads only the background, once.
    """
    return display["mode"] == "stats" or (display["mode"] == "clock" and display["renderer"] == "firmware")


def _paint_background(tile: Image.Image, config: dict[str, Any]) -> bool:
    ref = config.get("background")
    if not ref:
        return False
    tile.alpha_composite(render_background(ref, tile.size, "contain" if config.get("background_fit") == "contain" else "cover"))
    return True


def _shade(tile: Image.Image, strength: int = 110) -> None:
    """Darken a photo background so text on top stays readable."""
    tile.alpha_composite(Image.new("RGBA", tile.size, (0, 0, 0, strength)))


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int, max_lines: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and draw.textlength(lines[-1] + "…", font=font) > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return lines


def _draw_title(tile: Image.Image, text: str, position: str, color: tuple[int, int, int], area: tuple[int, int, int, int]) -> None:
    draw = ImageDraw.Draw(tile)
    left, top, right, bottom = area
    width, max_lines = right - left, 2 if position == "middle" else 1
    for size in (34, 30, 26, 22, 20, 18):
        font = _font(size)
        lines = _wrap(draw, text, font, width, max_lines)
        if all(draw.textlength(line, font=font) <= width for line in lines):
            break
    line_height = int(size * 1.18)
    block = line_height * len(lines)
    y = top + (bottom - top - block) // 2
    shadow = (0, 0, 0, 150)
    for line in lines:
        x = left + (width - draw.textlength(line, font=font)) / 2
        draw.text((x + 1, y + 2), line, font=font, fill=shadow)
        draw.text((x, y), line, font=font, fill=color + (255,))
        y += line_height


def render_tile(config: dict[str, Any], size: tuple[int, int] = KEY_SIZE) -> bytes:
    width, height = size
    tile = Image.new("RGBA", size, _hex(config.get("color"), DEFAULT_BACKGROUND) + (255,))
    _paint_background(tile, config)
    position = title_position(config)
    title = (config.get("title") or "").strip()
    icon = config.get("icon")
    if width >= 2 * height:
        # Wide display: icon on the left, title beside it.
        if icon:
            box = height - 36 if position != "none" else height - 20
            image = render_icon(icon, box, resolve_icon_color(icon, config.get("icon_color")))
            x = (height - box) // 2 if position != "none" else (width - box) // 2
            tile.alpha_composite(image, (x, (height - box) // 2))
        if position != "none":
            area = (height - 6, 16, width - 14, height - 16) if icon else (16, 16, width - 16, height - 16)
            _draw_title(tile, title, "middle", _hex(config.get("title_color"), "#ffffff"), area)
        buffer = io.BytesIO()
        tile.convert("RGB").save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
    band = 52 if position in {"top", "bottom"} else 0
    if icon:
        icon_area = (0, band if position == "top" else 0, width, height - (band if position == "bottom" else 0))
        if position == "middle":
            icon_area = (0, 0, width, height - 70)
        box = min(icon_area[2] - icon_area[0], icon_area[3] - icon_area[1]) - (10 if not band and position != "middle" else 16)
        image = render_icon(icon, box, resolve_icon_color(icon, config.get("icon_color")))
        x = icon_area[0] + (icon_area[2] - icon_area[0] - box) // 2
        y = icon_area[1] + (icon_area[3] - icon_area[1] - box) // 2
        tile.alpha_composite(image, (x, y))
    if position != "none":
        color = _hex(config.get("title_color"), "#ffffff")
        if position == "top":
            area = (8, 4, width - 8, band)
        elif position == "bottom":
            area = (8, height - band, width - 8, height - 4)
        elif icon:
            area = (8, height - 72, width - 8, height - 6)
        else:
            area = (10, 10, width - 10, height - 10)
        _draw_title(tile, title, position, color, area)
    buffer = io.BytesIO()
    tile.convert("RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _fit_font(draw: ImageDraw.ImageDraw, text: str, width: int, start: int, minimum: int = 14):
    size = start
    while size > minimum and draw.textlength(text, font=_font(size)) > width:
        size -= 2
    return _font(size), size


def _centered(draw: ImageDraw.ImageDraw, text: str, font, x0: int, x1: int, y: int, color: tuple[int, int, int]) -> None:
    x = x0 + (x1 - x0 - draw.textlength(text, font=font)) / 2
    draw.text((x + 1, y + 2), text, font=font, fill=(0, 0, 0, 140))
    draw.text((x, y), text, font=font, fill=color + (255,))


def _wide_base(config: dict[str, Any]) -> tuple[Image.Image, int]:
    """Background (color and image) plus the optional icon on the left; returns the text start x."""
    width, height = WIDE_SIZE
    tile = Image.new("RGBA", WIDE_SIZE, _hex(config.get("color"), DEFAULT_BACKGROUND) + (255,))
    if _paint_background(tile, config):
        _shade(tile)
    left = 14
    icon = config.get("icon")
    if icon:
        box = 128
        tile.alpha_composite(render_icon(icon, box, resolve_icon_color(icon, config.get("icon_color"))), (16, (height - box) // 2))
        left = 16 + box + 8
    # Opaque RGB so the translucent bar tracks and text shadows drawn next are
    # blended (ImageDraw on an RGBA image replaces pixels, alpha included).
    return tile.convert("RGB"), left


def _render_clock(config: dict[str, Any], display: dict[str, Any], now: float | None) -> Image.Image:
    tile, left = _wide_base(config)
    draw = ImageDraw.Draw(tile, "RGBA")
    width, height = WIDE_SIZE
    color = _hex(config.get("title_color"), "#ffffff")
    moment = time.localtime(now)
    clock = time.strftime("%I:%M" if display["clock_format"] == "12h" else "%H:%M", moment)
    suffix = (" AM" if moment.tm_hour < 12 else " PM") if display["clock_format"] == "12h" else ""
    title = (config.get("title") or "").strip() if config.get("title_position") != "none" else ""
    font, size = _fit_font(draw, clock + suffix, width - left - 14, 92, 40)
    lines_height = size + (34 if display["show_date"] else 0) + (30 if title else 0)
    y = (height - lines_height) // 2 - 6
    if title:
        _centered(draw, title, _fit_font(draw, title, width - left - 14, 22)[0], left, width - 10, y, color)
        y += 30
    _centered(draw, clock + suffix, font, left, width - 10, y, color)
    y += size + 6
    if display["show_date"]:
        date = f"{_WEEKDAYS[moment.tm_wday]} {moment.tm_mday} {_MONTHS[moment.tm_mon - 1]}"
        _centered(draw, date, _font(24), left, width - 10, y, tuple((c * 3 + 160) // 4 for c in color))
    return tile


def _render_dev_clock(config: dict[str, Any], display: dict[str, Any], now: float | None) -> bytes:
    """Animated GIF: a code editor showing the time, with a terminal typing a command.

    The D200 firmware plays GIFs by itself, so the animation runs without the
    host re-uploading frames; the daemon sends a new GIF only when the minute changes.
    """
    width, height = WIDE_SIZE
    background = _hex(config.get("color"), "#0d1117")
    accent = _hex(config.get("title_color"), "#c5f26b")
    muted, keyword, name, text = (125, 133, 144), (255, 123, 114), (121, 192, 255), (201, 209, 217)
    moment = time.localtime(now)
    clock = time.strftime("%I:%M" if display["clock_format"] == "12h" else "%H:%M", moment)
    if display["clock_format"] == "12h":
        clock += " AM" if moment.tm_hour < 12 else " PM"
    snippet = DEV_SNIPPETS[(moment.tm_hour * 60 + moment.tm_min) % len(DEV_SNIPPETS)]
    small, code_font = _mono(13), _mono(15)
    big = _mono(64 if len(clock) <= 5 else 46, bold=True)
    base = Image.new("RGBA", WIDE_SIZE, background + (255,))
    if _paint_background(base, config):
        _shade(base, 150)  # keep the code readable over a photo
    base = base.convert("RGB")

    def frame(typed: str, cursor: bool) -> Image.Image:
        tile = base.copy()
        draw = ImageDraw.Draw(tile, "RGBA")
        draw.rectangle((0, 0, width, 30), fill=(255, 255, 255, 10))
        for x, dot in zip((14, 30, 46), ((255, 95, 86), (255, 189, 46), (39, 201, 63))):
            draw.ellipse((x, 10, x + 10, 20), fill=dot)
        draw.text((66, 8), "clock.ts", font=small, fill=text)
        draw.text((width - 14 - draw.textlength("main ●", font=small), 8), "main ●", font=small, fill=muted)
        for number, y in enumerate((40, 120, 148), 1):
            draw.text((10, y), str(number), font=small, fill=(72, 79, 88))
        draw.text((30, 39), "const", font=code_font, fill=keyword)
        draw.text((30 + draw.textlength("const ", font=code_font), 39), "now", font=code_font, fill=name)
        draw.text((30 + draw.textlength("const now ", font=code_font), 39), "=", font=code_font, fill=text)
        draw.text((30, 52), clock, font=big, fill=accent)
        if display["show_date"]:
            date = f"// {_WEEKDAYS[moment.tm_wday]} {moment.tm_mday} {_MONTHS[moment.tm_mon - 1]} {moment.tm_year}"
            draw.text((30, 120), date, font=code_font, fill=muted)
        prompt = "$ "
        draw.text((30, 148), prompt, font=code_font, fill=accent)
        x = 30 + draw.textlength(prompt, font=code_font)
        draw.text((x, 148), typed, font=code_font, fill=text)
        if cursor:
            cx = x + draw.textlength(typed, font=code_font) + 1
            draw.rectangle((cx, 150, cx + 8, 166), fill=accent)
        return tile

    steps = [(snippet[:end], True) for end in range(0, len(snippet) + 1, 2)]
    if steps[-1][0] != snippet:
        steps.append((snippet, True))
    holds = [(snippet, index % 2 == 0) for index in range(6)]
    frames = [frame(typed, cursor) for typed, cursor in steps + holds]
    durations = [90] * len(steps) + [450] * len(holds)
    # One palette from the complete frame keeps colours stable and the file small.
    palette = frames[len(steps) - 1].quantize(colors=64, method=Image.Quantize.MEDIANCUT)
    indexed = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    buffer = io.BytesIO()
    indexed[0].save(buffer, format="GIF", save_all=True, append_images=indexed[1:], duration=durations, loop=0,
                    optimize=False, disposal=1)
    return buffer.getvalue()


def _render_stats(config: dict[str, Any], metrics: dict[str, int] | None) -> Image.Image:
    tile, left = _wide_base(config)
    draw = ImageDraw.Draw(tile, "RGBA")
    width, height = WIDE_SIZE
    color = _hex(config.get("title_color"), "#ffffff")
    values = metrics or {}
    title = (config.get("title") or "").strip() if config.get("title_position") != "none" else ""
    top = 22
    if title:
        _centered(draw, title, _fit_font(draw, title, width - left - 14, 22)[0], left, width - 10, 14, color)
        top = 48
    columns = (("CPU", "cpu", (197, 242, 107)), ("RAM", "mem", (125, 211, 252)), ("GPU", "gpu", (244, 114, 182)))
    span = (width - left - 10) // 3
    for index, (label, key, accent) in enumerate(columns):
        x0 = left + index * span
        value = max(0, min(100, int(values.get(key, 0))))
        _centered(draw, label, _font(18), x0, x0 + span, top, (190, 196, 210))
        _centered(draw, f"{value}%", _font(38), x0, x0 + span, top + 26, color)
        bar_y, bar_x0, bar_x1 = height - 30, x0 + 12, x0 + span - 12
        draw.rounded_rectangle((bar_x0, bar_y, bar_x1, bar_y + 10), radius=5, fill=(255, 255, 255, 40))
        if value:
            draw.rounded_rectangle((bar_x0, bar_y, bar_x0 + max(10, (bar_x1 - bar_x0) * value // 100), bar_y + 10), radius=5, fill=accent + (255,))
    return tile


def render_wide(config: dict[str, Any] | None, metrics: dict[str, int] | None = None, now: float | None = None,
                values: bool = True) -> bytes:
    """PNG for the wide display: custom image, or the DeckForge clock/statistics.

    values=False draws only the background, for modes where the firmware draws
    the values on top (see firmware_draws_values); the editor preview keeps them.
    """
    config = config or {}
    display = display_settings(config)
    if display["mode"] == "image" or display["renderer"] == "firmware":
        return render_tile(config, WIDE_SIZE)
    if not values:
        image = _wide_base({key: value for key, value in config.items() if key != "icon"})[0]
    elif display["mode"] == "clock" and display["style"] == "dev":
        return _render_dev_clock(config, display, now)
    elif display["mode"] == "clock":
        image = _render_clock(config, display, now)
    else:
        image = _render_stats(config, metrics)
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
