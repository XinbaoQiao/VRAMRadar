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
        with patch('vram_radar.usage_surface._dll', lambda _name: api):
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

    def test_reference_widget_text_and_compact_countdown(self):
        state = {"enabled": True, "state": "ready", "windows": [
            {"remaining_percent": 68, "window_minutes": 300, "resets_at": 8200},
            {"remaining_percent": 42, "window_minutes": 10080, "resets_at": 173800}]}
        row = widget_reading(state, now=1000)
        self.assertEqual((row["value"], row["percent"], row["countdown"]), ("68%", 68, "2.0h"))
        self.assertNotIn("time_percent", row)
        weekly = widget_reading(state, 1, now=1000)
        self.assertEqual((weekly["value"], weekly["countdown"]), ("42%", "48.0h"))
        self.assertEqual(widget_reading(state, 1, now=1000, time_format="hours")["countdown"], "48.0h")
        self.assertEqual(widget_reading(state, 1, now=1000, time_format="decimal")["countdown"], "48.0h")
        self.assertEqual(widget_reading(state, 1, language="en", now=1000)["countdown"], "48.0h")
        self.assertEqual(widget_reading(state, 1, language="en", now=1000, time_format="hours")["countdown"], "48.0h")
        self.assertEqual(widget_reading(state, language="en", now=8000)["countdown"], "<0.1h")

    def test_reference_widget_unknown_or_stale_reads_as_status(self):
        state = {"enabled": True, "state": "ready", "windows": [{"remaining_percent": None}]}
        row = widget_reading(state, now=1000)
        self.assertEqual((row["percent"], row["countdown"]), (None, "—"))
        state.update(stale=True, windows=[{"remaining_percent": 90, "window_minutes": 300, "resets_at": 4000}])
        row = widget_reading(state, language="en", now=1000)
        self.assertEqual((row["percent"], row["countdown"]), (None, "Stale"))

    def test_reference_widget_expired_and_subhour_windows(self):
        state = {"enabled": True, "state": "ready", "windows": [
            {"remaining_percent": 0, "window_minutes": 300, "resets_at": 1020}]}
        row = widget_reading(state, now=1000)
        self.assertEqual((row["percent"], row["countdown"]), (0, "<0.1h"))
        row = widget_reading(state, language="en", now=1020)
        self.assertEqual((row["percent"], row["countdown"]), (None, "Wait"))

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


class SessionConsentSurfaceTests(unittest.TestCase):
    def surface(self, *, consented=(), answer=False, states=None):
        from unittest.mock import Mock
        self.asked, self.saved, self.notices = [], Mock(return_value={"ok": True}), []
        def confirm(app, provider):
            self.asked.append((app, provider))
            return answer
        surface = CodexUsageSurface(None, lambda: {}, language=lambda: "zh-CN", open_settings=lambda: None,
                                    disable=lambda: None, refresh=lambda: None, quit_application=lambda: None,
                                    providers=lambda: {"selected": ["codex", "grok"],
                                                       "session_consent": list(consented),
                                                       "providers": dict(states or {})},
                                    save_consent=self.saved, confirm_consent=confirm)
        surface.notify_blocked = self.notices.append
        surface._action = lambda callback: callback()  # run synchronously
        return surface

    def test_not_installed_explains_instead_of_asking(self):
        surface = self.surface(answer=True, states={"kimi": {"installed": False, "signed_in": None}})
        self.assertFalse(surface.request_session_consent("kimi"))
        self.assertEqual(self.asked, [])
        self.assertIn("未检测到 Kimi", self.notices[0])
        self.saved.assert_not_called()

    def test_signed_out_explains_instead_of_asking(self):
        surface = self.surface(answer=True, states={"grok": {"installed": True, "signed_in": False}})
        self.assertFalse(surface.request_session_consent("grok"))
        self.assertEqual(self.asked, [])
        self.assertIn("尚未登录", self.notices[0])

    def test_installed_and_signed_in_or_unknown_asks(self):
        surface = self.surface(states={"grok": {"installed": True, "signed_in": True}})
        surface.request_session_consent("grok")
        surface.request_session_consent("kimi")   # state unknown (fresh start): still asks
        self.assertEqual([a for a, _ in self.asked], ["Grok", "Kimi"])
        self.assertEqual(self.notices, [])

    def test_dialog_layout_scales_and_fits_text(self):
        from vram_radar.usage_surface import dialog_layout
        normal, big = dialog_layout(1.0, 60), dialog_layout(1.5, 90)
        self.assertEqual(big["client"][0], 660)
        self.assertGreaterEqual(big["label"][3], 90)
        self.assertGreater(big["button_y"], big["label"][1] + big["label"][3])
        self.assertLessEqual(big["button_y"] + big["button"][1], big["client"][1])
        self.assertEqual(normal["font_px"], 12)
        self.assertEqual(big["font_px"], 18)

    def test_fresh_profile_has_no_consent(self):
        from vram_radar.models import Profile
        self.assertEqual(Profile.empty("new").usage_session_consent, ())
        raw = Profile.empty("new").to_dict()
        raw.pop("usage_session_consent", None)
        self.assertEqual(Profile.from_dict(raw).usage_session_consent, ())

    def test_dialog_text(self):
        from vram_radar.usage_surface import consent_text
        self.assertEqual(consent_text("Grok", "xAI"),
                         "显存雷达将使用 Grok 在本机保存的登录状态，向 xAI 服务器发送只读的额度查询。"
                         "登录凭证只在内存中使用，不会保存、记录或上传。每 5 分钟最多查询一次，可随时在菜单中撤销。")

    def test_cancel_keeps_local_only_and_saves_nothing(self):
        surface = self.surface(answer=False)
        self.assertFalse(surface.request_session_consent("grok"))
        self.assertEqual(self.asked, [("Grok", "xAI")])
        self.saved.assert_not_called()

    def test_confirm_persists_consent(self):
        surface = self.surface(answer=True)
        self.assertTrue(surface.request_session_consent("kimi"))
        self.saved.assert_called_once_with("kimi", True)
        self.assertFalse(surface._display_error)

    def test_not_asked_for_incapable_or_already_consented(self):
        surface = self.surface(consented=["grok"], answer=True)
        self.assertFalse(surface.request_session_consent("codex"))
        self.assertFalse(surface.request_session_consent("grok"))
        self.assertEqual(self.asked, [])
        self.saved.assert_not_called()

    def test_menu_click_revokes_consented_provider(self):
        surface = self.surface(consented=["grok"])
        surface._consent_clicked("grok")
        self.saved.assert_called_once_with("grok", False)
        self.assertEqual(self.asked, [])

    def test_dialog_failure_counts_as_cancel(self):
        surface = self.surface()
        surface.confirm_consent = lambda app, provider: 1 / 0
        self.assertFalse(surface.request_session_consent("grok"))
        self.saved.assert_not_called()
