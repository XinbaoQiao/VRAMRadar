import threading
import unittest

from vram_radar.usage_surface import CATCHER_OPACITY, TaskbarLayout, sample_taskbar_color

BAR = (0, 1528, 2560, 1600)
REAL = {"WidgetsButton": (9, 1528, 237, 1600), "StartButton": (491, 1528, 559, 1600),
        "SearchButton": (562, 1540, 891, 1588), "TaskViewButton": (895, 1528, 961, 1600)}


def solid(rect, colour=(40, 50, 60)):
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    r, g, b = colour
    return bytes((b, g, r, 255)) * (width * height)


class SampleColourTests(unittest.TestCase):
    def test_reads_bar_colour_from_few_screen_grabs(self):
        grabs = []
        def capture(rect):
            grabs.append(rect)
            return solid(rect)
        self.assertEqual(sample_taskbar_color(BAR, capture=capture), (40, 50, 60))
        self.assertLessEqual(len(grabs), 2)  # one per sampled row, not one per pixel

    def test_near_widget_is_one_grab_and_skips_own_rect(self):
        grabs = []
        def capture(rect):
            grabs.append(rect)
            return solid(rect, (200, 210, 220))
        near = (100, 1528, 150, 1600)
        self.assertEqual(sample_taskbar_color(BAR, None, near, capture=capture), (200, 210, 220))
        self.assertEqual(len(grabs), 1)
        self.assertLess(grabs[0][2] - grabs[0][0], 80)
        # Our own strip covering every sample point -> no reading.
        self.assertIsNone(sample_taskbar_color(BAR, BAR, near, capture=capture))

    def test_failed_grab_returns_none(self):
        self.assertIsNone(sample_taskbar_color(BAR, capture=lambda rect: None))


class BackgroundLayoutTests(unittest.TestCase):
    def make(self, reads):
        layout = TaskbarLayout(interval=0.0, trim=None, background=True)
        layout._ready = True
        def read(_handle):
            reads.append(threading.current_thread().name)
            return dict(REAL)
        layout._read = read
        return layout

    def test_first_read_is_synchronous_then_refreshes_off_thread(self):
        reads = []
        layout = self.make(reads)
        self.assertEqual(layout.elements(1, BAR), REAL)
        self.assertEqual(reads, [threading.current_thread().name])
        self.assertEqual(layout.elements(1, BAR), REAL)  # cached, worker started
        layout._worker.join(2)
        self.assertEqual(reads[-1], "taskbar-layout")

    def test_new_bar_is_read_synchronously(self):
        reads = []
        layout = self.make(reads)
        layout.elements(1, BAR)
        layout.elements(1, (0, 0, 2560, 72))
        self.assertEqual(len(reads), 2)
        self.assertNotIn("taskbar-layout", reads)


class CatcherTests(unittest.TestCase):
    def test_catcher_alpha_is_one_not_zero(self):
        self.assertEqual(int(CATCHER_OPACITY * 255), 1)


if __name__ == "__main__":
    unittest.main()