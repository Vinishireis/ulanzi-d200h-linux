import isolation  # noqa: F401  (must precede deckforge imports)
import http.server
import io
import threading
import unittest

from PIL import Image

from deckforge import api, icon_library as lib
from deckforge.render import KEY_SIZE, WIDE_SIZE, has_visual, render_tile, title_position


def png_bytes(color=(255, 0, 0, 255), size=(64, 32)):
    buffer = io.BytesIO()
    Image.new("RGBA", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


class StreamlineCatalog(unittest.TestCase):
    def test_bundled_packs_are_all_cc_by_and_large(self):
        catalog = lib.catalog()
        packs = [s for s in catalog["sets"] if s["id"] != "logos-solid"]
        self.assertEqual(len(packs), 20)
        self.assertGreater(catalog["total"], 23000)
        self.assertTrue(all(s["license"] == "CC BY 4.0" and s["author"] == "Streamline" for s in packs))

    def test_portuguese_search_finds_english_names(self):
        hits = lib.search("casa", "streamline-flex")["items"]
        self.assertTrue(hits and all("home" in h["name"] or "house" in h["name"] for h in hits))
        self.assertGreater(lib.search("musica")["total"], 50)
        self.assertTrue(all("microphone" in h["name"] for h in lib.search("microfone", "packs")["items"][:10]))

    def test_packs_filter_excludes_local_logos_and_category_filter_works(self):
        self.assertFalse(any(h["set"] == "logos-solid" for h in lib.search("github", "packs", limit=300)["items"]))
        category = lib.catalog()["sets"][1]["categories"][0]["name"]
        set_id = lib.catalog()["sets"][1]["id"]
        result = lib.search("", set_id, category)
        self.assertGreater(result["total"], 0)
        self.assertTrue(all(h["set"] == set_id for h in result["items"]))

    def test_pack_svg_is_a_complete_document_without_current_color(self):
        ref = lib.search("home", "streamline-flex")["items"][0]["ref"]
        svg = lib.pack_svg(ref).decode()
        self.assertTrue(svg.startswith("<svg") and "viewBox" in svg and "currentColor" not in svg)
        with self.assertRaises(lib.IconError):
            lib.pack_svg("pack:streamline-flex:nao-existe")
        with self.assertRaises(lib.IconError):
            lib.pack_svg("pack:../../etc:passwd")

    def test_monochrome_icons_default_to_white_and_color_icons_keep_colors(self):
        mono = lib.search("home", "streamline-flex")["items"][0]["ref"]
        color = lib.search("fire", "streamline-emojis")["items"][0]["ref"]
        self.assertEqual(lib.resolve_icon_color(mono, None), "#ffffff")
        self.assertIsNone(lib.resolve_icon_color(color, None))
        self.assertIsNone(lib.resolve_icon_color(mono, "original"))
        self.assertEqual(lib.resolve_icon_color(color, "#123456"), "#123456")
        tinted = lib.render_icon(mono, 64, "#00ff00")
        opaque = [p for p in tinted.getdata() if p[3] > 200]
        self.assertTrue(opaque and all(p[:3] == (0, 255, 0) for p in opaque))


class UserIcons(unittest.TestCase):
    def test_upload_png_is_normalized_and_listed(self):
        result = lib.save_user_icon(png_bytes(), "Meu logo")
        self.assertRegex(result["ref"], r"^user:[0-9a-f]{32}\.png$")
        with Image.open(lib.user_icon_path(result["ref"])) as image:
            self.assertEqual(image.size, (256, 256))
        self.assertIn(result, lib.list_user_icons())
        self.assertIsNone(lib.resolve_icon_color(result["ref"], None))  # photos keep their colors

    def test_svg_upload_renders_and_scripts_are_refused(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="#0f0"/></svg>'
        self.assertTrue(lib.save_user_icon(svg, "círculo")["ref"].startswith("user:"))
        with self.assertRaisesRegex(lib.IconError, "scripts"):
            lib.save_user_icon(b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')

    def test_invalid_and_oversized_files_are_refused(self):
        with self.assertRaises(lib.IconError):
            lib.save_user_icon(b"not an image")
        with self.assertRaisesRegex(lib.IconError, "5 MB"):
            lib.save_user_icon(b"\x89PNG" + bytes(lib.MAX_IMAGE_BYTES + 1))

    def test_import_from_url_downloads_and_rejects_other_schemes(self):
        payload = png_bytes((0, 0, 255, 255))

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.send_header("Content-Type", "image/png"); self.end_headers(); self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            result = lib.import_icon_url(f"http://127.0.0.1:{server.server_port}/logo-azul.png")
        finally:
            server.shutdown()
        self.assertEqual(result["name"], "logo azul")
        for url in ("file:///etc/passwd", "ftp://example.com/a.png", "javascript:alert(1)"):
            with self.subTest(url=url), self.assertRaises(lib.IconError):
                lib.import_icon_url(url)

    def test_icon_in_use_cannot_be_deleted(self):
        ref = lib.save_user_icon(png_bytes((9, 9, 9, 255)), "em uso")["ref"]
        api.store.save_button("1_1", {"type": "none", "title": "x", "icon": ref})
        with self.assertRaises(api.HTTPException) as caught:
            api.delete_user_icon(ref.removeprefix("user:"))
        self.assertEqual(caught.exception.status_code, 409)
        api.store.delete_button("1_1")
        api.delete_user_icon(ref.removeprefix("user:"))
        self.assertFalse(lib.user_icon_path(ref).exists())


class Rendering(unittest.TestCase):
    def test_title_defaults(self):
        self.assertEqual(title_position({"title": "Copiar"}), "middle")
        self.assertEqual(title_position({"title": "Copiar", "icon": "x"}), "none")
        self.assertEqual(title_position({"title": "Copiar", "icon": "x", "title_position": "bottom"}), "bottom")
        self.assertFalse(has_visual({"type": "keys", "keys": ["ctrl", "c"]}))

    def test_key_and_wide_tiles_have_lcd_sizes(self):
        icon = lib.search("home", "streamline-flex")["items"][0]["ref"]
        for config, size in (({"title": "Texto longo para quebrar em duas linhas", "color": "#4c68ff"}, KEY_SIZE),
                             ({"icon": icon, "title": "Início", "title_position": "bottom"}, KEY_SIZE),
                             ({"icon": icon, "title": "Modo foco", "title_position": "middle"}, WIDE_SIZE)):
            with Image.open(io.BytesIO(render_tile(config, size))) as image:
                self.assertEqual((image.size, image.mode), (size, "RGB"))

    def test_preview_endpoint_matches_lcd_renderer(self):
        config = api.ButtonConfig(type="none", title="Olá", color="#19ae87")
        response = api.render_preview(api.TilePreview(key="0_0", config=config))
        self.assertEqual(response.body, render_tile(config.model_dump(exclude_none=True)))
        wide = api.render_preview(api.TilePreview(key="2_3", config=config))
        with Image.open(io.BytesIO(wide.body)) as image:
            self.assertEqual(image.size, WIDE_SIZE)


if __name__ == "__main__":
    unittest.main()
