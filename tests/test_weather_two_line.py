"""10-03 03:02 regression: 2-line news widget ('精选资讯' / '港股午评：恒指跌2.6…').

Line 1 is short and bold, line 2 longer, low-contrast grey and has a wide
blank after the full-width colon.  The strip sat right after line 1 with its
bottom row (DeepSeek icon, '¥6') over line 2: a visible end at the colon /
a glyph gap just left of the strip never counted as "runs under the
strip", and LockApp -- still the foreground window after an unlock until
another window is activated -- made the foreground-based lock check skip
every scan.
"""
import unittest
from unittest import mock

from vram_radar import usage_surface as us

BAR, TRAY = (0, 1528, 2560, 1600), (1905, 1528, 2560, 1600)
RAW = {"WidgetsButton": (9, 1528, 237, 1600), "StartButton": (491, 1528, 559, 1600),
       "SearchButton": (562, 1540, 891, 1588), "TaskViewButton": (895, 1528, 961, 1600)}
SIZE, MARGIN = (120, 51), 18      # live strip is ~110 px wide at 150 %
BG = (239, 228, 207)            # light taskbar (live 10-03 capture)
BOLD = (30, 30, 30)
GREY = (205, 196, 178)          # low contrast: sum |dRGB| = 95 at full ink, ~50 anti-aliased
FAINT = (222, 212, 192)         # anti-aliased grey stroke, sum |dRGB| = 48
ICON = (33, 153, 238)
# Columns measured from the live capture (physical px, 150 %, 72 px bar).
LINE1 = [(77, 149)]                                        # 精选资讯
LINE2_SHORT = [(77, 149), (152, 155), (167, 203), (204, 215)]   # 港股午评：恒指…
LINE2_LONG = [(77, 149), (152, 155), (167, 221), (224, 241), (243, 256)]   # 港股午评：恒指跌2.6…


def text_end(*lines):
    return max(b for line in lines for _, b in line)


class Screen:
    """Taskbar pixels incl. the strip itself (opaque bg, ink from +12 px)."""
    def __init__(self, line1=LINE1, line2=LINE2_SHORT):
        self.line1, self.line2, self.own = list(line1), list(line2), None

    def colour(self, x, y, h):
        own = self.own
        if own and own[0] <= x < own[2] and own[1] <= BAR[1] + y < own[3]:
            return BOLD if x - own[0] >= 12 and (x - own[0]) % 4 == 0 and 16 <= y < 56 else BG
        if 20 <= x < 62 and 18 <= y < 54:
            return ICON
        if int(h * 0.22) <= y < int(h * 0.45):
            if any(a <= x < b for a, b in self.line1) and (x % 3 == 0 or any(x == b - 1 for _, b in self.line1)):
                return BOLD
        if int(h * 0.55) <= y < int(h * 0.78):
            for a, b in self.line2:
                if a <= x < b:
                    if x == b - 1:
                        return FAINT          # glyph's last column: faint anti-aliasing
                    if x % 3 == 0:
                        return GREY
        return BG

    def capture(self, rect):
        l, t, r, b = rect
        h = b - t
        data = bytearray()
        for y in range(h):
            for x in range(l, r):
                c = self.colour(x, y, h)
                data += bytes((c[2], c[1], c[0], 255))
        return bytes(data)


class Running:
    def __init__(self, screen, session_flags=1):
        # LockApp left as the foreground window after an unlock (10-03).
        self.screen, self.clock, self.flags = screen, [0.0], session_flags
        self.layout = us.TaskbarLayout(
            interval=2.0, retry=0.0, trim=lambda found, own: us.trim_widgets(found, own, self.screen.capture),
            locked=lambda: us.lock_state(True, "LockApp.exe", True, self.flags))
        self.layout._load = lambda: True
        self.layout._read = lambda handle: dict(RAW)
        self.placer = us.PlacementDebouncer(confirm=3, switch_confirm=5)
        self.own = None

    def tick(self, seconds=1.0):
        self.clock[0] += seconds
        with mock.patch.object(us.time, "monotonic", lambda: self.clock[0]):
            elements = self.layout.elements(1, BAR, self.own)
        target = self.placer.propose(us.docked_target(BAR, TRAY, elements, SIZE, MARGIN, 6))
        x, y = us.docked_point(BAR, target, SIZE[0])
        self.own = (x, y, x + SIZE[0], y + SIZE[1])
        self.screen.own = self.own
        assert self.own[2] <= RAW["StartButton"][0], self.own      # never over Start
        return self.own

    def gap(self):
        return self.own[0] - text_end(self.screen.line1, self.screen.line2)


class TwoLineWidget(unittest.TestCase):
    def test_end_is_rightmost_ink_of_both_rows(self):
        out = us.trim_widgets(dict(RAW), None, capture=Screen().capture)
        self.assertEqual(out["WidgetsButton"][2], text_end(LINE2_SHORT))
        out = us.trim_widgets(dict(RAW), None, capture=Screen(line2=LINE2_LONG).capture)
        self.assertEqual(out["WidgetsButton"][2], text_end(LINE2_LONG))

    def test_colon_and_word_gaps_do_not_end_the_text(self):
        # A 26 px blank (0.36 x bar height) after '：' used to end the scan at 0.3 x.
        line2 = [(77, 149), (152, 155), (181, 230)]
        out = us.trim_widgets(dict(RAW), None, capture=Screen(line2=line2).capture)
        self.assertEqual(out["WidgetsButton"][2], 230)
        # ...but the blank-run rule still stops well before Start/Search.
        far = [(77, 149), (300, 320)]
        out = us.trim_widgets(dict(RAW), None, capture=Screen(line2=far).capture)
        self.assertEqual(out["WidgetsButton"][2], 149)

    def test_visible_end_at_colon_left_of_strip_counts_as_under_strip(self):
        # Strip right after line 1: only '：' of line 2 is visible before it.
        screen = Screen(line2=LINE2_LONG)
        own = (166, 1538, 366, 1589)
        screen.own = own
        out = us.trim_widgets(dict(RAW), own, capture=screen.capture)
        self.assertTrue(out["_weather_scan"][1])
        layout = us.TaskbarLayout(trim=None)
        settled = layout.widest_weather(out, RAW["WidgetsButton"], own, now=0.0)
        self.assertGreater(settled["WidgetsButton"][2], own[0])          # steps right of the strip

    def test_strip_left_of_line2_steps_clear_within_seconds(self):
        # Strip settled while the widget showed line 1 only, then line 2 appears.
        r = Running(Screen(line2=[]))
        for _ in range(6):
            r.tick()
        self.assertEqual(r.own[0], text_end(LINE1) + MARGIN)
        r.screen.line2 = list(LINE2_LONG)            # under the strip now
        clear_at = None
        for second in range(1, 31):
            r.tick()
            if clear_at is None and r.gap() >= MARGIN:
                clear_at = second
        self.assertEqual(r.gap(), MARGIN)
        self.assertIsNotNone(clear_at)
        self.assertLessEqual(clear_at, 8)

    def test_grows_from_short_to_long_second_line(self):
        r = Running(Screen(line2=LINE2_SHORT))
        for _ in range(6):
            r.tick()
        self.assertEqual(r.gap(), MARGIN)
        r.screen.line2 = list(LINE2_LONG)
        overlapped = 0
        for second in range(1, 21):
            r.tick()
            if r.gap() < MARGIN:
                overlapped = second
        self.assertEqual(r.gap(), MARGIN)
        self.assertLessEqual(overlapped, 8)

    def test_shrink_waits_20s(self):
        r = Running(Screen(line2=LINE2_LONG))
        for _ in range(6):
            r.tick()
        wide = r.own
        r.screen.line2 = list(LINE2_SHORT)
        for _ in range(12):
            r.tick()
        self.assertEqual(r.own, wide)                 # held back
        for _ in range(30):
            r.tick()
        self.assertEqual(r.gap(), MARGIN)

    def test_real_lock_screen_still_holds(self):
        r = Running(Screen(line2=LINE2_SHORT))
        for _ in range(6):
            r.tick()
        before = r.own
        r.flags = 0                                   # session really locked
        r.screen.line2 = list(LINE2_LONG)
        for _ in range(10):
            r.tick()
        self.assertEqual(r.own, before)

    def test_strip_moved_during_background_scan_is_not_measured(self):
        layout = us.TaskbarLayout(trim=lambda found, own: dict(found, WidgetsButton=(9, 1528, 400, 1600),
                                                               _weather_scan=(400, False, 491)),
                                  locked=lambda: False)
        layout._load = lambda: True
        layout._read = lambda handle: dict(RAW)
        layout._elements = dict(RAW, WidgetsButton=(9, 1528, 215, 1600))
        layout._own_now = (233, 1538, 433, 1589)        # strip moved after the worker started
        out = layout._refresh((1, BAR), 1, (300, 1538, 500, 1589))
        self.assertEqual(out["WidgetsButton"][2], 215)


class LockState(unittest.TestCase):
    def test_stale_lockapp_foreground_after_unlock_is_not_locked(self):
        self.assertFalse(us.lock_state(True, "LockApp.exe", True, 1))

    def test_session_flags_are_authoritative(self):
        self.assertTrue(us.lock_state(True, "explorer.exe", True, 0))
        self.assertFalse(us.lock_state(False, None, False, 1))

    def test_fallback_without_session_flags(self):
        self.assertTrue(us.lock_state(True, "LockApp.exe", True, None))
        self.assertTrue(us.lock_state(True, "LogonUI.exe", None))
        self.assertTrue(us.lock_state(False, None, False))       # secure desktop
        self.assertTrue(us.lock_state(False, None, None))        # unknown: as before
        self.assertFalse(us.lock_state(False, None, True))
        self.assertFalse(us.lock_state(True, "explorer.exe", True))

    def test_session_lock_flags_is_safe(self):
        self.assertIn(us.session_lock_flags(), (None, 0, 1))


if __name__ == "__main__":
    unittest.main()
