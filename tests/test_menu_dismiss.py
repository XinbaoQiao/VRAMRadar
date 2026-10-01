"""Fallback outside-click dismissal of the strip menu (no real input)."""
import unittest
from types import SimpleNamespace as NS

from vram_radar.menu_dismiss import BUTTONS, OutsideClickWatch, menu_rects, outside_press

UP = (False,) * len(BUTTONS)
LEFT = (True,) + (False,) * (len(BUTTONS) - 1)
RIGHT = (False, True) + (False,) * (len(BUTTONS) - 2)


class FakeEvent(list):
    def __iadd__(self, handler):
        self.append(handler)
        return self


class FakeTimer:
    def __init__(self):
        self.Tick, self.Interval, self.running, self.disposed = FakeEvent(), 0, False, False
    def Start(self): self.running = True
    def Stop(self): self.running = False
    def Dispose(self): self.disposed = True


MENU, SUBMENU, STRIP = (100, 100, 300, 400), (300, 150, 500, 300), (10, 1000, 200, 1040)


class Harness:
    def __init__(self, held=UP):
        self.buttons, self.cursor, self.closed, self.open = held, (600, 600), 0, True
        self.timer = FakeTimer()
        self.watch = OutsideClickWatch(self.timer, lambda: self.buttons, lambda: self.cursor,
                                       lambda: [MENU, SUBMENU, STRIP], self.close, lambda: self.open)
    def close(self):
        self.closed += 1
        self.open = False
    def press(self, at, button=LEFT):
        self.cursor, self.buttons = at, button
        self.watch.tick()
        self.buttons = UP
        self.watch.tick()


class OutsideClick(unittest.TestCase):
    def test_pure_rule(self):
        self.assertTrue(outside_press(UP, LEFT, (0, 0), [MENU]))
        self.assertFalse(outside_press(LEFT, LEFT, (0, 0), [MENU]))      # held, not a new press
        self.assertFalse(outside_press(UP, LEFT, (150, 150), [MENU]))    # inside
        self.assertFalse(outside_press(UP, UP, (0, 0), [MENU]))
        self.assertFalse(outside_press(UP, LEFT, None, [MENU]))          # cursor unknown

    def test_outside_press_closes_and_stops_polling(self):
        h = Harness(); h.watch.start()
        self.assertTrue(h.timer.running)
        h.press((700, 700))
        self.assertEqual(h.closed, 1)
        self.assertFalse(h.timer.running)

    def test_inside_presses_keep_menu_open(self):
        h = Harness(); h.watch.start()
        for at in ((150, 150), (350, 200), (50, 1020)):                  # menu, submenu, strip
            h.press(at)
        h.press((150, 150), RIGHT)
        self.assertEqual(h.closed, 0)
        self.assertTrue(h.timer.running)

    def test_button_held_from_opening_click_is_ignored(self):
        h = Harness(held=RIGHT); h.watch.start()
        h.cursor = (700, 700); h.watch.tick()                           # still held: no new press
        self.assertEqual(h.closed, 0)
        h.buttons = UP; h.watch.tick()
        h.press((700, 700), RIGHT)
        self.assertEqual(h.closed, 1)

    def test_timer_removed_on_close_exception_and_dispose(self):
        h = Harness(); h.watch.start(); h.watch.stop()                  # menu.Closed
        self.assertFalse(h.timer.running)
        h.press((700, 700))
        self.assertEqual(h.closed, 0)                                    # stopped watch never acts
        h = Harness(); h.watch.start()
        h.watch._buttons = lambda: 1 / 0
        h.watch.tick()
        self.assertFalse(h.timer.running)
        h = Harness(); h.watch.start(); h.open = False; h.watch.tick()  # menu gone without Closed
        self.assertFalse(h.timer.running)
        h = Harness(); h.watch.start(); h.watch.dispose()
        self.assertTrue(h.timer.disposed and not h.timer.running)

    def test_menu_rects_walk_open_submenus_only(self):
        rect = lambda l, t, r, b: NS(Visible=True, Bounds=NS(Left=l, Top=t, Right=r, Bottom=b))
        sub = rect(*SUBMENU); sub.Items = NS(Count=0)
        closed = rect(0, 0, 1, 1); closed.Visible = False; closed.Items = NS(Count=0)
        items = [NS(DropDown=sub, HasDropDownItems=True), NS(DropDown=closed, HasDropDownItems=True), NS()]
        menu = rect(*MENU)
        menu.Items = type("I", (), {"Count": len(items), "__getitem__": lambda s, i: items[i]})()
        hidden = rect(0, 0, 5, 5); hidden.Visible = False
        self.assertEqual(menu_rects(menu, rect(*STRIP), None, hidden), [MENU, SUBMENU, STRIP])


if __name__ == "__main__":
    unittest.main()