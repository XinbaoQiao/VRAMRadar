import unittest

from vram_radar.usage_surface import (PlacementDebouncer, TaskbarLayout, docked_point, docked_target,
                                      exclude_rect, settle_color, usable_layout)

BAR = (0, 1032, 1920, 1080)
TRAY = (1700, 1032, 1920, 1080)
GOOD = {"WidgetsButton": (0, 1032, 150, 1080), "StartButton": (700, 1032, 748, 1080)}


class FakeLayout(TaskbarLayout):
    def __init__(self, readings):
        super().__init__(interval=0)
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
        left, moved, tray = ("left", 156, 1039), ("left", 170, 1039), ("tray", 1700, 1039)
        placer.propose(left)
        self.assertEqual([placer.propose(tray) for _ in range(5)], [left] * 4 + [tray])
        placer.propose(left)
        self.assertEqual([placer.propose(left) for _ in range(5)][-1], left)
        self.assertEqual([placer.propose(moved) for _ in range(3)], [left, left, moved])

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
