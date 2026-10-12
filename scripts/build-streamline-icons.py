#!/usr/bin/env python3
"""Build the bundled Streamline icon packs from the Iconify icon-sets data.

Streamline publishes its free sets under CC BY 4.0; Iconify redistributes them
as JSON (https://github.com/iconify/icon-sets). This script keeps only what
DeckForge needs (SVG bodies, sizes, categories), rejects any icon body with
scripts, external references or embedded documents, and writes:

  apps/desktop/public/icons/streamline/<prefix>.json   compact pack
  apps/desktop/public/icons/streamline/catalog.json    sets, categories, names

Usage:
  python3 scripts/build-streamline-icons.py --download
  python3 scripts/build-streamline-icons.py --source DIR   # pre-downloaded <prefix>.json files
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

ICONIFY_RAW = "https://raw.githubusercontent.com/iconify/icon-sets/master/json/{prefix}.json"
PREFIXES = [
    "streamline", "streamline-flex", "streamline-flex-color", "streamline-plump", "streamline-plump-color",
    "streamline-sharp", "streamline-sharp-color", "streamline-ultimate", "streamline-ultimate-color",
    "streamline-freehand", "streamline-freehand-color", "streamline-cyber", "streamline-cyber-color",
    "streamline-color", "streamline-kameleon-color", "streamline-stickies-color", "streamline-emojis",
    "streamline-logos", "streamline-pixel", "streamline-block",
]
UNSAFE = re.compile(r"<script|<foreignObject|<image|<iframe|\bon[a-z]+\s*=|javascript:|@import|"
                    r"(?:xlink:)?href\s*=\s*[\"'](?!#)|url\(\s*[\"']?(?!#)", re.I)
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,159}$")
DEFAULT_SIZE = 16


def load(prefix: str, source: Path | None) -> dict:
    if source is not None:
        return json.loads((source / f"{prefix}.json").read_text(encoding="utf-8"))
    with urllib.request.urlopen(ICONIFY_RAW.format(prefix=prefix), timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def build(data: dict) -> tuple[dict, dict, int]:
    info = data.get("info", {})
    license_info = info.get("license", {})
    if license_info.get("spdx") != "CC-BY-4.0":
        raise SystemExit(f"{data.get('prefix')}: licença inesperada {license_info!r}; nada foi gravado.")
    width, height = data.get("width", DEFAULT_SIZE), data.get("height", DEFAULT_SIZE)
    icons, rejected = {}, 0
    for name, icon in sorted(data["icons"].items()):
        body = icon.get("body", "")
        if not NAME.match(name) or not body or len(body) > 60_000 or UNSAFE.search(body):
            rejected += 1
            continue
        entry = {"body": body}
        for key in ("width", "height", "left", "top"):
            if key in icon and icon[key] != {"width": width, "height": height}.get(key, 0):
                entry[key] = icon[key]
        icons[name] = entry
    categories = {category: sorted(n for n in names if n in icons)
                  for category, names in sorted(data.get("categories", {}).items())}
    pack = {"prefix": data["prefix"], "width": width, "height": height, "icons": icons}
    meta = {"prefix": data["prefix"], "name": info.get("name", data["prefix"]), "total": len(icons),
            "palette": bool(info.get("palette")), "license": license_info.get("title", "CC BY 4.0"),
            "author": info.get("author", {}).get("name", "Streamline"),
            "categories": {c: n for c, n in categories.items() if n}, "names": sorted(icons)}
    return pack, meta, rejected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", type=Path, help="diretório com <prefix>.json do Iconify")
    group.add_argument("--download", action="store_true", help="baixar do repositório iconify/icon-sets")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "apps/desktop/public/icons/streamline")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    catalog, total = [], 0
    for prefix in PREFIXES:
        pack, meta, rejected = build(load(prefix, None if args.download else args.source))
        (args.out / f"{prefix}.json").write_text(json.dumps(pack, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
        catalog.append(meta)
        total += meta["total"]
        print(f"{prefix:28} {meta['total']:5} ícones{f' ({rejected} rejeitados)' if rejected else ''}")
    (args.out / "catalog.json").write_text(json.dumps({"sets": catalog, "total": total}, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"Total: {total} ícones em {len(catalog)} pacotes -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
