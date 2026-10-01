import unittest

from vram_radar.usage_surface import (FIT_PLAN, compact_value, content_right, docked_point, docked_target,
                                      fit_choice, left_gap, trim_widgets)

# The user's 150 % taskbar (physical px) as read by UI Automation on 10-01.
BAR = (0, 1528, 2560, 1600)
TRAY = (1905, 1528, 2560, 1600)
REAL = {"WidgetsButton": (9, 1528, 237, 1600), "StartButton": (491, 1528, 559, 1600),
        "SearchButton": (562, 1540, 891, 1588), "TaskViewButton": (895, 1528, 961, 1600)}


def capture_with_text(width, height, text_end, bg=(230, 220, 210), fg=(20, 20, 20)):
    data = bytearray()
    for y in range(height):
        for x in range(width):
            ink = x < text_end and int(height * 0.3) <= y <= int(height * 0.7) and x % 3 == 0
            r, g, b = fg if ink else bg
            data += bytes((b, g, r, 255))
    return bytes(data)


class StripFitTests(unittest.TestCase):
    def test_content_right_finds_end_of_weather_text(self):
        pixels = capture_with_text(228, 72, 139)
        self.assertIn(content_right(pixels, 228, 72), (137, 138, 139))
        self.assertEqual(content_right(capture_with_text(50, 72, 0), 50, 72), 0)
        self.assertIsNone(content_right(b"", 10, 72))

    def test_trim_widgets_uses_visible_content(self):
        cap = lambda rect: capture_with_text(rect[2] - rect[0], rect[3] - rect[1], 139)
        trimmed = trim_widgets(REAL, None, cap)
        self.assertLess(trimmed["WidgetsButton"][2], 150)
        gap = left_gap(BAR, trimmed, 9)
        self.assertGreaterEqual(gap[1] - gap[0], 320)  # was 236 with the raw UIA rect
        self.assertEqual(left_gap(BAR, REAL, 9), (246, 482))

    def test_trim_never_scans_under_own_strip(self):
        seen = []
        def cap(rect):
            seen.append(rect)
            return capture_with_text(rect[2] - rect[0], rect[3] - rect[1], 10_000)
        own = (160, 1534, 480, 1594)
        trimmed = trim_widgets(REAL, own, cap)
        self.assertEqual(seen[0][2], 160)
        self.assertLessEqual(trimmed["WidgetsButton"][2], 160)

    def test_trim_failure_keeps_reading(self):
        self.assertEqual(trim_widgets(REAL, None, lambda rect: None), REAL)

    def test_too_wide_stays_left_on_centered_taskbar(self):
        for width in (200, 238, 400, 900):
            target = docked_target(BAR, TRAY, REAL, (width, 60), 9, 6)
            self.assertEqual(target[0], "left")
            self.assertEqual(docked_point(BAR, target, width)[0], 246)

    def test_left_aligned_taskbar_uses_tray_side(self):
        left_aligned = {"StartButton": (0, 1528, 68, 1600), "WidgetsButton": (1700, 1528, 1900, 1600)}
        self.assertEqual(docked_target(BAR, TRAY, left_aligned, (300, 60), 9, 6)[0], "tray")

    def test_compact_value_levels(self):
        self.assertEqual(compact_value("0% 5.1h", 0), "0% 5.1h")
        self.assertEqual(compact_value("0% 5.1h", 1), "0%")
        self.assertEqual(compact_value("已用 15%", 1), "已用 15%")
        self.assertEqual(compact_value("已用 15%", 2), "15%")
        self.assertEqual(compact_value("¥6.00", 2), "¥6")
        self.assertEqual(compact_value("¥6.05", 2), "¥6.05")
        self.assertEqual(compact_value("可用", 2), "可用")

    def test_minimal_strip_labels(self):
        from decimal import Decimal as D
        from vram_radar.providers import kimi
        from vram_radar.providers.deepseek import balance_labels
        w = lambda kind, amount, cur="CNY": {"currency": cur, "kind": kind, "amount": D(amount)}
        head, brief = balance_labels([w("granted", "6.00"), w("topped_up", "0")], [])
        self.assertEqual((head["zh"], brief["zh"]), ("¥6", "¥6（赠送余额）"))
        head, brief = balance_labels([w("granted", "10.00"), w("topped_up", "56.00")], [])
        self.assertEqual((head["zh"], brief["zh"]), ("¥66", "¥66（充值 ¥56 + 赠送 ¥10）"))
        self.assertEqual(balance_labels([w("topped_up", "66.00", "USD")], [])[0]["zh"], "$66")
        self.assertEqual(balance_labels([w("granted", "0")], [])[0]["zh"], "无余额")
        now = 1_000_000
        ok = kimi._show_reading({"facts": []}, {"used_percent": 0, "is_member": False, "reset_at": now + 36.3 * 3600}, now)
        self.assertEqual(ok["headline"]["zh"], "36.3h")
        self.assertEqual(ok["brief"]["zh"].split(" · ")[0], "已用 0%")
        self.assertEqual(kimi._show_reading({"facts": []}, {"used_percent": 100}, now)["headline"]["zh"], "已用尽")
        self.assertEqual(kimi._show_reading({"facts": []}, {"is_member": False}, now)["headline"]["zh"], "可用")
        self.assertEqual(kimi._show_reading({"facts": []}, {"active": False, "is_member": False}, now)["headline"]["zh"], "无额度")

    def test_font_never_below_ninety_percent(self):
        self.assertEqual(FIT_PLAN[0], (1.0, 0))
        self.assertGreaterEqual(min(factor for factor, _ in FIT_PLAN), 0.9)

    def test_fit_choice(self):
        self.assertEqual(fit_choice([300, 280, 260], 325), 0)
        self.assertEqual(fit_choice([400, 330, 300], 325), 2)
        self.assertEqual(fit_choice([400, 390], 325), 1)
        self.assertEqual(fit_choice([400], None), 0)


if __name__ == "__main__":
    unittest.main()