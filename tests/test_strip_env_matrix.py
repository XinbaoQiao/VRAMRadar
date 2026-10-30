"""Placement matrix for other Windows taskbar setups (pure geometry)."""
import unittest

from vram_radar.usage_surface import docked_point, docked_target, strip_margin, taskbar_scale


def place(bar, elements, size, display=1.5, tray=None):
    tray = tray or (bar[2] - 300, bar[1], bar[2], bar[3])
    m = strip_margin(taskbar_scale(display * 96, None))
    target = docked_target(bar, tray, elements, size, m, 4)
    return target[0], docked_point(bar, target, size[0]), m


class EnvironmentMatrixTests(unittest.TestCase):
    def assertClear(self, x, w, left_end, right_start, m):
        self.assertGreaterEqual(x - left_end, m)
        self.assertGreaterEqual(right_start - (x + w), m)

    def test_centered_with_weather(self):
        bar = (0, 1528, 2560, 1600)
        slot, (x, _), m = place(bar, {"WidgetsButton": (9, 1528, 113, 1600), "StartButton": (623, 1528, 691, 1600)}, (219, 51))
        # Flush left of Start: left = Start.left - m - width - 1
        self.assertEqual((slot, x, m), ("start", 623 - 18 - 219 - 1, 18))
        self.assertLessEqual(x + 219, 623 - m)

    def test_weather_widget_off_docks_left_of_start(self):
        bar = (0, 1528, 2560, 1600)
        slot, (x, _), m = place(bar, {"StartButton": (623, 1528, 691, 1600)}, (219, 51))
        self.assertEqual((slot, x), ("start", 623 - 18 - 219 - 1))

    def test_left_aligned_taskbar_uses_tray_side(self):
        bar = (0, 1528, 2560, 1600)
        tray = (2200, 1528, 2560, 1600)
        elements = {"StartButton": (0, 1528, 68, 1600), "WidgetsButton": (2000, 1528, 2150, 1600)}
        slot, (x, _), m = place(bar, elements, (219, 51), tray=tray)
        self.assertEqual(slot, "tray")
        self.assertLessEqual(x + 219, 2000)          # left of the right-side weather

    def test_secondary_monitor_negative_coordinates(self):
        bar = (-1920, 1032, 0, 1080)                 # 100 %, monitor left of primary
        elements = {"WidgetsButton": (-1912, 1032, -1840, 1080), "StartButton": (-1180, 1032, -1132, 1080)}
        slot, (x, _), m = place(bar, elements, (155, 34), display=1.0)
        self.assertEqual((slot, m), ("start", 12))
        self.assertEqual(x, -1180 - m - 155 - 1)  # left of Start on this bar

    def test_small_taskbar_scales_strip_down(self):
        bar_h = 32                                   # small taskbar buttons, 100 %
        s = taskbar_scale(96, ((0, 1048, 1920, 1080), None))
        self.assertLessEqual(round(40 * s), bar_h)
        w = 150
        slot, (x, _), m = place((0, 1048, 1920, 1080), {"WidgetsButton": (8, 1048, 80, 1080),
                                                         "StartButton": (700, 1048, 732, 1080)}, (w, round(40 * s)), display=1.0)
        self.assertEqual(slot, "start")
        self.assertEqual(x, 700 - m - w - 1)


if __name__ == "__main__":
    unittest.main()