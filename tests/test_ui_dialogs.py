import time
import unittest

from vram_radar import ui_dialogs as ui
from vram_radar.usage_surface import CONSENT_ETA_SECONDS, auto_read_status, consent_precheck, still_pending


class DialogSpecTests(unittest.TestCase):
    def test_consent_headline_first_short_lines_and_eta(self):
        spec = ui.consent_spec("Grok", "Grok", CONSENT_ETA_SECONDS)
        self.assertEqual(spec["headline"], "允许显存雷达自动读取 Grok 额度？")
        self.assertEqual(len(spec["lines"]), 3)
        self.assertTrue(all(len(line) <= 24 for line in spec["lines"]))
        self.assertIn("只读", spec["lines"][0])
        self.assertIn("不保存、不上传", spec["lines"][1])
        self.assertIn("右键菜单", spec["lines"][2])
        self.assertEqual(spec["note"], f"允许后约 {CONSENT_ETA_SECONDS} 秒内显示在任务栏，之后每 5 分钟更新")
        self.assertEqual(spec["buttons"], [("allow", "允许", True), ("cancel", "暂不", False)])
        self.assertEqual(spec["cancel"], "cancel")

    def test_notice_splits_headline_from_body(self):
        spec = ui.notice_spec(consent_precheck({"installed": False}, "Kimi"), "Kimi")
        self.assertEqual(spec["headline"], "未检测到 Kimi")
        self.assertEqual(len(spec["lines"]), 1)
        self.assertEqual(spec["buttons"], [("ok", "知道了", True)])

    def test_revoke_has_primary_and_cancel(self):
        spec = ui.revoke_spec("Kimi")
        self.assertEqual(spec["headline"], "关闭 Kimi 的自动读取？")
        self.assertEqual([b[0] for b in spec["buttons"]], ["revoke", "cancel"])

    def test_toast_has_no_buttons(self):
        self.assertEqual(ui.toast_spec("正在读取 Grok 额度…")["buttons"], [])


class DialogLayoutTests(unittest.TestCase):
    def test_two_buttons_share_footer_equally_and_scale(self):
        normal = ui.button_rects(420, 100, 1.0, [1, 2])
        big = ui.button_rects(630, 150, 1.5, [1, 2])
        self.assertEqual(normal[0][2], normal[1][2])
        self.assertEqual(normal[0][0], 24)
        self.assertEqual(normal[1][0] + normal[1][2], 420 - 24)
        self.assertEqual(big[0][3], 48)
        self.assertEqual(ui.hit_button(normal, (normal[1][0] + 1, normal[1][1] + 1)), 1)
        self.assertIsNone(ui.hit_button(normal, (0, 0)))

    def test_single_button_sits_right(self):
        (x, _, w, _), = ui.button_rects(420, 0, 1.0, [1])
        self.assertEqual(x + w, 396)


class PaletteTests(unittest.TestCase):
    def test_primary_keeps_contrast_in_both_themes(self):
        for dark in (False, True):
            for accent in (None, (255, 185, 0), (0, 99, 177), (16, 16, 16)):
                pal = ui.palette(dark, accent)
                text_lum, fill_lum = ui._lum(pal["primary_text"]), ui._lum(pal["primary"])
                self.assertGreater(abs(text_lum - fill_lum), 90, (dark, accent))

    def test_light_and_dark_differ(self):
        self.assertNotEqual(ui.palette(False)["surface"], ui.palette(True)["surface"])
        self.assertGreater(ui._lum(ui.palette(True)["text"]), 200)


class IconTests(unittest.TestCase):
    def test_candidates_start_with_exe_then_resources(self):
        found = ui.icon_candidates(r"D:\Download\Kimi\Kimi.exe")
        self.assertEqual(found[0], r"D:\Download\Kimi\Kimi.exe")
        self.assertTrue(any(c.endswith(r"resources\icon.ico") or c.endswith("resources/icon.ico") for c in found))
        self.assertEqual(ui.icon_candidates(""), [])


class AutoReadMenuTests(unittest.TestCase):
    def test_states(self):
        self.assertEqual(auto_read_status({"installed": True}, True), ("已开启 ✓", True))
        self.assertEqual(auto_read_status({"installed": True, "session_relogin": True}, True)[0], "已开启 · 需重新登录")
        self.assertEqual(auto_read_status({"installed": False}, False), ("未安装", False))
        self.assertEqual(auto_read_status({"installed": True, "signed_in": False}, False), ("需登录", True))
        self.assertEqual(auto_read_status(None, False), ("未开启", True))
        self.assertEqual(auto_read_status(None, False, english=True), ("Off", True))

    def test_pending_until_session_result_after_grant(self):
        granted = 1000.0
        self.assertTrue(still_pending(None, granted, granted + 1))
        self.assertTrue(still_pending({"session_quota": "ok", "checked_at": granted - 5}, granted, granted + 2))
        self.assertFalse(still_pending({"session_quota": "ok", "checked_at": granted + 1}, granted, granted + 3))
        self.assertFalse(still_pending(None, granted, granted + 61))


class FetchNowTests(unittest.TestCase):
    def test_fetch_now_resets_cache_and_reprobes_after_inflight_probe(self):
        from vram_radar.providers import ProviderMonitor, grok
        calls = []
        def probe(**_):
            calls.append(time.monotonic())
            if len(calls) == 1:
                monitor.fetch_now("grok")   # consent arrives while the first probe runs
            return {}
        monitor = ProviderMonitor(probe=probe, interval=3600, screen_reader=False)
        grok.CACHE.next_at, grok.CACHE.failures = 9e18, 3
        try:
            monitor.configure(True, "", ["codex", "grok"])
            deadline = time.monotonic() + 3
            while len(calls) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertGreaterEqual(len(calls), 2)
            self.assertEqual((grok.CACHE.next_at, grok.CACHE.failures), (0.0, 0))
        finally:
            monitor.close()
            grok.CACHE.next_at, grok.CACHE.failures = 0.0, 0


class MenuWidthTests(unittest.TestCase):
    def test_footers_do_not_stretch_rows(self):
        from vram_radar.usage_surface import AUTO_READ_FOOTER, limit_menu_text
        # widest auto-read row is about "Kimi" + gap + "已开启 · 需重新登录" (~15 CJK-width chars)
        self.assertNotIn("\n", AUTO_READ_FOOTER[0])
        self.assertLessEqual(len(AUTO_READ_FOOTER[0]), 8)
        self.assertEqual(limit_menu_text(4), "最多显示 4 个")
        self.assertLessEqual(len(limit_menu_text(4)), len("Qwen 通义") + len("仅有旧数据"))

    def test_tray_rect_matches_shared_user32_argtypes(self):
        from ctypes import wintypes
        from vram_radar import tray
        self.assertIs(tray._WindowRect, wintypes.RECT)


class MenuTierTests(unittest.TestCase):
    def test_running_then_installed_then_other_ticked_first(self):
        from vram_radar.usage_surface import tiered
        ids = ["codex", "deepseek", "grok", "kimi", "claude", "glm", "qwen", "yuanbao"]
        states = {"codex": {"installed": True, "running": True}, "deepseek": {"installed": True, "running": False},
                  "grok": {"installed": True, "running": True}, "kimi": {"installed": True, "running": None},
                  "claude": {"installed": False}, "glm": {"installed": False},
                  "qwen": {"installed": False, "code": "leftover_data"}, "yuanbao": {"installed": True}}
        rows = tiered(ids, {"grok", "kimi", "qwen"}, states)
        self.assertEqual([pid for _, pid in rows],
                         ["grok", "codex", "kimi", "deepseek", "yuanbao", "qwen", "claude", "glm"])
        self.assertEqual([t for t, _ in rows], [0, 0, 1, 1, 1, 2, 2, 2])

    def test_unprobed_counts_as_installed_and_sort_is_stable(self):
        from vram_radar.usage_surface import tiered
        rows = tiered(["grok", "kimi"], set(), {})
        self.assertEqual(rows, [(1, "grok"), (1, "kimi")])
        self.assertEqual(tiered(["grok", "kimi"], set(), {}), rows)
        self.assertEqual(tiered(["grok", "kimi"], {"kimi"}, {"grok": {"installed": False}}), [(1, "kimi"), (2, "grok")])


class StripIconTests(unittest.TestCase):
    def test_profile_default_text_migration_and_round_trip(self):
        from vram_radar.models import Profile
        raw = Profile.empty("new").to_dict()
        self.assertEqual(raw["usage_labels"], "text")
        self.assertNotIn("usage_icons", raw)
        raw.pop("usage_labels")
        self.assertEqual(Profile.from_dict(raw).usage_labels, "text")       # older profiles
        self.assertEqual(Profile.from_dict({**raw, "usage_icons": True}).usage_labels, "icons")  # earlier toggle
        self.assertEqual(Profile.from_dict({**raw, "usage_icons": False}).usage_labels, "text")
        self.assertEqual(Profile.from_dict({**raw, "usage_labels": "emoji"}).usage_labels, "text")
        migrated = Profile.from_dict({**raw, "usage_icons": True}).to_dict()
        self.assertEqual((migrated["usage_labels"], "usage_icons" in migrated), ("icons", False))

    def test_msix_asset_choice(self):
        names = ["Square44x44Logo.png", "Square44x44Logo.scale-200.png",
                 "Square44x44Logo.targetsize-16_altform-unplated.png", "Square44x44Logo.targetsize-16_altform-lightunplated.png",
                 "Square44x44Logo.targetsize-24_altform-unplated.png", "Square44x44Logo.targetsize-24_altform-lightunplated.png",
                 "Square44x44Logo.targetsize-48_altform-lightunplated.png"]
        self.assertEqual(ui.pick_msix_asset(names, "Square44x44Logo.png", 24, light=True),
                         "Square44x44Logo.targetsize-24_altform-lightunplated.png")
        self.assertEqual(ui.pick_msix_asset(names, "Square44x44Logo.png", 20, light=False),
                         "Square44x44Logo.targetsize-24_altform-unplated.png")
        self.assertEqual(ui.pick_msix_asset(names, "Square44x44Logo.png", 64, light=True),
                         "Square44x44Logo.targetsize-48_altform-lightunplated.png")
        self.assertEqual(ui.pick_msix_asset(names[:2], "Square44x44Logo.png", 24), "Square44x44Logo.scale-200.png")
        self.assertIsNone(ui.pick_msix_asset([], "Square44x44Logo.png", 24))

    def test_package_folder_uses_manifest_logo(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as folder:
            os.makedirs(os.path.join(folder, "assets"))
            with open(os.path.join(folder, "AppxManifest.xml"), "w", encoding="utf-8") as f:
                f.write('<Package><uap:VisualElements Square44x44Logo="assets/Square44x44Logo.png"/></Package>')
            for name in ("Square44x44Logo.png", "Square44x44Logo.targetsize-32_altform-unplated.png"):
                open(os.path.join(folder, "assets", name), "wb").close()
            found = ui.icon_candidates(folder, 32)
            self.assertEqual(len(found), 1)
            self.assertTrue(found[0].endswith("Square44x44Logo.targetsize-32_altform-unplated.png"))

if __name__ == "__main__":
    unittest.main()