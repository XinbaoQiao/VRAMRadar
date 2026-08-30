from __future__ import annotations

import ctypes
from ctypes import wintypes
import importlib
import logging
from pathlib import Path
import sys
import threading
import time
from typing import Any, Callable

from .window_state import (
    MIN_WINDOW_HEIGHT,
    MIN_WINDOW_WIDTH,
    WindowGeometry,
)


TrayIconFactory = Callable[..., Any]
StateCallback = Callable[[], bool]
TextCallback = Callable[[], str]


_REDRAW_WINDOW_AND_CHILDREN = 0x0001 | 0x0004 | 0x0080 | 0x0100
# RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW


_hidden_notification_lock = threading.Lock()
_hidden_notification_sent = False


class _WindowRect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def _native_window_handle(window: Any) -> int:
    native = getattr(window, "native", None)
    handle = getattr(native, "Handle", 0)
    if not handle:
        # pywebview does not expose ``Window.native`` on every Windows backend.
        # WinForms keeps the actual Form in its per-window instance registry.
        gui = getattr(window, "gui", None)
        browser_view = getattr(gui, "BrowserView", None)
        instances = getattr(browser_view, "instances", {})
        form = instances.get(getattr(window, "uid", None)) if hasattr(instances, "get") else None
        handle = getattr(form, "Handle", 0)
    if hasattr(handle, "ToInt64"):
        try:
            return int(handle.ToInt64())
        except (TypeError, ValueError):
            return 0
    try:
        return int(handle)
    except (TypeError, ValueError):
        return 0


def _native_window_form(window: Any) -> Any | None:
    native = getattr(window, "native", None)
    if native is not None and hasattr(native, "Handle"):
        return native
    gui = getattr(window, "gui", None)
    browser_view = getattr(gui, "BrowserView", None)
    instances = getattr(browser_view, "instances", {})
    if hasattr(instances, "get"):
        return instances.get(getattr(window, "uid", None))
    return None


def _windows_apps_use_dark_theme() -> bool:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return int(value) == 0
    except (ImportError, OSError, TypeError, ValueError):
        return False


def configure_windows_native_chrome(window: Any) -> bool:
    """Remove the duplicate caption icon and align the caption with app theme.

    The packaged Windows backend is WinForms. ``ShowIcon`` controls only the
    caption decoration; the form remains a normal taskbar window and the
    independent notification-area icon is untouched. DWM attributes are
    best-effort because older Windows builds do not expose all of them.
    """

    if sys.platform != "win32":
        return False
    form = _native_window_form(window)
    if form is not None:
        try:
            form.ShowIcon = False
            form.ShowInTaskbar = True
        except Exception:
            logging.getLogger("vram_radar").exception("failed to simplify the native caption")

    handle = _native_window_handle(window)
    if not handle:
        return form is not None
    dark = _windows_apps_use_dark_theme()
    try:
        dwmapi = ctypes.windll.dwmapi
        enabled = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):  # current and older immersive-dark-mode IDs
            if dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(handle),
                attribute,
                ctypes.byref(enabled),
                ctypes.sizeof(enabled),
            ) == 0:
                break

        # Match the WebView's --surface token on supported Windows 11 builds.
        caption_rgb = (0x11, 0x18, 0x16) if dark else (0xF8, 0xF9, 0xF4)
        text_rgb = (0xEF, 0xF6, 0xF2) if dark else (0x17, 0x1D, 0x1A)
        border_rgb = (0x2B, 0x38, 0x34) if dark else (0xCB, 0xD2, 0xCC)
        for attribute, rgb in ((35, caption_rgb), (36, text_rgb), (34, border_rgb)):
            color = ctypes.c_uint32(rgb[0] | (rgb[1] << 8) | (rgb[2] << 16))
            dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(handle),
                attribute,
                ctypes.byref(color),
                ctypes.sizeof(color),
            )
    except (AttributeError, OSError, TypeError, ctypes.ArgumentError):
        logging.getLogger("vram_radar").info("native caption theming is unavailable")
    return True


def native_window_is_normal(window: Any) -> bool:
    """Return whether a native window is neither minimized nor maximized."""

    if sys.platform == "win32":
        handle = _native_window_handle(window)
        if not handle:
            return False
        user32 = ctypes.windll.user32
        native_handle = wintypes.HWND(handle)
        return not bool(user32.IsIconic(native_handle)) and not bool(
            user32.IsZoomed(native_handle)
        )
    if sys.platform == "darwin":
        native = getattr(window, "native", None)
        if native is None:
            return False
        is_miniaturized = getattr(native, "isMiniaturized", None)
        if callable(is_miniaturized) and bool(is_miniaturized()):
            return False
        is_zoomed = getattr(native, "isZoomed", None)
        if callable(is_zoomed) and bool(is_zoomed()):
            return False
        style_mask = getattr(native, "styleMask", None)
        if callable(style_mask) and int(style_mask()) & (1 << 14):
            # NSWindowStyleMaskFullScreen
            return False
    return True


def refresh_windows_window_surface(window: Any) -> bool:
    """Synchronously repaint the restored WinForms and WebView2 surface.

    The packaged WebView2 compositor can retain the form's loading-color frame
    after a native minimize/restore even though the DOM is still alive. A
    recursive native invalidation repaints the existing surface without
    navigating, reloading, or replacing any page state.
    """

    if sys.platform != "win32":
        return False
    handle = _native_window_handle(window)
    if not handle:
        return False
    try:
        return bool(
            ctypes.windll.user32.RedrawWindow(
                wintypes.HWND(handle),
                None,
                None,
                _REDRAW_WINDOW_AND_CHILDREN,
            )
        )
    except (AttributeError, OSError, TypeError, ctypes.ArgumentError):
        logging.getLogger("vram_radar").exception("failed to repaint the restored WebView surface")
        return False


def recover_offscreen_windows_window(
    window: Any,
    preferred_geometry: WindowGeometry | None = None,
) -> bool:
    """Recover off-screen or anomalously small restore bounds on Windows."""
    if sys.platform != "win32":
        return False
    handle = _native_window_handle(window)
    if not handle:
        logging.getLogger("vram_radar").info("window recovery skipped because no native handle was available")
        return False
    user32 = ctypes.windll.user32
    work_area = _WindowRect()
    if not user32.SystemParametersInfoW(48, 0, ctypes.byref(work_area), 0):  # SPI_GETWORKAREA
        return False

    preferred = (
        WindowGeometry.validated(preferred_geometry.width, preferred_geometry.height)
        if preferred_geometry is not None
        else None
    ) or WindowGeometry()
    recovered = False
    stable_checks = 0
    for attempt in range(6):
        rect = _WindowRect()
        if not user32.GetWindowRect(wintypes.HWND(handle), ctypes.byref(rect)):
            return recovered
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        owned_by_monitor = bool(user32.MonitorFromRect(ctypes.byref(rect), 0))
        invalid_size = width < MIN_WINDOW_WIDTH or height < MIN_WINDOW_HEIGHT
        if owned_by_monitor and not invalid_size:
            if not recovered:
                return False
            stable_checks += 1
            if stable_checks >= 2:
                user32.SetForegroundWindow(wintypes.HWND(handle))
                return True
        else:
            stable_checks = 0
            if not recovered:
                logging.getLogger("vram_radar").info(
                    "recovering off-screen window handle=%s bounds=%s,%s,%s,%s",
                    handle,
                    rect.left,
                    rect.top,
                    rect.right,
                    rect.bottom,
                )
            work_width = max(1, work_area.right - work_area.left)
            work_height = max(1, work_area.bottom - work_area.top)
            width = min(work_width, max(MIN_WINDOW_WIDTH, preferred.width))
            height = min(work_height, max(MIN_WINDOW_HEIGHT, preferred.height))
            x = work_area.left + max(0, (work_area.right - work_area.left - width) // 2)
            y = work_area.top + max(0, (work_area.bottom - work_area.top - height) // 2)
            # A hidden minimized WinForms form can keep reapplying the shell's
            # sentinel coordinates. Force the native state out of minimized
            # before placing it back in the work area.
            user32.ShowWindow(wintypes.HWND(handle), 9)  # SW_RESTORE
            recovered = bool(
                user32.SetWindowPos(
                    wintypes.HWND(handle),
                    wintypes.HWND(0),
                    x,
                    y,
                    width,
                    height,
                    0x0044,
                )
            ) or recovered  # SWP_NOZORDER | SWP_SHOWWINDOW
        if attempt < 5:
            # WinForms can apply its minimized restore bounds shortly after Show().
            # Recheck briefly so that delayed backend state cannot move it off-screen again.
            time.sleep(0.15)

    if recovered:
        user32.SetForegroundWindow(wintypes.HWND(handle))
    logging.getLogger("vram_radar").info("off-screen window recovery result=%s", recovered)
    return recovered


def restore_window(
    window: Any,
    preferred_geometry: WindowGeometry | None = None,
) -> None:
    window.restore()
    window.show()
    try:
        recover_offscreen_windows_window(window, preferred_geometry)
    except Exception:
        logging.getLogger("vram_radar").exception("failed to recover an off-screen window")
    if sys.platform == "win32":
        refresh_windows_window_surface(window)


def create_windows_tray_icon(
    icon_path: Path,
    show_window: Callable[[], None],
    exit_application: Callable[[], None],
    *,
    refresh_application: Callable[[], None] | None = None,
    open_settings: Callable[[], None] | None = None,
    toggle_pause: Callable[[], None] | None = None,
    is_paused: StateCallback | None = None,
    status_text: TextCallback | str | None = None,
    language: TextCallback | str = "zh-CN",
) -> Any:
    pystray = importlib.import_module("pystray")
    image_module = importlib.import_module("PIL.Image")
    with image_module.open(icon_path) as source:
        image = source.convert("RGBA")

    def show_callback(_icon: Any, _item: Any) -> None:
        show_window()

    def refresh_callback(_icon: Any, _item: Any) -> None:
        if refresh_application is None:
            return
        try:
            refresh_application()
        except Exception:
            logging.getLogger("vram_radar").exception("notification-area refresh failed")

    def settings_callback(_icon: Any, _item: Any) -> None:
        if open_settings is None:
            return
        try:
            open_settings()
        except Exception:
            logging.getLogger("vram_radar").exception("notification-area settings action failed")

    def toggle_pause_callback(_icon: Any, _item: Any) -> None:
        if toggle_pause is None:
            return
        try:
            toggle_pause()
        except Exception:
            logging.getLogger("vram_radar").exception("notification-area pause toggle failed")

    def paused() -> bool:
        if is_paused is None:
            return False
        try:
            return bool(is_paused())
        except Exception:
            logging.getLogger("vram_radar").exception("failed to read automatic-monitoring state")
            return False

    def english() -> bool:
        try:
            value = language() if callable(language) else language
        except Exception:
            logging.getLogger("vram_radar").exception("failed to read interface language")
            return False
        return str(value).strip() == "en"

    def status_label(_item: Any) -> str:
        try:
            value = status_text() if callable(status_text) else status_text
        except Exception:
            logging.getLogger("vram_radar").exception("failed to read notification-area status")
            value = None
        if value is not None and str(value).strip():
            return str(value).strip()
        if is_paused is not None:
            if english():
                return "Automatic monitoring paused" if paused() else "Automatic monitoring active"
            return "自动监控已暂停" if paused() else "自动监控中"
        return "VRAM Radar is running" if english() else "VRAM Radar 正在运行"

    def pause_label(_item: Any) -> str:
        if english():
            return "Resume automatic monitoring" if paused() else "Pause automatic monitoring"
        return "继续自动监控" if paused() else "暂停自动监控"

    def menu_label(chinese: str, english_text: str) -> Callable[[Any], str]:
        return lambda _item: english_text if english() else chinese

    def exit_callback(_icon: Any, _item: Any) -> None:
        exit_application()

    menu = pystray.Menu(
        pystray.MenuItem(status_label, None, enabled=False),
        pystray.MenuItem(menu_label("显示 VRAM Radar", "Show VRAM Radar"), show_callback, default=True),
        pystray.MenuItem(menu_label("立即刷新", "Refresh now"), refresh_callback, enabled=refresh_application is not None),
        pystray.MenuItem(menu_label("打开设置", "Open settings"), settings_callback, enabled=open_settings is not None),
        pystray.MenuItem(
            pause_label,
            toggle_pause_callback,
            enabled=toggle_pause is not None and is_paused is not None,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(menu_label("退出", "Exit"), exit_callback),
    )
    return pystray.Icon("vram-radar", image, "VRAM Radar", menu)


class WindowsTrayController:
    def __init__(
        self,
        window: Any,
        icon_path: Path,
        *,
        icon_factory: TrayIconFactory = create_windows_tray_icon,
        refresh_application: Callable[[], None] | None = None,
        open_settings: Callable[[], None] | None = None,
        toggle_pause: Callable[[], None] | None = None,
        is_paused: StateCallback | None = None,
        status_text: TextCallback | str | None = None,
        language: TextCallback | str = "zh-CN",
        close_behavior: Callable[[], str] | str = "hide",
        before_exit: Callable[[], None] | None = None,
        restore_application: Callable[[], object] | None = None,
        hide_application: Callable[[], object] | None = None,
    ) -> None:
        self.window = window
        self.icon_path = icon_path
        self.icon_factory = icon_factory
        self.refresh_application = refresh_application
        self.open_settings = open_settings
        self.toggle_pause = toggle_pause
        self.is_paused = is_paused
        self.status_text = status_text
        self.language = language
        self.close_behavior = close_behavior
        self.before_exit = before_exit
        self.restore_application = restore_application
        self.hide_application = hide_application
        self.icon: Any = None
        self.started = False
        self.exit_requested = False
        self._visibility_lock = threading.Lock()
        self._show_generation = 0

    def start(self) -> None:
        if self.started:
            return
        if self.icon_factory is create_windows_tray_icon or any(
            value is not None
            for value in (
                self.refresh_application,
                self.open_settings,
                self.toggle_pause,
                self.is_paused,
                self.status_text,
            )
        ):
            icon = self.icon_factory(
                self.icon_path,
                self.show_window,
                self.request_exit,
                refresh_application=(self.refresh_now if self.refresh_application is not None else None),
                open_settings=(self.show_settings if self.open_settings is not None else None),
                toggle_pause=(
                    self.toggle_automatic_monitoring if self.toggle_pause is not None else None
                ),
                is_paused=(
                    self.automatic_monitoring_is_paused if self.is_paused is not None else None
                ),
                status_text=(
                    self.current_status_text
                    if self.status_text is not None or self.is_paused is not None
                    else None
                ),
                language=self.current_language,
            )
        else:
            # Preserve the original three-argument factory contract for tests,
            # embedders, and integrations that do not request quick actions.
            icon = self.icon_factory(self.icon_path, self.show_window, self.request_exit)
        self.icon = icon
        self.window.events.closing += self._on_closing
        self.window.events.minimized += self._on_minimized
        try:
            icon.run_detached()
        except Exception:
            self.window.events.closing -= self._on_closing
            self.window.events.minimized -= self._on_minimized
            self.icon = None
            raise
        self.started = True

    def _hide_window(self, show_generation: int | None = None) -> None:
        with self._visibility_lock:
            if show_generation is not None and show_generation != self._show_generation:
                return
        try:
            if self.hide_application is None:
                self.window.hide()
            elif self.hide_application() is False:
                return
        except Exception:
            logging.getLogger("vram_radar").exception("failed to hide the window in the notification area")
            return
        with self._visibility_lock:
            stale_hide = (
                show_generation is not None and show_generation != self._show_generation
            )
        if stale_hide:
            # A show request can arrive after the initial generation check but
            # before a slower native hide completes. Reapply that newer intent
            # without holding the lock across UI-thread dispatch.
            self._restore_window()
            return
        self._notify_hidden_once()

    def _notify_hidden_once(self) -> None:
        global _hidden_notification_sent
        with _hidden_notification_lock:
            if _hidden_notification_sent:
                return
            _hidden_notification_sent = True
        if self.current_language() == "en":
            self.notify(
                "VRAM Radar is still running",
                "The window is in the notification area. Right-click the icon to refresh, pause monitoring, or exit.",
            )
        else:
            self.notify(
                "VRAM Radar 仍在运行",
                "窗口已收起到通知区域。右键图标可以刷新、暂停监控或退出。",
            )

    def _close_behavior_value(self) -> str:
        try:
            value = self.close_behavior() if callable(self.close_behavior) else self.close_behavior
        except Exception:
            logging.getLogger("vram_radar").exception("failed to read window close behavior")
            return "hide"
        return str(value).strip().lower()

    def _on_closing(self) -> bool:
        if self.exit_requested:
            return True
        if self._close_behavior_value() == "exit":
            self.prepare_exit()
            if self.before_exit is not None:
                self.before_exit()
                # Cancel this native close. The lifecycle coordinator will
                # destroy the window only after the activation worker joins.
                return False
            return True
        with self._visibility_lock:
            show_generation = self._show_generation
        threading.Thread(
            target=self._hide_window,
            args=(show_generation,),
            name="vram-radar-tray-hide",
            daemon=True,
        ).start()
        return False

    def _on_minimized(self) -> None:
        # Keep ordinary minimization in the taskbar. Only an explicit close
        # follows the configured notification-area behavior.
        return None

    def show_window(self) -> None:
        with self._visibility_lock:
            self._show_generation += 1
        self._restore_window()

    def _restore_window(self) -> None:
        if self.restore_application is None:
            restore_window(self.window)
        else:
            self.restore_application()

    def refresh_now(self) -> None:
        if self.refresh_application is None:
            return
        try:
            self.refresh_application()
        except Exception:
            logging.getLogger("vram_radar").exception("notification-area refresh failed")

    def show_settings(self) -> None:
        if self.open_settings is None:
            return
        try:
            self.open_settings()
        except Exception:
            logging.getLogger("vram_radar").exception("notification-area settings action failed")

    def toggle_automatic_monitoring(self) -> None:
        if self.toggle_pause is None:
            return
        try:
            self.toggle_pause()
        except Exception:
            logging.getLogger("vram_radar").exception("notification-area pause toggle failed")

    def automatic_monitoring_is_paused(self) -> bool:
        if self.is_paused is None:
            return False
        try:
            return bool(self.is_paused())
        except Exception:
            logging.getLogger("vram_radar").exception("failed to read automatic-monitoring state")
            return False

    def current_status_text(self) -> str:
        try:
            value = self.status_text() if callable(self.status_text) else self.status_text
        except Exception:
            logging.getLogger("vram_radar").exception("failed to read notification-area status")
            value = None
        if value is not None and str(value).strip():
            return str(value).strip()
        if self.current_language() == "en":
            return "Automatic monitoring paused" if self.automatic_monitoring_is_paused() else "Automatic monitoring active"
        return "自动监控已暂停" if self.automatic_monitoring_is_paused() else "自动监控中"

    def current_language(self) -> str:
        try:
            value = self.language() if callable(self.language) else self.language
        except Exception:
            logging.getLogger("vram_radar").exception("failed to read interface language")
            return "zh-CN"
        return "en" if str(value).strip() == "en" else "zh-CN"

    def refresh_menu(self) -> bool:
        callback = getattr(self.icon, "update_menu", None)
        if not callable(callback):
            return False
        try:
            callback()
            return True
        except Exception:
            logging.getLogger("vram_radar").exception("failed to update the notification-area menu")
            return False

    def notify(self, title: str, message: str) -> bool:
        callback = getattr(self.icon, "notify", None)
        if not callable(callback):
            return False
        try:
            callback(message, title)
            return True
        except Exception:
            logging.getLogger("vram_radar").exception("failed to show a notification-area message")
            return False

    def _stop_icon(self) -> None:
        if self.icon is None:
            return
        try:
            self.icon.stop()
        except Exception:
            logging.getLogger("vram_radar").exception("failed to stop the notification-area icon")

    def request_exit(self) -> None:
        if self.exit_requested:
            return
        self.prepare_exit()
        if self.before_exit is not None:
            self.before_exit()
        else:
            self.window.destroy()

    def prepare_exit(self) -> None:
        self.exit_requested = True
        self._stop_icon()

    def stop(self) -> None:
        self.exit_requested = True
        if self.started:
            self.window.events.closing -= self._on_closing
            self.window.events.minimized -= self._on_minimized
        self._stop_icon()
        self.started = False
