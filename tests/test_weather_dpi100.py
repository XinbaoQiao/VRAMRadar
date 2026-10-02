"""100 % DPI (48 px bar, 12 px strip margin): a 1-2 px wider weather text
leaves the strip in place (PlacementDebouncer tolerance) with a 10-11 px gap.
That must not read as "text runs under the strip" -- a 0.2 x height slack
(10 px) did, and the strip jumped one bar height right and back."""
import unittest
from unittest import mock

from vram_radar import usage_surface as us

BAR, TRAY = (0, 1032, 1920, 1080), (1500, 1032, 1920, 1080)
RAW = {"WidgetsButton": (6, 1032, 160, 1080), "StartButton": (700, 1032, 748, 1080),
       "SearchButton": (750, 1036, 980, 1076)}
SIZE, MARGIN = (90, 34), us.strip_margin(0.85)


class Screen:
    def __init__(self, text_end):
        self.text_end, self.own = text_end, None

    def capture(self, rect):
        l, t, r, b = rect
        h = b - t
        data = bytearray()
        for y in range(h):
            for x in range(l, r):
                own = self.own
                if own and own[0] <= x < own[2] and own[1] <= t + y < own[3]:
                    c = (30, 30, 30) if x - own[0] >= 8 and x % 3 == 0 and 12 <= y < 36 else (32, 32, 32)
                elif 14 <= x < 40 and 12 <= y < 36:
                    c = (230, 180, 60)
                elif 50 <= x < self.text_end and 14 <= y < 34 and (x % 3 == 0 or x == self.text_end - 1):
                    c = (235, 235, 235)
                else:
                    c = (32, 32, 32)
                data += bytes((c[2], c[1], c[0], 255))
        return bytes(data)


class Dpi100(unittest.TestCase):
    def run_strip(self, screen, ticks, clock, layout, placer, own):
        positions = []
        for _ in range(ticks):
            clock[0] += 1.0
            with mock.patch.object(us.time, "monotonic", lambda: clock[0]):
                elements = layout.elements(1, BAR, own[0])
            x, y = us.docked_point(BAR, placer.propose(us.docked_target(BAR, TRAY, elements, SIZE, MARGIN, 4)), SIZE[0])
            own[0] = screen.own = (x, y, x + SIZE[0], y + SIZE[1])
            positions.append(x)
        return positions

    def test_small_growth_does_not_jump(self):
        self.assertEqual(MARGIN, 12)
        screen, clock, own = Screen(110), [0.0], [None]
        layout = us.TaskbarLayout(interval=0.0, retry=0.0, trim=lambda f, o: us.trim_widgets(f, o, screen.capture),
                                  locked=lambda: False)
        layout._load = lambda: True
        layout._read = lambda handle: dict(RAW)
        placer = us.PlacementDebouncer(confirm=3, switch_confirm=5)
        self.run_strip(screen, 6, clock, layout, placer, own)
        settled = own[0][0]
        self.assertEqual(settled, 110 + MARGIN)
        screen.text_end = 112                         # '26°C' -> '27°C': 2 px wider
        positions = self.run_strip(screen, 30, clock, layout, placer, own)
        self.assertLessEqual(max(positions), settled + 2, positions)
        self.assertGreaterEqual(own[0][0] - screen.text_end, MARGIN - 2)

    def test_real_growth_under_strip_still_steps(self):
        screen, clock, own = Screen(110), [0.0], [None]
        layout = us.TaskbarLayout(interval=0.0, retry=0.0, trim=lambda f, o: us.trim_widgets(f, o, screen.capture),
                                  locked=lambda: False)
        layout._load = lambda: True
        layout._read = lambda handle: dict(RAW)
        placer = us.PlacementDebouncer(confirm=3, switch_confirm=5)
        self.run_strip(screen, 6, clock, layout, placer, own)
        screen.text_end = 170                         # far under the strip
        self.run_strip(screen, 12, clock, layout, placer, own)
        self.assertEqual(own[0][0] - screen.text_end, MARGIN)


if __name__ == "__main__":
    unittest.main()
