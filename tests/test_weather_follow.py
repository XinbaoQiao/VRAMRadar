"""Weather measurement while the strip docks flush left of the Start button."""
import unittest
from unittest import mock

from vram_radar import usage_surface as us

BAR, TRAY = (0, 1528, 2560, 1600), (1905, 1528, 2560, 1600)
# Explorer's rect stays at 237 px even when the text is drawn past it (10-02).
RAW = {"WidgetsButton": (9, 1528, 237, 1600), "StartButton": (491, 1528, 559, 1600),
       "SearchButton": (562, 1540, 891, 1588), "TaskViewButton": (895, 1528, 961, 1600)}
SIZE, MARGIN = (200, 51), 18


class Screen:
    """Taskbar pixels: weather ink from x=20 to ``text_end`` (screen px)."""
    def __init__(self, text_end):
        self.text_end = text_end
        self.photo = False      # lock-screen photo instead of the taskbar

    def capture(self, rect):
        l, t, r, b = rect
        w, h = r - l, b - t
        data = bytearray()
        for y in range(h):
            for x in range(l, r):
                if self.photo:
                    data += bytes(((x * 7 + y * 13) % 256, (x * 3) % 256, (y * 5) % 256, 255))
                    continue
                ink = 20 <= x < self.text_end and int(h * 0.3) <= y <= int(h * 0.7) and (x % 3 == 0 or x == self.text_end - 1)
                data += bytes((20, 20, 20, 255) if ink else (210, 220, 230, 255))
        return bytes(data)


class Running:
    def __init__(self, text_end, raw=RAW):
        self.screen, self.clock, self.raw = Screen(text_end), [0.0], dict(raw)
        self.layout = us.TaskbarLayout(interval=0.0, retry=0.0,
                                       trim=lambda found, own: us.trim_widgets(found, own, self.screen.capture),
                                       locked=lambda: self.is_locked)
        self.is_locked = False
        self.layout._load = lambda: True
        self.layout._read = lambda handle: dict(self.raw)
        self.placer = us.PlacementDebouncer(confirm=3, switch_confirm=5)
        self.own = None

    def tick(self, seconds=1.0):
        self.clock[0] += seconds
        with mock.patch.object(us.time, "monotonic", lambda: self.clock[0]):
            elements = self.layout.elements(1, BAR, self.own)
        target = self.placer.propose(us.docked_target(BAR, TRAY, elements, SIZE, MARGIN, 6))
        x, y = us.docked_point(BAR, target, SIZE[0])
        self.own = (x, y, x + SIZE[0], y + SIZE[1])
        return self.own

    def anchor(self):
        # Exclusive right-edge bound; docked_point left = anchor - width - 1.
        return RAW["StartButton"][0] - MARGIN

    def gap(self):
        return self.own[0] - self.screen.text_end

    def run(self, ticks):
        moves, last = 0, self.own
        for _ in range(ticks):
            if self.tick() != last:
                moves, last = moves + 1, self.own
            self.assertion()
        return moves

    def assertion(self):
        # Flush left of blue Start: strip right edge stays left of Start.left.
        assert self.own[2] <= RAW["StartButton"][0], self.own


class WeatherFollow(unittest.TestCase):
    def test_settles_flush_left_of_start(self):
        r = Running(150)
        r.run(6)
        self.assertEqual(r.own[0], r.anchor() - SIZE[0] - 1)

    def test_weather_growth_does_not_move_start_left_strip(self):
        r = Running(150)
        r.run(6)
        r.screen.text_end = 270           # wider than the 237 px rect
        moves = r.run(30)
        self.assertEqual((moves, r.own[0]), (0, r.anchor() - SIZE[0] - 1))
        r.screen.text_end = 245
        self.assertEqual(r.run(8), 0)

    def test_small_growth_leaves_strip_put(self):
        r = Running(150)
        r.run(6)
        r.screen.text_end = 160
        for second in range(1, 6):
            r.tick()
        self.assertEqual(r.own[0], r.anchor() - SIZE[0] - 1)

    def test_shrink_leaves_strip_put(self):
        r = Running(200)
        r.run(6)
        r.screen.text_end = 150
        self.assertEqual((r.run(35), r.own[0]), (0, r.anchor() - SIZE[0] - 1))

    def test_steady_text_never_moves(self):
        r = Running(150)
        r.run(6)
        self.assertEqual(r.run(60), 0)

    def test_lock_screen_photo_never_moves_strip(self):
        r = Running(150)
        r.run(6)
        before = r.own
        r.screen.photo, r.is_locked = True, True       # locked: capture shows the photo
        self.assertEqual(r.run(40), 0)
        self.assertEqual(r.own, before)
        r.screen.photo, r.is_locked = False, False
        r.screen.text_end = 170                        # weather changed meanwhile
        r.run(5)
        self.assertEqual(r.own[0], r.anchor() - SIZE[0] - 1)

    def test_implausible_capture_is_ignored(self):
        # Overlay/glitch not detected as lock: placement still stays flush left of Start.
        r = Running(150)
        r.run(6)
        before = r.own
        r.screen.photo = True
        self.assertEqual(r.run(40), 0)
        self.assertEqual(r.own, before)

    def test_occluded_steps_never_pass_start(self):
        layout = us.TaskbarLayout(trim=None)
        found = dict(RAW, WidgetsButton=(9, 1528, 480, 1600), _weather_scan=(480, True, 491))
        out = layout.widest_weather(found, RAW["WidgetsButton"], (480, 1538, 680, 1589), now=0.0)
        self.assertLessEqual(out["WidgetsButton"][2], 491)

    def test_real_bar_border_and_icon_gap(self):
        # Live 10-02 bar: 2 dark border rows on top, icon 20..48, a 15 px
        # blank run, then the text to 125.  Must read as plausible and end at
        # the text, not the icon.
        h, l, r = 72, 9, 255
        data = bytearray()
        for y in range(h):
            for x in range(l, r):
                if y < 2:
                    c = (190, 183, 171)
                elif 20 <= x < 48 and 18 <= y < 54:
                    c = (33, 153, 238)
                elif 63 <= x < 125 and 12 <= y < 58 and x % 3 == 0:
                    c = (60, 60, 60)
                else:
                    c = (239, 228, 207)
                data += bytes((*c, 255))
        raw = dict(RAW, WidgetsButton=(9, 1528, 237, 1600))
        out = us.trim_widgets(raw, (255, 1538, 455, 1590), capture=lambda rect: bytes(data))
        self.assertEqual(out["WidgetsButton"][2], 124)
        self.assertEqual(out["_weather_scan"][1], False)

    def test_screen_locked_is_safe(self):
        self.assertIn(us.screen_locked(), (True, False))

    def test_scan_limit_runs_to_start_not_uia_rect(self):
        self.assertEqual(us.weather_scan_limit(RAW, None), 491)
        self.assertEqual(us.weather_scan_limit(RAW, (255, 1538, 455, 1589)), 255)
        self.assertEqual(us.weather_scan_limit(RAW, (600, 1538, 800, 1589)), 491)


if __name__ == "__main__":
    unittest.main()