"""Memory upkeep (10-03): no per-2 s UI Automation FindAll, periodic trim
instead of restarts, opt-in diagnostics."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vram_radar import housekeeping as hk
from vram_radar import usage_surface as us


class Rect:
    def __init__(self, l, t, r, b):
        self.Left, self.Top, self.Right, self.Bottom = l, t, r, b
        self.Width, self.IsEmpty = r - l, r <= l


class Element:
    def __init__(self, rect):
        self.rect, self.gone = rect, False

    @property
    def Current(self):
        if self.gone:
            raise RuntimeError("ElementNotAvailable")
        return self

    @property
    def BoundingRectangle(self):
        return Rect(*self.rect)


class UiaCache(unittest.TestCase):
    def setUp(self):
        self.clock = [100.0]
        self.layout = us.TaskbarLayout(trim=None)
        self.searches = 0
        self.elements = {"StartButton": Element((491, 1528, 559, 1600)),
                         "WidgetsButton": Element((9, 1528, 237, 1600))}

        def find_all(handle):
            self.searches += 1
            self.layout._uia_cache = (int(handle), self.clock[0], list(self.elements.items()))
            return {k: (e.rect) for k, e in self.elements.items()}
        self.layout._find_all = find_all

    def read(self, advance=2.0):
        self.clock[0] += advance
        with mock.patch.object(us.time, "monotonic", lambda: self.clock[0]):
            return self.layout._read(1)

    def test_rects_are_reread_without_searching_again(self):
        self.read()
        self.elements["StartButton"].rect = (451, 1528, 519, 1600)   # centered icons shifted
        for _ in range(30):
            out = self.read()
        self.assertEqual(self.searches, 1)
        self.assertEqual(out["StartButton"][0], 451)

    def test_full_search_again_after_refind_interval(self):
        self.read()
        self.read(us.TaskbarLayout.UIA_REFIND + 1)
        self.assertEqual(self.searches, 2)

    def test_missing_widgets_button_searched_again_soon(self):
        del self.elements["WidgetsButton"]
        self.read()
        self.read(5)
        self.assertEqual(self.searches, 1)
        self.read(6)
        self.assertEqual(self.searches, 2)

    def test_vanished_element_or_new_handle_searches(self):
        self.read()
        self.elements["StartButton"].gone = True
        self.elements["StartButton"] = Element((491, 1528, 559, 1600))
        self.read()
        self.assertEqual(self.searches, 2)
        self.clock[0] += 1
        with mock.patch.object(us.time, "monotonic", lambda: self.clock[0]):
            self.layout._read(2)
        self.assertEqual(self.searches, 3)

    def test_invalidate_drops_cache(self):
        self.read()
        self.layout.invalidate()
        self.read()
        self.assertEqual(self.searches, 2)


class TrimSchedule(unittest.TestCase):
    def test_trim_after_startup_then_hourly_and_diagnostics_each_check(self):
        clock = [0.0]
        trim, diag = mock.Mock(return_value={"gc": 0}), mock.Mock()
        keeper = hk.Housekeeper(lambda: {}, clock=lambda: clock[0], trim=trim, diagnostics=diag,
                                first_maintenance=1e9)
        for _ in range(int(7200 / 60) + 1):
            keeper.step()
            clock[0] += 60
        self.assertEqual(trim.call_count, 2)          # ~3 min after start, then after an hour
        self.assertEqual(diag.call_count, 12)         # every 10 minutes, not every step

    def test_trim_memory_is_safe(self):
        out = hk.trim_memory(working_set=False)
        self.assertIn("gc", out)
        self.assertEqual(len(out["private_mb"]), 2)

    def test_trim_failure_never_raises(self):
        keeper = hk.Housekeeper(lambda: {}, clock=lambda: 1e6, trim=mock.Mock(side_effect=RuntimeError()),
                                first_trim=0)
        keeper.step()


class Diagnostics(unittest.TestCase):
    def test_off_without_flag_and_on_with_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime, logs = Path(tmp) / "runtime", Path(tmp) / "logs"
            runtime.mkdir()
            logs.mkdir()
            self.assertFalse(hk.write_diagnostics(runtime, logs))
            self.assertFalse((logs / hk.DIAGNOSTICS_FILE).exists())
            (runtime / hk.DIAGNOSTICS_FLAG).write_text("", encoding="utf-8")
            self.assertTrue(hk.write_diagnostics(runtime, logs))
            line = json.loads((logs / hk.DIAGNOSTICS_FILE).read_text(encoding="utf-8").splitlines()[-1])
            self.assertIn("py_objects", line)
            self.assertLessEqual(len(line["py_types"]), 15)

    def test_trace_only_when_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(hk.start_tracing_if_requested(Path(tmp)))
            self.assertIsNone(hk.diagnostics_requested(Path(tmp)))


class QuotaColorCache(unittest.TestCase):
    def test_cache_is_bounded_small(self):
        from vram_radar import quota_colors
        self.assertLessEqual(quota_colors._color.cache_info().maxsize, 512)


if __name__ == "__main__":
    unittest.main()
