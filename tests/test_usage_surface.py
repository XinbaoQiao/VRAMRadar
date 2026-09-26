import unittest

from vram_radar.usage_surface import CodexUsageSurface, quota_lines, strip_bounds, widget_reading, taskbar_anchor


class QuotaSurfaceTests(unittest.TestCase):
    def test_usage_urgency_boundaries_and_unknown(self):
        from vram_radar.usage_surface import usage_color
        self.assertEqual(usage_color(None), (160, 168, 173))
        for bright in (False, True):
            for waiting, limit, step in ((False, 100, 0.1), (True, 259200, 60)):
                samples = [usage_color(i*step, bright=bright, waiting=waiting)
                           for i in range(int(limit/step)+1)]
                self.assertGreater(samples[0][0], samples[0][2])
                self.assertGreater(samples[-1][2], samples[-1][0])
                self.assertGreater(len(set(samples)), 100)
                self.assertTrue(all(max(abs(a-b) for a, b in zip(x, y)) <= 2
                                    for x, y in zip(samples, samples[1:])))
                self.assertTrue(all(max(color)-min(color) <= 115 for color in samples))
                self.assertEqual(usage_color(-1, bright=bright, waiting=waiting), samples[0])
                if waiting:
                    self.assertNotEqual(usage_color(limit*2, bright=bright, waiting=True), samples[-1])
                else:
                    self.assertEqual(usage_color(limit*2, bright=bright), samples[-1])
                    for anchor in (25, 50, 75):
                        self.assertNotEqual(usage_color(anchor-.5, bright=bright),
                                            usage_color(anchor+.5, bright=bright))
            self.assertEqual(usage_color(float('nan'), bright=bright), usage_color(None, bright=bright))
    def test_windows_fullscreen_and_hidden_taskbar_detection(self):
        import ctypes
        from unittest.mock import MagicMock, patch
        from vram_radar.usage_surface import windows_surface_obscured
        from types import SimpleNamespace
        api = MagicMock()
        api.FindWindowW.return_value = 1
        api.GetForegroundWindow.return_value = 2
        api.MonitorFromWindow.return_value = 10
        api.IsWindowVisible.return_value = True
        api.IsZoomed.return_value = False
        rectangles = {1: (0, 1032, 1920, 1080), 2: (0, 0, 1920, 1080)}
        def get_rect(handle, pointer):
            pointer._obj.left, pointer._obj.top, pointer._obj.right, pointer._obj.bottom = rectangles[handle]
            return True
        def monitor(_handle, pointer):
            rect = pointer._obj.monitor
            rect.left, rect.top, rect.right, rect.bottom = (0, 0, 1920, 1080)
            return True
        def owner(handle, pointer):
            pointer._obj.value = handle
        api.GetWindowRect.side_effect = get_rect
        api.GetMonitorInfoW.side_effect = monitor
        api.GetWindowThreadProcessId.side_effect = owner
        api.GetClassNameW.side_effect = lambda _h, text, _n: setattr(text, 'value', 'TestWindow')
        with patch.object(ctypes, 'windll', SimpleNamespace(user32=api), create=True):
            self.assertTrue(windows_surface_obscured(3))
            api.IsZoomed.return_value = True
            self.assertFalse(windows_surface_obscured(3))
            rectangles[1] = (0, 1078, 1920, 1126)
            self.assertTrue(windows_surface_obscured(3))
            self.assertFalse(windows_surface_obscured(3, docked=False))
            api.FindWindowW.return_value = 0
            self.assertTrue(windows_surface_obscured(3))

    def test_taskbar_anchor_tracks_tray_and_top_taskbars(self):
        self.assertEqual(taskbar_anchor((0, 1032, 1920, 1080), (1500, 1032, 1920, 1080), (86, 44)), (1413, 1034))
        self.assertEqual(taskbar_anchor((-1920, 0, 0, 72), (-400, 0, 0, 72), (133, 68)), (-534, 2))

    def test_reference_widget_disks_and_compact_countdown(self):
        state = {"enabled": True, "state": "ready", "windows": [
            {"remaining_percent": 68, "window_minutes": 300, "resets_at": 8200},
            {"remaining_percent": 42, "window_minutes": 10080, "resets_at": 173800}]}
        row = widget_reading(state, now=1000)
        self.assertEqual((row["value"], row["percent"], row["countdown"], row["time_percent"]),
                         ("68%", 68, "2.0h", 40))
        weekly = widget_reading(state, 1, now=1000)
        self.assertEqual((weekly["value"], weekly["countdown"]), ("42%", "48.0h"))
        self.assertEqual(widget_reading(state, 1, now=1000, time_format="hours")["countdown"], "48.0h")
        self.assertEqual(widget_reading(state, 1, now=1000, time_format="decimal")["countdown"], "48.0h")
        self.assertEqual(widget_reading(state, 1, language="en", now=1000)["countdown"], "48.0h")
        self.assertEqual(widget_reading(state, 1, language="en", now=1000, time_format="hours")["countdown"], "48.0h")
        self.assertEqual(widget_reading(state, language="en", now=8000)["countdown"], "<0.1h")
        self.assertAlmostEqual(weekly["time_percent"], 100*2/7)

    def test_reference_widget_never_fills_unknown_or_stale_disks(self):
        state = {"enabled": True, "state": "ready", "windows": [{"remaining_percent": None}]}
        row = widget_reading(state, now=1000)
        self.assertEqual((row["percent"], row["time_percent"], row["countdown"]), (None, None, "—"))
        state.update(stale=True, windows=[{"remaining_percent": 90, "window_minutes": 300, "resets_at": 4000}])
        row = widget_reading(state, language="en", now=1000)
        self.assertEqual((row["percent"], row["time_percent"], row["countdown"]), (None, None, "Stale"))

    def test_reference_widget_expired_and_subhour_windows(self):
        state = {"enabled": True, "state": "ready", "windows": [
            {"remaining_percent": 0, "window_minutes": 300, "resets_at": 1020}]}
        row = widget_reading(state, now=1000)
        self.assertEqual((row["percent"], row["countdown"]), (0, "<0.1h"))
        row = widget_reading(state, language="en", now=1020)
        self.assertEqual((row["percent"], row["time_percent"], row["countdown"]), (None, 0, "Wait"))

    def test_native_disable_saves_before_reopening_fresh_settings(self):
        calls = []
        surface = CodexUsageSurface(None, lambda: {}, language=lambda: "en",
                                    open_settings=lambda: calls.append("open"),
                                    disable=lambda: calls.append("disable"),
                                    refresh=lambda: None, quit_application=lambda: None)
        surface._disable()
        self.assertEqual(calls, ["disable", "open"])

    def test_direct_remaining_and_reset_countdown_for_actual_windows(self):
        rows = quota_lines({"enabled": True, "state": "ready", "windows": [
            {"name": "Codex", "window_minutes": 300, "remaining_percent": 68, "resets_at": 8200},
            {"name": "Codex", "window_minutes": 10080, "remaining_percent": 9, "resets_at": 173800},
        ]}, now=1000)
        self.assertEqual([row["label"] for row in rows], ["5h", "7d"])
        self.assertEqual([row["value"] for row in rows], ["68%", "9%"])
        self.assertEqual([row["countdown"] for row in rows], ["2.0h", "48.0h"])
        self.assertTrue(rows[1]["low"])

    def test_disabled_has_no_surface_even_with_cached_windows(self):
        self.assertEqual(quota_lines({"enabled": False, "windows": [{}]}), [])

    def test_expired_stale_error_and_unknown_never_show_percentage(self):
        for changes in [{"stale": True}, {"state": "error"}, {}]:
            state = {"enabled": True, "state": "ready", "windows": [
                {"remaining_percent": 90, "resets_at": 1}, {"remaining_percent": None}
            ], **changes}
            rows = quota_lines(state, "en", now=1000)
            self.assertEqual([r["value"] for r in rows], ["—", "—"])
            self.assertEqual(rows[0]["countdown"], "Updating…")
            self.assertEqual(rows[1]["countdown"], "Reset unknown")

    def test_loading_retains_only_unexpired_quota(self):
        rows = quota_lines({"enabled": True, "state": "loading", "windows": [
            {"remaining_percent": 0, "resets_at": 2000}
        ]}, now=1000)
        self.assertEqual(rows[0]["value"], "0%")

    def test_account_errors_are_readable_without_raw_details(self):
        state = {"enabled": True, "state": "error", "code": "login_required", "error": "private"}
        self.assertEqual(quota_lines(state, "en")[0]["countdown"], "Sign in to Codex")
        self.assertEqual(quota_lines(state)[0]["countdown"], "请登录 Codex")
        self.assertNotIn("private", str(quota_lines(state)))

    def test_taskbar_anchor_and_display_change_clamping(self):
        self.assertEqual(strip_bounds((0, 0, 1920, 1040), (236, 48)), (1676, 984))
        self.assertEqual(strip_bounds((-1920, 0, 0, 1040), (236, 48)), (-244, 984))
        self.assertEqual(strip_bounds((0, 0, 800, 600), (236, 48), (1700, 1000)), (564, 552))
        self.assertEqual(strip_bounds((40, 0, 1920, 1080), (236, 48), (-10, -10)), (40, 0))
