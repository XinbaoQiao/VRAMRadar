"""Strip follows the weather text while running (grow / shrink / stale UIA rect)."""
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
        assert self.own[2] <= RAW["StartButton"][0], self.own          # never over Start


class WeatherFollow(unittest.TestCase):
    def test_settles_18px_right_of_text(self):
        r = Running(150)
        r.run(6)
        self.assertEqual(r.gap(), MARGIN)

    def test_grows_past_stale_uia_rect(self):
        r = Running(150)
        r.run(6)
        r.screen.text_end = 270           # wider than the 237 px rect, under the strip
        overlapped = 0
        for second in range(1, 31):
            r.tick()
            if r.gap() < MARGIN:
                overlapped = second
        self.assertEqual(r.gap(), MARGIN)
        self.assertLessEqual(overlapped, 10)   # clear again within a few seconds
        r.screen.text_end = 245          # small growth that stays visible: one prompt move
        moves = r.run(8)
        self.assertEqual((r.gap(), moves), (MARGIN + 25, 0))   # wider reading recently seen: no move back yet

    def test_small_growth_moves_promptly(self):
        r = Running(150)
        r.run(6)
        r.screen.text_end = 160           # still visible left of the strip
        for second in range(1, 6):
            r.tick()
        self.assertEqual(r.gap(), MARGIN)

    def test_shrink_follows_after_hold_without_flicker(self):
        r = Running(200)
        r.run(6)
        r.screen.text_end = 150
        moves = r.run(10)
        self.assertEqual((moves, r.gap()), (0, MARGIN + 50))     # held back briefly
        moves = r.run(25)
        self.assertEqual((moves, r.gap()), (1, MARGIN))         # then exactly one move

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
        self.assertEqual(r.gap(), MARGIN)

    def test_implausible_capture_is_ignored(self):
        # Overlay/glitch not detected as lock: "content" running to Start is no
        # weather text -> keep the previous edge, never step onto Start/Search.
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

    def test_screen_locked_is_safe(self):
        self.assertIn(us.screen_locked(), (True, False))

    def test_scan_limit_runs_to_start_not_uia_rect(self):
        self.assertEqual(us.weather_scan_limit(RAW, None), 491)
        self.assertEqual(us.weather_scan_limit(RAW, (255, 1538, 455, 1589)), 255)
        self.assertEqual(us.weather_scan_limit(RAW, (600, 1538, 800, 1589)), 491)


if __name__ == "__main__":
    unittest.main()