"""Hover detail card content and layout (pure + optional WinForms render)."""
from __future__ import annotations

import sys
import unittest


def _avg_opaque(bitmap, threshold=40):
    """Mean RGB of opaque pixels in a System.Drawing Bitmap."""
    from System.Drawing import Rectangle
    from System.Drawing.Imaging import ImageLockMode, PixelFormat
    from System import Array, Byte
    from System.Runtime.InteropServices import Marshal
    w, h = bitmap.Width, bitmap.Height
    data = bitmap.LockBits(Rectangle(0, 0, w, h), ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb)
    try:
        arr = Array.CreateInstance(Byte, data.Stride * h)
        Marshal.Copy(data.Scan0, arr, 0, len(arr))
        stride = data.Stride
    finally:
        bitmap.UnlockBits(data)
    rs = gs = bs = n = 0
    buf = bytes(arr)
    for y in range(h):
        for x in range(w):
            i = y * stride + x * 4
            if buf[i + 3] <= threshold:
                continue
            bs += buf[i]; gs += buf[i + 1]; rs += buf[i + 2]; n += 1
    if not n:
        return (0, 0, 0)
    return (rs // n, gs // n, bs // n)


from vram_radar.hover_detail import (
    THIN, build_hover_rows, card_position, codex_hover_row, hover_card_spec,
    hover_tooltip_text, inactive_status, provider_hover_row, thin_reset, updated_ago,
)
from vram_radar.reset_format import reset_full


NOW = 1_700_000_000


class FormatTests(unittest.TestCase):
    def test_thin_reset_and_updated_ago(self):
        self.assertEqual(thin_reset(30 * 3600), f"1d{THIN}6h")
        self.assertEqual(updated_ago(NOW - 180, NOW, True), "Updated 3 min ago")
        self.assertEqual(updated_ago(NOW - 180, NOW, False), "3 分钟前更新")
        self.assertEqual(updated_ago(NOW - 10, NOW, True), "Updated just now")
        self.assertEqual(updated_ago(None, NOW, True), "")
        self.assertEqual(updated_ago(NOW + 60, NOW, True), "")

    def test_inactive_status_bilingual(self):
        self.assertEqual(inactive_status({"running": False, "installed": True}, True), "Not running")
        self.assertEqual(inactive_status({"running": False, "installed": True}, False), "未运行")
        self.assertEqual(inactive_status({"signed_in": False, "installed": True}, False), "未登录")
        self.assertEqual(inactive_status({"session_relogin": True}, False), "需登录")
        self.assertEqual(inactive_status({"stale": True}, True), "Stale")


class CodexRowTests(unittest.TestCase):
    def test_windows_remaining_and_reset(self):
        state = {"enabled": True, "state": "ready", "fetched_at": NOW - 120, "windows": [
            {"name": "5 hour", "remaining_percent": 85, "window_minutes": 300, "resets_at": NOW + 2.3 * 3600},
            {"name": "Weekly", "remaining_percent": 60, "window_minutes": 10080, "resets_at": NOW + 88 * 3600},
        ]}
        row = codex_hover_row(state, "en", now=NOW)
        texts = [line["text"] for line in row["lines"]]
        self.assertEqual(row["name"], "Codex")
        self.assertIn("5 hour · 85% left", texts[0])
        self.assertIn(f"2h · {reset_full(NOW + 2.3 * 3600, True)}", texts[1])
        self.assertIn("Weekly · 60% left", texts[2])
        self.assertEqual(texts[-1], "Updated 2 min ago")

    def test_zh_and_no_invention(self):
        state = {"enabled": True, "state": "ready", "windows": [
            {"name": "5 hour", "remaining_percent": 40, "window_minutes": 300, "resets_at": NOW + 3600},
        ]}
        row = codex_hover_row(state, "zh-CN", now=NOW)
        self.assertIn("5 小时", row["lines"][0]["text"])
        self.assertIn("剩余 40%", row["lines"][0]["text"])
        weekly = codex_hover_row(
            {"enabled": True, "state": "ready", "windows": [
                {"name": "Weekly", "remaining_percent": 10, "window_minutes": 10080}]},
            "zh-CN", now=NOW)
        self.assertIn("每周", weekly["lines"][0]["text"])
        # No absolute when reset missing
        empty = codex_hover_row({"enabled": True, "state": "ready", "windows": [
            {"remaining_percent": 10, "window_minutes": 300},
        ]}, "en", now=NOW)
        self.assertEqual([line["text"] for line in empty["lines"]], ["5 hour · 10% left"])


class ProviderRowTests(unittest.TestCase):
    def test_deepseek_balance(self):
        row = provider_hover_row(
            {"installed": True, "running": True, "signed_in": True,
             "headline": {"zh": "¥6.00", "en": "¥6.00"}, "quota": {"zh": "¥6.00", "en": "¥6.00"},
             "fetched_at": NOW - 300},
            provider_id="deepseek", name="DeepSeek", language="en", now=NOW)
        self.assertEqual(row["lines"][0]["text"], "Balance ¥6.00")
        self.assertEqual(row["lines"][-1]["text"], "Updated 5 min ago")

    def test_inactive_grey_status(self):
        row = provider_hover_row(
            {"installed": True, "running": False, "signed_in": True},
            provider_id="claude", name="Claude", language="zh-CN", now=NOW)
        self.assertEqual(row["lines"][0], {"text": "未运行", "tone": "status"})
        self.assertTrue(row["inactive"])

    def test_grok_used_and_remaining(self):
        row = provider_hover_row(
            {"installed": True, "running": True, "signed_in": True,
             "quota": {"zh": "25%", "en": "25%"}, "quota_percent": 25,
             "brief": {"zh": "已用 75%", "en": "75% used"},
             "reset_at": NOW + 30 * 3600, "fetched_at": NOW - 60},
            provider_id="grok", name="Grok", language="en", now=NOW)
        self.assertIn("75% used", row["lines"][0]["text"])
        self.assertIn("25% left", row["lines"][0]["text"])
        self.assertIn(THIN, row["lines"][1]["text"])


class BuildTests(unittest.TestCase):
    def test_selection_order_and_tooltip_text(self):
        codex = {"enabled": True, "state": "ready", "windows": [
            {"remaining_percent": 50, "window_minutes": 300, "resets_at": NOW + 7200}]}
        providers = {
            "deepseek": {"installed": True, "running": True, "signed_in": True,
                         "quota": {"zh": "¥1", "en": "¥1"}, "headline": {"zh": "¥1", "en": "¥1"}},
            "claude": {"installed": True, "running": False},
        }
        rows = build_hover_rows(codex, providers, ["codex", "deepseek", "claude"],
                                language="en", now=NOW,
                                names={"deepseek": "DeepSeek", "claude": "Claude"})
        self.assertEqual([r["name"] for r in rows], ["Codex", "DeepSeek", "Claude"])
        tip = hover_tooltip_text(rows)
        self.assertIn("Codex", tip)
        self.assertIn("DeepSeek", tip)
        self.assertIn("Not running", tip)
        spec = hover_card_spec(rows, "en")
        self.assertEqual(spec["kind"], "hover")
        self.assertEqual(len(spec["rows"]), 3)

    def test_all_inactive_edge(self):
        rows = build_hover_rows(None, {
            "claude": {"installed": True, "running": False},
            "qwen": {"installed": False},
        }, ["claude", "qwen"], language="zh-CN", now=NOW,
           names={"claude": "Claude", "qwen": "Qwen 通义"})
        self.assertEqual([r["lines"][0]["text"] for r in rows], ["未运行", "未安装"])

    def test_long_values_kept(self):
        long_name = "腾讯元宝应用客户端"
        row = provider_hover_row(
            {"installed": True, "running": True, "signed_in": True,
             "headline": {"zh": "已登录但无公开额度数据可显示", "en": "Signed in; no public quota fields"},
             "quota": {"zh": "已登录但无公开额度数据可显示", "en": "Signed in; no public quota fields"}},
            provider_id="yuanbao", name=long_name, language="zh-CN", now=NOW)
        self.assertEqual(row["name"], long_name)
        self.assertGreater(len(row["lines"][0]["text"]), 10)

    def test_english_has_no_cjk(self):
        import re
        cjk = re.compile(r"[　-鿿＀-￯]")
        codex = {"enabled": True, "state": "ready", "fetched_at": NOW - 60, "windows": [
            {"name": "5 hour", "remaining_percent": 12, "window_minutes": 300, "resets_at": NOW + 90000}]}
        rows = build_hover_rows(codex, {
            "grok": {"installed": True, "running": False, "signed_in": False},
            "deepseek": {"installed": True, "running": True, "quota": {"en": "$1.25", "zh": "$1.25"},
                         "headline": {"en": "$1.25", "zh": "$1.25"}, "fetched_at": NOW - 120},
        }, ["codex", "grok", "deepseek"], language="en", now=NOW,
           names={"grok": "Grok", "deepseek": "DeepSeek"})
        blob = hover_tooltip_text(rows)
        self.assertFalse(cjk.search(blob), blob)
        for row in hover_card_spec(rows, "en")["rows"]:
            for line in row["lines"]:
                self.assertFalse(cjk.search(line["text"]), line)


class PositionTests(unittest.TestCase):
    def test_above_strip_clamped_to_work_area(self):
        x, y = card_position((100, 900, 300, 940), (280, 120), (0, 0, 1920, 1040), 8)
        self.assertEqual((x, y), (100, 900 - 120 - 8))
        # Near top of work area: flip below
        x, y = card_position((100, 20, 300, 60), (280, 120), (0, 0, 1920, 1040), 8)
        self.assertEqual(y, 60 + 8)
        # Multi-monitor left offset
        x, y = card_position((-800, 900, -500, 940), (280, 120), (-1920, 0, 0, 1040), 8)
        self.assertGreaterEqual(x, -1920 + 8)
        self.assertLessEqual(x + 280, 0 - 8)


class IconPipelineTests(unittest.TestCase):
    def test_rows_carry_strip_icon_fields(self):
        rows = build_hover_rows(
            {"enabled": True, "state": "ready", "windows": [
                {"name": "5 hour", "remaining_percent": 50, "window_minutes": 300}]},
            {"deepseek": {"installed": True, "running": True,
                          "quota": {"en": "¥1", "zh": "¥1"}, "headline": {"en": "¥1", "zh": "¥1"},
                          "install_path": r"D:\Apps\DeepSeek\DeepSeek.exe"},
             "claude": {"installed": False, "running": False}},
            ["codex", "deepseek", "claude"], language="en", now=NOW,
            names={"deepseek": "DeepSeek", "claude": "Claude"},
            icon_paths={"codex": None, "deepseek": r"D:\Apps\DeepSeek\DeepSeek.exe", "claude": None},
            icon_names={"codex": "Codex", "deepseek": "DeepSeek", "claude": "Claude"},
        )
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id["codex"]["icon_name"], "Codex")
        self.assertEqual(by_id["deepseek"]["icon_path"], r"D:\Apps\DeepSeek\DeepSeek.exe")
        self.assertEqual(by_id["deepseek"]["icon_name"], "DeepSeek")
        self.assertEqual(by_id["claude"]["icon_name"], "Claude")

    def test_window_title_stock_forms(self):
        from vram_radar.hover_detail import window_title
        self.assertEqual(window_title({"name": "5 hour", "window_minutes": 300}, False), "5 小时")
        self.assertEqual(window_title({"name": "Weekly", "window_minutes": 10080}, False), "每周")
        self.assertEqual(window_title({"name": "5 hour", "window_minutes": 300}, True), "5 hour")
        self.assertEqual(window_title({"name": "Custom", "window_minutes": 300}, True), "Custom")



class IconThemeContrastTests(unittest.TestCase):
    def test_bundled_variant_follows_surface_not_only_taskbar(self):
        from vram_radar import ui_dialogs as ui
        light = ui.bundled_icon_path("Codex", light=True)
        dark = ui.bundled_icon_path("Codex", light=False)
        self.assertIsNotNone(light)
        self.assertIsNotNone(dark)
        self.assertIn("openai-light.png", light.replace("\\", "/"))
        self.assertIn("openai-dark.png", dark.replace("\\", "/"))
        self.assertNotEqual(light, dark)

    def test_surface_theme_and_contrast_helpers(self):
        from vram_radar import ui_dialogs as ui
        self.assertTrue(ui.surface_is_light((255, 255, 255)))
        self.assertFalse(ui.surface_is_light((44, 44, 44)))
        self.assertTrue(ui.icon_contrast_ok((240, 240, 240), (44, 44, 44)))
        self.assertTrue(ui.icon_contrast_ok((20, 20, 20), (255, 255, 255)))
        self.assertFalse(ui.icon_contrast_ok((30, 30, 30), (44, 44, 44)))

    def test_palette_surfaces_need_matching_glyph(self):
        from vram_radar import ui_dialogs as ui
        light_pal = ui.palette(False)
        dark_pal = ui.palette(True)
        self.assertTrue(ui.surface_is_light(light_pal["surface"]))
        self.assertFalse(ui.surface_is_light(dark_pal["surface"]))
        self.assertFalse(ui.icon_contrast_ok((20, 20, 20), dark_pal["surface"]))
        self.assertTrue(ui.icon_contrast_ok((230, 230, 230), dark_pal["surface"]))
        self.assertTrue(ui.icon_contrast_ok((20, 20, 20), light_pal["surface"]))




class HoverLifecycleTests(unittest.TestCase):
    def test_empty_rows_spec_is_empty(self):
        spec = hover_card_spec([], "en")
        self.assertEqual(spec["rows"], [])
        self.assertEqual(build_hover_rows(None, {}, [], language="en"), [])

    def test_card_position_clamps_multi_monitor_and_tall_card(self):
        # Tall card near top flips below; wide card clamps into work area.
        x, y = card_position((10, 40, 100, 80), (400, 300), (0, 0, 500, 400), 8)
        self.assertGreaterEqual(x, 8)
        self.assertLessEqual(x + 400, 500 - 8)
        self.assertEqual(y, 80 + 8)  # flipped below
        x, y = card_position((-900, 800, -700, 840), (280, 100), (-1920, 0, 0, 1040), 8)
        self.assertGreaterEqual(x, -1920 + 8)
        self.assertLessEqual(x + 280, -8)


@unittest.skipUnless(sys.platform == "win32", "WinForms GDI cycle")
class HoverGdiCycleTests(unittest.TestCase):
    def test_thousand_show_hide_cycles_do_not_grow_gdi(self):
        import ctypes
        import clr
        clr.AddReference("System.Drawing")
        clr.AddReference("System.Windows.Forms")
        from vram_radar import ui_dialogs as ui
        from vram_radar.hover_detail import build_hover_rows, hover_card_spec

        GetGuiResources = ctypes.windll.user32.GetGuiResources
        GetGuiResources.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        GetGuiResources.restype = ctypes.c_uint
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetCurrentProcess()

        rows = build_hover_rows(
            {"enabled": True, "state": "ready", "windows": [
                {"name": "5 hour", "remaining_percent": 50, "window_minutes": 300, "resets_at": NOW + 7200}]},
            {"claude": {"installed": True, "running": False}},
            ["codex", "claude"], language="en", now=NOW,
            names={"claude": "Claude"}, icon_names={"codex": "Codex", "claude": "Claude"})
        spec = hover_card_spec(rows, "en")
        anchor = (100, 800, 300, 840)

        def counts():
            return GetGuiResources(handle, 0), GetGuiResources(handle, 1)

        # Warm up (create form once).
        ui.show_hover_card(spec, anchor, scale=1.0, dark=True, delay_ms=0)
        ui.hide_hover_card()
        before = counts()
        for i in range(1000):
            ui.show_hover_card(spec, anchor, scale=1.0, dark=(i % 2 == 0), delay_ms=0)
            ui.hide_hover_card()
        after = counts()
        ui.close_hover_card()
        # Allow a small amount of noise; 1000 cycles must not leak hundreds of objects.
        self.assertLessEqual(after[0] - before[0], 30, (before, after))
        self.assertLessEqual(after[1] - before[1], 30, (before, after))
        self.assertFalse(ui._HOVER.get("shown"))
        self.assertIsNone(ui._HOVER.get("form"))

@unittest.skipUnless(sys.platform == "win32", "WinForms offscreen render")
class HoverRenderTests(unittest.TestCase):
    def test_offscreen_zh_en_light_dark(self):
        import clr
        clr.AddReference("System.Drawing")
        clr.AddReference("System.Windows.Forms")
        from vram_radar import ui_dialogs as ui
        rows = build_hover_rows(
            {"enabled": True, "state": "ready", "fetched_at": NOW - 180, "windows": [
                {"name": "5 hour", "remaining_percent": 85, "window_minutes": 300, "resets_at": NOW + 7200},
                {"name": "Weekly", "remaining_percent": 60, "window_minutes": 10080, "resets_at": NOW + 88 * 3600},
            ]},
            {
                "deepseek": {"installed": True, "running": True, "signed_in": True,
                             "quota": {"zh": "¥6.00", "en": "¥6.00"}, "headline": {"zh": "¥6.00", "en": "¥6.00"},
                             "fetched_at": NOW - 300},
                "claude": {"installed": True, "running": False},
            },
            ["codex", "deepseek", "claude"], language="zh-CN", now=NOW,
            names={"deepseek": "DeepSeek", "claude": "Claude"},
            icon_names={"codex": "Codex", "deepseek": "DeepSeek", "claude": "Claude"},
        )
        self.assertIn("5 小时", rows[0]["lines"][0]["text"])
        self.assertIn("每周", rows[0]["lines"][2]["text"])
        for language, lang_rows in (("zh-CN", rows), ("en", build_hover_rows(
                {"enabled": True, "state": "ready", "fetched_at": NOW - 180, "windows": [
                    {"name": "5 hour", "remaining_percent": 85, "window_minutes": 300, "resets_at": NOW + 7200}]},
                {"claude": {"installed": True, "running": False}},
                ["codex", "claude"], language="en", now=NOW, names={"claude": "Claude"}))):
            spec = hover_card_spec(lang_rows, language)
            for dark in (False, True):
                pal = ui.palette(dark)
                bitmap, layout = ui.render_hover(spec, 1.25, pal)
                try:
                    self.assertGreater(layout["width"], 100)
                    self.assertGreater(layout["height"], 40)
                    self.assertEqual(bitmap.Width, layout["width"])
                    icon = ui.provider_icon(None, 24, "Codex", light=ui.surface_is_light(pal["surface"]))
                    ink = _avg_opaque(icon)
                    self.assertTrue(ui.icon_contrast_ok(ink, pal["surface"]), (language, dark, ink, pal["surface"]))
                finally:
                    bitmap.Dispose()


if __name__ == "__main__":
    unittest.main()
