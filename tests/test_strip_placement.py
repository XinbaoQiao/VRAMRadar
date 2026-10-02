import unittest

from vram_radar.usage_surface import (PlacementDebouncer, TaskbarLayout, docked_point, docked_target,
                                      exclude_rect, settle_color, usable_layout)

BAR = (0, 1032, 1920, 1080)
TRAY = (1700, 1032, 1920, 1080)
GOOD = {"WidgetsButton": (0, 1032, 150, 1080), "StartButton": (700, 1032, 748, 1080)}


class FakeLayout(TaskbarLayout):
    def __init__(self, readings):
        super().__init__(interval=0, trim=None)
        self.readings = list(readings)

    def _load(self):
        return True

    def _read(self, handle):
        return self.readings.pop(0)


class StripPlacementTests(unittest.TestCase):
    def test_left_slot_target_and_point(self):
        target = docked_target(BAR, TRAY, GOOD, (200, 34))
        self.assertEqual(target[0], "left")
        self.assertEqual(docked_point(BAR, target, 200), (156, 1039))

    def test_tray_fallback_anchor_is_width_independent(self):
        a = docked_target(BAR, TRAY, {}, (200, 34))
        b = docked_target(BAR, TRAY, {}, (260, 34))
        self.assertEqual(a, b)
        self.assertEqual(docked_point(BAR, a, 200)[0], 1700 - 200 - 1)

    def test_transient_lookup_failure_keeps_last_good(self):
        layout = FakeLayout([GOOD, {}, {"WidgetsButton": (0, 1032, 150, 1080)}, GOOD])
        self.assertEqual(layout.elements(1, BAR), GOOD)
        self.assertEqual(layout.elements(1, BAR), GOOD)
        self.assertEqual(layout.elements(1, BAR), GOOD)
        self.assertEqual(layout.failures, 2)
        self.assertEqual(layout.elements(1, BAR), GOOD)
        self.assertEqual(layout.failures, 0)

    def test_explorer_restart_same_bar_keeps_last_good(self):
        layout = FakeLayout([GOOD, {}, {"WidgetsButton": (0, 1032, 150, 1080)}])
        layout.elements(1, BAR)
        self.assertEqual(layout.elements(2, BAR), GOOD)
        self.assertEqual(layout.elements(2, BAR), GOOD)

    def test_new_bar_rect_drops_stale_reading(self):
        layout = FakeLayout([GOOD, {}])
        layout.elements(1, BAR)
        self.assertEqual(layout.elements(2, (0, 1400, 2560, 1440)), {})

    def test_failed_read_retries_sooner(self):
        layout = FakeLayout([{}, GOOD])
        layout.interval, layout.retry = 3600, 0
        self.assertEqual(layout.elements(1, BAR), {})
        self.assertEqual(layout.elements(1, BAR), GOOD)
        layout.retry = 3600
        self.assertEqual(layout.elements(1, BAR), GOOD)  # cached, no further read

    def test_own_window_excluded_from_layout(self):
        own = (156, 1032, 356, 1080)
        reading = dict(GOOD, first_app=(160, 1036, 200, 1076))
        self.assertEqual(exclude_rect(reading, own), GOOD)
        self.assertEqual(exclude_rect(GOOD, None), GOOD)
        layout = FakeLayout([{"StartButton": (170, 1036, 210, 1076)}, GOOD])
        self.assertEqual(layout.elements(1, BAR, own), {})

    def test_debouncer_needs_consistent_readings(self):
        placer = PlacementDebouncer(confirm=3, switch_confirm=3)
        left, tray = ("left", 156, 1039), ("tray", 1700, 1039)
        self.assertEqual(placer.propose(left), left)
        for sequence in ([tray, left], [tray, tray, left]):
            for target in sequence:
                self.assertEqual(placer.propose(target), left)
        self.assertEqual(placer.propose(tray), left)
        self.assertEqual(placer.propose(tray), left)
        self.assertEqual(placer.propose(tray), tray)
        placer.reset()
        self.assertEqual(placer.propose(left), left)

    def test_alternating_readings_never_move(self):
        placer = PlacementDebouncer(confirm=3)
        a, b = ("left", 156, 1039), ("tray", 1700, 1039)
        placer.propose(a)
        moves = sum(placer.propose(t) != a for t in [b, a] * 50)
        self.assertEqual(moves, 0)

    def test_side_switch_needs_more_readings_than_move(self):
        placer = PlacementDebouncer(confirm=3, switch_confirm=5)
        left, moved, tray = ("left", 156, 1039), ("left", 142, 1039), ("tray", 1700, 1039)
        placer.propose(left)
        self.assertEqual([placer.propose(tray) for _ in range(5)], [left] * 4 + [tray])
        placer.propose(left)
        self.assertEqual([placer.propose(left) for _ in range(5)][-1], left)
        self.assertEqual([placer.propose(moved) for _ in range(3)], [left, left, moved])   # toward the weather: confirmed
        away = ("left", 190, 1039)
        self.assertEqual(placer.propose(away), away)    # away from a growing weather text: at once

    def test_small_jitter_is_ignored(self):
        placer = PlacementDebouncer(confirm=1, switch_confirm=1, tolerance=2)
        a = ("left", 156, 1039)
        placer.propose(a)
        for t in [("left", 157, 1039), ("left", 154, 1040), ("left", 158, 1039)]:
            self.assertEqual(placer.propose(t), a)
        self.assertEqual(placer.propose(("left", 160, 1039)), ("left", 160, 1039))

    def test_usable_layout(self):
        self.assertTrue(usable_layout(GOOD))
        self.assertFalse(usable_layout({}))
        self.assertFalse(usable_layout({"WidgetsButton": (0, 0, 1, 1)}))

    def test_settle_color_ignores_noise(self):
        self.assertEqual(settle_color(None, (30, 30, 30)), (30, 30, 30))
        self.assertEqual(settle_color((30, 30, 30), (36, 28, 33)), (30, 30, 30))
        self.assertEqual(settle_color((30, 30, 30), (200, 200, 200)), (200, 200, 200))
        self.assertEqual(settle_color((30, 30, 30), None), (30, 30, 30))


if __name__ == "__main__":
    unittest.main()


class WidestWeatherTests(unittest.TestCase):
    RAW = (9, 1528, 237, 1600)

    def run_reading(self, layout, right, own, now, scan=None):
        found = {"WidgetsButton": (9, 1528, right, 1600), "StartButton": (623, 1528, 691, 1600)}
        if scan:
            found["_weather_scan"] = scan
        out = layout.widest_weather(found, self.RAW, own, now=now)
        self.assertNotIn("_weather_scan", out)
        return out["WidgetsButton"][2]

    def test_keeps_widest_recent_and_expires(self):
        layout = TaskbarLayout(trim=None)
        own = (131, 1538, 350, 1589)
        self.assertEqual(self.run_reading(layout, 150, own=None, now=0), 150)    # "局部多云"
        self.assertEqual(self.run_reading(layout, 150, own=None, now=5), 150)    # confirmed -> remembered
        self.assertEqual(self.run_reading(layout, 113, own=own, now=10), 150)    # narrower: keep widest
        self.assertEqual(self.run_reading(layout, 113, own=own, now=30), 113)    # narrower held > WEATHER_WINDOW

    def test_content_under_strip_is_assumed_full_button_once(self):
        layout = TaskbarLayout(trim=None)
        own = (121, 1538, 342, 1589)
        self.assertEqual(self.run_reading(layout, 121, own=own, now=0, scan=(121, True)), 237)     # cannot see past the strip
        self.assertEqual(self.run_reading(layout, 150, own=(255, 1538, 476, 1589), now=5), 150)  # real width
        self.assertEqual(self.run_reading(layout, 150, own=(168, 1538, 389, 1589), now=10), 150)
        self.assertEqual(layout._weather_seen, [(10, 150)])

    def test_single_or_near_strip_readings_are_not_remembered(self):
        layout = TaskbarLayout(trim=None)
        self.assertEqual(self.run_reading(layout, 120, own=None, now=0), 120)                    # once: used, not kept
        self.assertEqual(self.run_reading(layout, 127, own=(135, 1538, 368, 1589), now=5), 127)  # 8 px from the strip
        self.assertEqual(self.run_reading(layout, 127, own=(135, 1538, 368, 1589), now=10), 127)
        self.assertEqual(layout._weather_seen, [])
        self.assertEqual(self.run_reading(layout, 113, own=(145, 1538, 378, 1589), now=15), 113)


class StripMarginTests(unittest.TestCase):
    def test_margin_follows_real_display_scale(self):
        from vram_radar.usage_surface import strip_margin, taskbar_scale
        self.assertEqual(strip_margin(taskbar_scale(144, None)), 18)   # 150 %
        self.assertEqual(strip_margin(taskbar_scale(96, None)), 12)    # 100 %


class ContentClusterTests(unittest.TestCase):
    def test_own_strip_pixels_after_blank_run_are_ignored(self):
        from vram_radar.usage_surface import content_right
        w, h = 120, 20
        buf = bytearray(b"\xf0\xf0\xf0\xff" * w * h)
        def ink(x0, x1):
            for x in range(x0, x1):
                for y in range(6, 14):
                    i = (y * w + x) * 4
                    buf[i:i + 3] = b"\x20\x20\x20"
        ink(5, 20); ink(26, 50)      # weather icon + text (6 px internal gap)
        ink(80, 110)                 # our strip, 30 px further right
        self.assertEqual(content_right(bytes(buf), w, h), 110)
        self.assertEqual(content_right(bytes(buf), w, h, max_gap=14), 50)
