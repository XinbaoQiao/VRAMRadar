from pathlib import Path
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch

from vram_radar import tray
from vram_radar.tray import WindowsTrayController, restore_window
from vram_radar.window_state import WindowGeometry, WindowStateController


class FakeEvent:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        self.handlers.remove(handler)
        return self


class FakeIcon:
    def __init__(self) -> None:
        self.detached = False
        self.stopped = False
        self.menu_updates = 0
        self.notifications = []
        self.notification_event = threading.Event()

    def run_detached(self) -> None:
        self.detached = True

    def stop(self) -> None:
        self.stopped = True

    def update_menu(self) -> None:
        self.menu_updates += 1

    def notify(self, message, title=None) -> None:
        self.notifications.append((title, message))
        self.notification_event.set()


class FakeMenuItem:
    def __init__(self, text, action, **options) -> None:
        self._text = text
        self.action = action
        self.options = options

    @property
    def text(self):
        return self._text(self) if callable(self._text) else self._text

    @property
    def enabled(self):
        value = self.options.get("enabled", True)
        return value(self) if callable(value) else value

    @property
    def default(self):
        value = self.options.get("default", False)
        return value(self) if callable(value) else value

    def activate(self, icon) -> None:
        if self.action is not None:
            self.action(icon, self)
        # pystray wraps activated menu actions and refreshes dynamic labels.
        icon.update_menu()


class FakeMenu:
    SEPARATOR = object()

    def __init__(self, *items) -> None:
        self.items = items


class FakePystrayIcon(FakeIcon):
    def __init__(self, name, image, title, menu) -> None:
        super().__init__()
        self.name = name
        self.image = image
        self.title = title
        self.menu = menu


class TrayControllerTests(unittest.TestCase):
    def setUp(self):
        # Production sends this at most once per process. Reset the process flag
        # so each test can independently prove its first-hide behavior.
        tray._hidden_notification_sent = False

    def test_close_hides_but_native_minimize_remains_in_the_taskbar(self):
        hidden = threading.Event()
        window = Mock()
        window.native = None
        window.gui = None
        window.events = SimpleNamespace(closing=FakeEvent(), minimized=FakeEvent())
        window.hide.side_effect = hidden.set
        icon = FakeIcon()
        callbacks = {}

        def factory(_path, show_window, exit_application):
            callbacks["show"] = show_window
            callbacks["exit"] = exit_application
            return icon

        controller = WindowsTrayController(window, Path("app-icon.png"), icon_factory=factory)
        controller.start()

        self.assertTrue(icon.detached)
        self.assertFalse(window.events.closing.handlers[0]())
        self.assertTrue(hidden.wait(1))
        self.assertTrue(icon.notification_event.wait(1))
        window.hide.reset_mock()
        window.events.minimized.handlers[0]()
        window.hide.assert_not_called()

        callbacks["show"]()
        self.assertEqual([call[0] for call in window.method_calls[-2:]], ["restore", "show"])
        callbacks["exit"]()
        self.assertTrue(icon.stopped)
        window.destroy.assert_called_once_with()
        self.assertTrue(window.events.closing.handlers[0]())

        controller.stop()
        self.assertEqual(window.events.closing.handlers, [])
        self.assertEqual(window.events.minimized.handlers, [])

    def test_quick_action_menu_has_dynamic_status_pause_and_callbacks(self):
        paused = {"value": False}
        status = {"value": "刚刚更新 · 5 台服务器"}
        calls = []
        source = Mock()
        source.__enter__ = Mock(return_value=source)
        source.__exit__ = Mock(return_value=False)
        source.convert.return_value = "rgba-image"
        image_module = SimpleNamespace(open=Mock(return_value=source))
        pystray_module = SimpleNamespace(
            Menu=FakeMenu,
            MenuItem=FakeMenuItem,
            Icon=FakePystrayIcon,
        )

        def import_module(name):
            return pystray_module if name == "pystray" else image_module

        with patch("vram_radar.tray.importlib.import_module", side_effect=import_module):
            icon = tray.create_windows_tray_icon(
                Path("app-icon.png"),
                lambda: calls.append("show"),
                lambda: calls.append("exit"),
                refresh_application=lambda: calls.append("refresh"),
                toggle_pause=lambda: paused.update(value=not paused["value"]),
                is_paused=lambda: paused["value"],
                status_text=lambda: status["value"],
            )

        items = icon.menu.items
        self.assertEqual(len(items), 7)
        self.assertEqual(items[0].text, "刚刚更新 · 5 台服务器")
        self.assertFalse(items[0].enabled)
        self.assertEqual(items[1].text, "显示 VRAM Radar")
        self.assertTrue(items[1].default)
        self.assertEqual(items[2].text, "立即刷新")
        self.assertEqual(items[3].text, "打开设置")
        self.assertFalse(items[3].enabled)
        self.assertEqual(items[4].text, "暂停自动监控")
        self.assertIs(items[5], FakeMenu.SEPARATOR)
        self.assertEqual(items[6].text, "退出")

        items[1].activate(icon)
        items[2].activate(icon)
        items[4].activate(icon)
        self.assertEqual(calls, ["show", "refresh"])
        self.assertTrue(paused["value"])
        self.assertEqual(items[4].text, "继续自动监控")
        self.assertEqual(icon.menu_updates, 3)
        items[6].activate(icon)
        self.assertEqual(calls[-1], "exit")

    def test_controller_exposes_quick_actions_notifications_and_menu_refresh(self):
        paused = {"value": False}
        refresh = Mock()
        open_settings = Mock()
        captured = {}
        window = Mock()
        window.events = SimpleNamespace(closing=FakeEvent(), minimized=FakeEvent())
        icon = FakeIcon()

        def factory(_path, show_window, exit_application, **quick_actions):
            captured.update(quick_actions)
            captured["show"] = show_window
            captured["exit"] = exit_application
            return icon

        controller = WindowsTrayController(
            window,
            Path("app-icon.png"),
            icon_factory=factory,
            refresh_application=refresh,
            open_settings=open_settings,
            toggle_pause=lambda: paused.update(value=not paused["value"]),
            is_paused=lambda: paused["value"],
            status_text=lambda: "监控已暂停" if paused["value"] else "3 台服务器在线",
        )
        controller.start()

        captured["refresh_application"]()
        refresh.assert_called_once_with()
        captured["open_settings"]()
        open_settings.assert_called_once_with()
        captured["toggle_pause"]()
        self.assertTrue(captured["is_paused"]())
        self.assertEqual(captured["status_text"](), "监控已暂停")
        self.assertTrue(controller.refresh_menu())
        self.assertEqual(icon.menu_updates, 1)
        self.assertTrue(controller.notify("资源可用", "H100 已空闲"))
        self.assertEqual(icon.notifications[-1], ("资源可用", "H100 已空闲"))

    def test_repeated_close_hides_notify_only_once_for_the_process(self):
        window = Mock()
        hidden = threading.Event()
        window.hide.side_effect = hidden.set
        window.events = SimpleNamespace(closing=FakeEvent(), minimized=FakeEvent())
        icon = FakeIcon()
        controller = WindowsTrayController(
            window,
            Path("app-icon.png"),
            icon_factory=lambda *_args: icon,
        )
        controller.start()

        self.assertFalse(window.events.closing.handlers[0]())
        self.assertTrue(hidden.wait(1))
        hidden.clear()
        self.assertFalse(window.events.closing.handlers[0]())
        self.assertTrue(hidden.wait(1))

        self.assertEqual(window.hide.call_count, 2)
        self.assertEqual(len(icon.notifications), 1)
        title, message = icon.notifications[0]
        self.assertEqual(title, "VRAM Radar 仍在运行")
        self.assertIn("通知区域", message)

    def test_exit_close_behavior_can_change_at_runtime(self):
        behavior = {"value": "hide"}
        hidden = threading.Event()
        window = Mock()
        window.events = SimpleNamespace(closing=FakeEvent(), minimized=FakeEvent())
        window.hide.side_effect = hidden.set
        icon = FakeIcon()
        controller = WindowsTrayController(
            window,
            Path("app-icon.png"),
            icon_factory=lambda *_args: icon,
            close_behavior=lambda: behavior["value"],
        )
        controller.start()

        self.assertFalse(window.events.closing.handlers[0]())
        self.assertTrue(hidden.wait(1))
        behavior["value"] = "exit"
        self.assertTrue(window.events.closing.handlers[0]())
        self.assertTrue(controller.exit_requested)
        self.assertTrue(icon.stopped)
        window.destroy.assert_not_called()

    def test_lifecycle_coordinator_defers_native_destroy_until_worker_is_stopped(self):
        before_exit = Mock()
        window = Mock()
        window.events = SimpleNamespace(closing=FakeEvent(), minimized=FakeEvent())
        icon = FakeIcon()
        controller = WindowsTrayController(
            window,
            Path("app-icon.png"),
            icon_factory=lambda *_args: icon,
            close_behavior="exit",
            before_exit=before_exit,
        )
        controller.start()

        self.assertFalse(window.events.closing.handlers[0]())
        before_exit.assert_called_once_with()
        window.destroy.assert_not_called()
        self.assertTrue(controller.exit_requested)
        self.assertTrue(window.events.closing.handlers[0]())

    def test_tray_exit_delegates_native_destroy_to_lifecycle_coordinator(self):
        before_exit = Mock()
        window = Mock()
        window.events = SimpleNamespace(closing=FakeEvent(), minimized=FakeEvent())
        icon = FakeIcon()
        callbacks = {}

        def factory(_path, _show_window, exit_application):
            callbacks["exit"] = exit_application
            return icon

        controller = WindowsTrayController(
            window,
            Path("app-icon.png"),
            icon_factory=factory,
            before_exit=before_exit,
        )
        controller.start()

        callbacks["exit"]()

        before_exit.assert_called_once_with()
        window.destroy.assert_not_called()
        self.assertTrue(icon.stopped)

    def test_tray_show_uses_the_guarded_restore_callback(self):
        restore_application = Mock()
        window = Mock()
        window.events = SimpleNamespace(closing=FakeEvent(), minimized=FakeEvent())
        callbacks = {}

        def factory(_path, show_window, _exit_application):
            callbacks["show"] = show_window
            return FakeIcon()

        controller = WindowsTrayController(
            window,
            Path("app-icon.png"),
            icon_factory=factory,
            restore_application=restore_application,
        )
        controller.start()

        callbacks["show"]()

        restore_application.assert_called_once_with()
        window.restore.assert_not_called()
        window.show.assert_not_called()

    def test_tray_hide_uses_the_guarded_window_operation_callback(self):
        hidden = threading.Event()
        hide_application = Mock(side_effect=lambda: (hidden.set() or True))
        window = Mock()
        window.events = SimpleNamespace(closing=FakeEvent(), minimized=FakeEvent())
        controller = WindowsTrayController(
            window,
            Path("app-icon.png"),
            icon_factory=lambda *_args: FakeIcon(),
            hide_application=hide_application,
        )
        controller.start()

        self.assertFalse(window.events.closing.handlers[0]())
        self.assertTrue(hidden.wait(1))

        hide_application.assert_called_once_with()
        window.hide.assert_not_called()

    def test_failed_icon_start_does_not_intercept_window_lifecycle(self):
        window = Mock()
        window.events = SimpleNamespace(closing=FakeEvent(), minimized=FakeEvent())
        icon = FakeIcon()
        icon.run_detached = Mock(side_effect=RuntimeError("tray unavailable"))
        controller = WindowsTrayController(
            window,
            Path("app-icon.png"),
            icon_factory=lambda *_args: icon,
        )

        with self.assertRaisesRegex(RuntimeError, "tray unavailable"):
            controller.start()

        self.assertEqual(window.events.closing.handlers, [])
        self.assertEqual(window.events.minimized.handlers, [])

    def test_restore_recovers_a_window_that_the_backend_left_offscreen(self):
        window = Mock()
        geometry = WindowGeometry(1040, 720)
        native_order = []
        with patch(
            "vram_radar.tray.recover_offscreen_windows_window",
            side_effect=lambda *_args: (native_order.append("recover") or True),
        ) as recover, patch(
            "vram_radar.tray.refresh_windows_window_surface",
            side_effect=lambda *_args: (native_order.append("repaint") or True),
        ) as repaint:
            restore_window(window, geometry)

        self.assertEqual([call[0] for call in window.method_calls], ["restore", "show"])
        recover.assert_called_once_with(window, geometry)
        repaint.assert_called_once_with(window)
        self.assertEqual(native_order, ["recover", "repaint"])

    def test_restore_recursively_repaints_existing_webview_without_reloading_it(self):
        calls = []
        user32 = SimpleNamespace(
            RedrawWindow=lambda handle, rect, region, flags: calls.append(
                (handle, rect, region, flags)
            )
            or 1
        )
        with patch("vram_radar.tray.sys.platform", "win32"), patch(
            "vram_radar.tray._native_window_handle", return_value=321
        ), patch("vram_radar.tray.ctypes.windll", SimpleNamespace(user32=user32)):
            self.assertTrue(tray.refresh_windows_window_surface(Mock()))

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1:3], (None, None))
        self.assertEqual(calls[0][3], tray._REDRAW_WINDOW_AND_CHILDREN)

    def test_late_close_hide_cannot_override_a_newer_show_intent(self):
        window = Mock()
        window.events = SimpleNamespace(closing=FakeEvent(), minimized=FakeEvent())
        controller = WindowsTrayController(
            window,
            Path("app-icon.png"),
            icon_factory=lambda *_args: FakeIcon(),
        )
        controller.start()

        controller.show_window()
        controller._hide_window(show_generation=0)

        window.hide.assert_not_called()
        window.restore.assert_called_once_with()
        window.show.assert_called_once_with()

    def test_show_intent_is_reapplied_when_an_inflight_native_hide_finishes_late(self):
        hide_entered = threading.Event()
        release_hide = threading.Event()
        restore_application = Mock()

        def delayed_hide():
            hide_entered.set()
            release_hide.wait(1)
            return True

        window = Mock()
        window.events = SimpleNamespace(closing=FakeEvent(), minimized=FakeEvent())
        controller = WindowsTrayController(
            window,
            Path("app-icon.png"),
            icon_factory=lambda *_args: FakeIcon(),
            restore_application=restore_application,
            hide_application=delayed_hide,
        )
        controller.start()
        hide_thread = threading.Thread(target=controller._hide_window, args=(0,))
        hide_thread.start()
        self.assertTrue(hide_entered.wait(1))

        controller.show_window()
        release_hide.set()
        hide_thread.join(1)

        self.assertFalse(hide_thread.is_alive())
        self.assertEqual(restore_application.call_count, 2)

    def test_native_caption_icon_is_hidden_without_removing_taskbar_presence(self):
        form = SimpleNamespace(Handle=0, ShowIcon=True, ShowInTaskbar=False)
        window = SimpleNamespace(native=form, gui=None)

        with patch("vram_radar.tray.sys.platform", "win32"):
            self.assertTrue(tray.configure_windows_native_chrome(window))

        self.assertFalse(form.ShowIcon)
        self.assertTrue(form.ShowInTaskbar)

    def test_native_caption_customization_is_a_safe_noop_on_macos(self):
        with patch("vram_radar.tray.sys.platform", "darwin"):
            self.assertFalse(tray.configure_windows_native_chrome(Mock()))

    def test_windows_normal_state_check_rejects_minimized_and_maximized_forms(self):
        state = {"iconic": 0, "zoomed": 0}
        user32 = SimpleNamespace(
            IsIconic=lambda _handle: state["iconic"],
            IsZoomed=lambda _handle: state["zoomed"],
        )
        with patch("vram_radar.tray.sys.platform", "win32"), patch(
            "vram_radar.tray._native_window_handle", return_value=321
        ), patch("vram_radar.tray.ctypes.windll", SimpleNamespace(user32=user32)):
            self.assertTrue(tray.native_window_is_normal(Mock()))
            state["iconic"] = 1
            self.assertFalse(tray.native_window_is_normal(Mock()))
            state["iconic"] = 0
            state["zoomed"] = 1
            self.assertFalse(tray.native_window_is_normal(Mock()))

    def test_macos_normal_state_check_rejects_miniaturized_zoomed_and_fullscreen(self):
        state = {"miniaturized": False, "zoomed": False, "style_mask": 0}
        native = SimpleNamespace(
            isMiniaturized=lambda: state["miniaturized"],
            isZoomed=lambda: state["zoomed"],
            styleMask=lambda: state["style_mask"],
        )
        window = SimpleNamespace(native=native)

        with patch("vram_radar.tray.sys.platform", "darwin"):
            self.assertTrue(tray.native_window_is_normal(window))
            state["miniaturized"] = True
            self.assertFalse(tray.native_window_is_normal(window))
            state["miniaturized"] = False
            state["zoomed"] = True
            self.assertFalse(tray.native_window_is_normal(window))
            state["zoomed"] = False
            state["style_mask"] = 1 << 14
            self.assertFalse(tray.native_window_is_normal(window))

    def test_macos_event_probe_rejects_zoomed_resize_without_timer_appkit_access(self):
        state = {"zoomed": False}
        native = SimpleNamespace(
            isMiniaturized=lambda: False,
            isZoomed=lambda: state["zoomed"],
            styleMask=lambda: 0,
        )
        window = SimpleNamespace(native=native)
        saved = []
        completed = threading.Event()
        probe_threads = []

        def normal_state():
            probe_threads.append(threading.get_ident())
            return tray.native_window_is_normal(window)

        def save(geometry):
            saved.append(geometry)
            completed.set()

        controller = WindowStateController(
            SimpleNamespace(load=lambda: WindowGeometry(), save=save),
            debounce_seconds=0.02,
            normal_state=normal_state,
            recheck_normal_state_on_commit=False,
        )

        with patch("vram_radar.tray.sys.platform", "darwin"):
            event_thread = threading.get_ident()
            controller.on_resized(1000, 700)
            state["zoomed"] = True
            controller.on_resized(1920, 1040)
            self.assertTrue(completed.wait(1))
            # Simulate a late pywebview maximized callback after the debounce
            # timer has already committed the last normal working size.
            controller.on_maximized()

        self.assertEqual(saved, [WindowGeometry(1000, 700)])
        self.assertEqual(probe_threads, [event_thread, event_thread])
        controller.close()

    def test_restore_resolves_the_winforms_backend_handle(self):
        handle = SimpleNamespace(ToInt64=lambda: 321)
        form = SimpleNamespace(Handle=handle)
        window = SimpleNamespace(
            native=None,
            uid="main",
            gui=SimpleNamespace(BrowserView=SimpleNamespace(instances={"main": form})),
        )

        self.assertEqual(tray._native_window_handle(window), 321)

    def test_offscreen_recovery_uses_preferred_size_not_the_old_tiny_fallback(self):
        class FakeUser32:
            def __init__(self) -> None:
                self.rect = (-32000, -32000, -31520, -31680)
                self.placements = []

            def SystemParametersInfoW(self, _action, _parameter, pointer, _flags):
                pointer._obj.left = 0
                pointer._obj.top = 0
                pointer._obj.right = 1920
                pointer._obj.bottom = 1040
                return 1

            def GetWindowRect(self, _handle, pointer):
                (
                    pointer._obj.left,
                    pointer._obj.top,
                    pointer._obj.right,
                    pointer._obj.bottom,
                ) = self.rect
                return 1

            def MonitorFromRect(self, _pointer, _flags):
                left, top, right, bottom = self.rect
                return int(right > 0 and bottom > 0 and left < 1920 and top < 1040)

            def ShowWindow(self, _handle, _state):
                return 1

            def SetWindowPos(self, _handle, _after, x, y, width, height, _flags):
                self.placements.append((x, y, width, height))
                self.rect = (x, y, x + width, y + height)
                return 1

            def SetForegroundWindow(self, _handle):
                return 1

        user32 = FakeUser32()
        fake_windll = SimpleNamespace(user32=user32)
        with patch("vram_radar.tray.sys.platform", "win32"), patch(
            "vram_radar.tray._native_window_handle", return_value=321
        ), patch("vram_radar.tray.ctypes.windll", fake_windll), patch(
            "vram_radar.tray.time.sleep"
        ):
            recovered = tray.recover_offscreen_windows_window(
                Mock(),
                WindowGeometry(1060, 720),
            )

        self.assertTrue(recovered)
        self.assertTrue(user32.placements)
        self.assertEqual(user32.placements[0][2:], (1060, 720))


if __name__ == "__main__":
    unittest.main()
