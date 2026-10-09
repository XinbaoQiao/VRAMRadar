import unittest

from vram_radar.usage_surface import (PlacementDebouncer, TaskbarLayout, docked_point, docked_target,
                                      exclude_rect, refresh_left_anchor, settle_color, usable_layout)

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

    def test_narrow_left_gap_keeps_left_anchor_instead_of_switching_sides(self):
        # Reading from a 150 % taskbar where the Widgets edge reached x=422 (a second
        # VRAM Radar strip beside the weather was read as weather content).  Placement
        # caps that news-width edge (4 x bar height from the Widgets left) so the
        # strip stays on the left; left_slot's "fits" answer is not the rule.
        from vram_radar.usage_surface import left_slot, weather_place_right
        bar = (0, 1528, 2560, 1600)
        elements = {"WidgetsButton": (9, 1528, 422, 1600), "StartButton": (509, 1528, 577, 1600),
                    "SearchButton": (580, 1540, 909, 1588), "TaskViewButton": (913, 1528, 979, 1600)}
        capped = weather_place_right(elements["WidgetsButton"], bar) + 18
        target = docked_target(bar, (2200, 1528, 2560, 1600), elements, (69, 51), 18)
        self.assertEqual(target[0], "left")
        self.assertEqual(docked_point(bar, target, 69), (capped, 1538))
        self.assertLess(capped, 422)   # news width must not set the left edge
        # Capped edge leaves enough room for a 69 px strip; docked_target still
        # answers "left" (the historical left_slot-None case was the uncapped gap).
        self.assertEqual(left_slot(bar, elements, (69, 51), 18), (capped, 1538))

    def test_empty_uia_defaults_to_tray_width_independent(self):
        # Empty UIA cannot tell centered from left-aligned; tray is the portable
        # default.  Callers hold a committed left anchor across brief empties.
        a = docked_target(BAR, TRAY, {}, (200, 34))
        b = docked_target(BAR, TRAY, {}, (260, 34))
        self.assertEqual(a[0], "tray")
        self.assertEqual(a, b)
        self.assertEqual(docked_point(BAR, a, 200)[0], 1700 - 200 - 1)
        self.assertEqual(docked_point(BAR, b, 260)[0], 1700 - 260 - 1)

    def test_left_aligned_without_widgets_uses_tray(self):
        # Win10-style left icons, no Widgets button: no left gap -> tray.
        elements = {"StartButton": (8, 1032, 56, 1080), "first_app": (60, 1032, 108, 1080)}
        target = docked_target(BAR, TRAY, elements, (200, 34), 6)
        self.assertEqual(target[0], "tray")

    def test_widgets_left_without_start_stays_left(self):
        elements = {"WidgetsButton": (0, 1032, 150, 1080)}
        target = docked_target(BAR, TRAY, elements, (200, 34), 6)
        self.assertEqual(target[0], "left")
        self.assertEqual(target[1], 156)

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


class LeftAnchorStabilityTests(unittest.TestCase):
    """Quota strip left edge stays put across weather length and model width."""

    def test_news_width_weather_does_not_move_left_edge_past_cap(self):
        from vram_radar.usage_surface import weather_place_right
        bar = (0, 1528, 2560, 1600)
        tray = (2200, 1528, 2560, 1600)
        temp = {"WidgetsButton": (9, 1528, 150, 1600), "StartButton": (623, 1528, 691, 1600)}
        news = {"WidgetsButton": (9, 1528, 480, 1600), "StartButton": (623, 1528, 691, 1600)}
        left_temp = docked_target(bar, tray, temp, (200, 51), 18)[1]
        left_news = docked_target(bar, tray, news, (200, 51), 18)[1]
        self.assertEqual(left_temp, 150 + 18)
        self.assertEqual(left_news, weather_place_right(news["WidgetsButton"], bar) + 18)
        self.assertLess(left_news, 480)          # capped below raw news ink
        self.assertLessEqual(left_news - left_temp, 4 * 72)  # within place span

    def test_model_count_width_keeps_left_edge(self):
        target = docked_target(BAR, TRAY, GOOD, (200, 34))
        self.assertEqual(target[0], "left")
        xs = [docked_point(BAR, target, w)[0] for w in (120, 200, 360, 480)]
        self.assertEqual(xs, [target[1]] * 4)

    def test_left_aligned_widgets_still_use_tray(self):
        elements = {"WidgetsButton": (1400, 1032, 1600, 1080), "StartButton": (700, 1032, 748, 1080)}
        target = docked_target(BAR, TRAY, elements, (200, 34), 6, 4)
        self.assertEqual(target[0], "tray")
        self.assertEqual(docked_point(BAR, target, 200)[0], 1400 - 4 - 200 - 1)

    def test_refresh_left_anchor_recenters_y_on_new_bar(self):
        # Taskbar moved to another monitor: keep left slot, snap into new bar.
        committed = ("left", 156, 1039)
        new_bar = (1920, 1400, 3840, 1448)
        held = refresh_left_anchor(new_bar, committed, (200, 34), 12)
        self.assertEqual(held[0], "left")
        self.assertEqual(held[1], 1920 + 12)   # old X not on this bar -> bar+margin
        self.assertEqual(held[2], 1400 + (48 - 34) // 2)

    def test_refresh_left_anchor_clamps_within_same_bar(self):
        committed = ("left", 156, 1039)
        held = refresh_left_anchor(BAR, committed, (200, 34), 6)
        self.assertEqual(held, ("left", 156, 1032 + (48 - 34) // 2))

    def test_dpi_matrix_left_edge_is_relative(self):
        from vram_radar.usage_surface import strip_margin, taskbar_scale
        for dpi, bar_h in ((96, 48), (120, 60), (144, 72), (192, 96)):
            m = strip_margin(taskbar_scale(dpi, None))
            bar = (0, 1000, 1920, 1000 + bar_h)
            tray = (1600, 1000, 1920, 1000 + bar_h)
            weather_end = 10 + round(bar_h * 2.5)
            elements = {"WidgetsButton": (10, 1000, weather_end, 1000 + bar_h),
                        "StartButton": (700, 1000, 700 + bar_h, 1000 + bar_h)}
            target = docked_target(bar, tray, elements, (180, max(20, bar_h - 14)), m)
            self.assertEqual(target[0], "left")
            self.assertEqual(target[1], weather_end + m)
            self.assertLess(target[1], (bar[0] + bar[2]) / 2)

    def test_committed_left_survives_empty_via_refresh(self):
        # Simulates position(): empty UIA proposes tray, but refresh keeps left.
        committed = ("left", 156, 1039)
        proposed = docked_target(BAR, TRAY, {}, (200, 34))
        self.assertEqual(proposed[0], "tray")
        held = refresh_left_anchor(BAR, committed, (200, 34), 6)
        self.assertEqual(held[0], "left")
        self.assertEqual(docked_point(BAR, held, 200)[0], held[1])


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
