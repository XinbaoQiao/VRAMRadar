"""Exercise the native Codex display with synthetic quota and no account access."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
import tempfile
from check_english_web_ui import temporary_browser_directory
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from vram_radar.usage_surface import (CodexUsageSurface, windows_taskbar_geometry, taskbar_anchor, taskbar_scale,
                                      windows_taskbar_dpi, docked_target, docked_point, strip_margin)
from benchmark_webview_ui import FakeApi, _wait_until_ready
from vram_radar.reset_format import RESET_RE


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "work/codex-surface-validation")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    import webview
    logging.disable(logging.CRITICAL)
    window = webview.create_window("VRAM Radar synthetic quota display", width=900, height=700,
                                  url=(ROOT / "src/vram_radar/web/index.html").as_uri(),
                                  js_api=FakeApi(), hidden=True, focus=False)
    state = {"enabled": False, "state": "disabled", "windows": []}
    language = {"value": "zh-CN"}
    refreshed = threading.Event()
    home_opened, details_opened = threading.Event(), threading.Event()
    display = {"codex_time_format": "decimal"}
    def save_display(key, value):
        display[key] = value
        return {"ok": True}
    overview = {"enabled": True}
    surface = CodexUsageSurface(window, lambda: dict(state), language=lambda: language["value"],
                                providers=lambda: {"enabled": overview["enabled"], "selected": ["codex"], "providers": {}},
                                open_settings=details_opened.set, open_home=home_opened.set, refresh=refreshed.set,
                                display_options=lambda: dict(display), save_display=save_display,
                                disable=lambda: state.update(enabled=False), quit_application=lambda: None)
    result = {"ok": False, "synthetic_only": True, "remote_connections": 0, "platform": sys.platform}
    # Never take the user's foreground: the menu's tray-style
    # SetForegroundWindow(owner) is recorded instead of performed.
    foreground_requests = []
    if sys.platform == "win32":
        from vram_radar.usage_surface import _dll
        _dll("user32").SetForegroundWindow = lambda hwnd: foreground_requests.append(hwnd) or 0
    timeout = threading.Timer(45, window.destroy)

    def wait_for(predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            if time.monotonic() >= deadline:
                raise AssertionError("native display did not settle")
            time.sleep(0.05)

    def run():
        try:
            _wait_until_ready(window, time.monotonic()+20)
            surface.start()
            assertions = {}
            if sys.platform == "win32":
                from System import Action
                from System.Drawing import Bitmap, Rectangle, Size
                from System.Drawing.Imaging import ImageFormat
                from System.Windows.Forms import Screen
                def invoke(callback):
                    window.native.Invoke(Action(callback))
                assertions["disabled_creates_no_visible_strip"] = surface.form is not None and not surface.form.Visible
                original_handle = int(surface.form.Handle.ToInt64())
            elif sys.platform == "darwin":
                from PyObjCTools.AppHelper import callAfter
                def invoke(callback):
                    done = threading.Event()
                    def perform():
                        try:
                            callback()
                        finally:
                            done.set()
                    callAfter(perform)
                    if not done.wait(5):
                        raise AssertionError("Cocoa callback did not complete")
                wait_for(lambda: surface.timer is not None)
                assertions["disabled_creates_no_visible_strip"] = surface.status_item is None
            else:
                raise RuntimeError("native display validation requires Windows or macOS")
            state.update(enabled=True, state="ready", windows=[
                {"name": "Codex", "window_minutes": 300, "remaining_percent": 68, "resets_at": time.time()+8200},
                {"name": "Codex", "window_minutes": 10080, "remaining_percent": 42, "resets_at": time.time()+290000},
            ])
            tick = surface._tick if sys.platform == "win32" else surface._mac_tick
            invoke(tick)
            assertions["enabled_surface_visible"] = surface.active
            window.hide()
            invoke(tick)
            if sys.platform == "win32":
                def inspect_layout():
                    assertions["visible_with_main_window_hidden"] = bool(surface.form.Visible)
                    assertions["percent_and_countdown_visible"] = all("%" in label.Text for label in surface._labels[:2]) and all(
                        RESET_RE.fullmatch(label.Text) for label in surface._countdowns[:2])
                    text_controls = [*surface._labels[:2], *surface._captions[:2], *surface._countdowns[:2]]
                    assertions["both_text_lines_fit"] = all(label.GetPreferredSize(Size(0, 0)).Height <= label.Height
                        and label.GetPreferredSize(Size(0, 0)).Width <= label.Width for label in text_controls)
                    assertions["no_extra_taskbar_button"] = not surface.form.ShowInTaskbar
                    geometry = windows_taskbar_geometry()
                    # Docked placement: the empty area right of Widgets/left of Start when it fits,
                    # otherwise directly before the notification area.
                    if surface._slot == "left":
                        # Same reading the strip itself uses (its own rect excluded).  The
                        # left gap may be narrower than the strip (wide weather widget, or
                        # another running VRAM Radar strip next to it): by design the strip
                        # then stays anchored at the gap start and is compacted, never moved
                        # to the tray side, so compare against docked_target, not left_slot.
                        own = (surface.form.Left, surface.form.Top, surface.form.Right, surface.form.Bottom)
                        elements = surface._layout.elements(
                            __import__("ctypes").windll.user32.FindWindowW("Shell_TrayWnd", None), geometry[0], own)
                        margin = strip_margin(surface._scale)
                        target = docked_target(geometry[0], geometry[1], elements,
                                               (surface.form.Width, surface.form.Height), margin)
                        assertions["anchored_in_left_taskbar_area"] = target[0] == "left" and (
                            surface.form.Left, surface.form.Top) == docked_point(geometry[0], target, surface.form.Width)
                        start = elements.get("StartButton") or elements.get("SearchButton")
                        assertions["left_slot_clear_of_start"] = not start or surface.form.Left + surface.form.Width <= start[0]
                    else:
                        assertions["anchored_before_notification_area"] = bool(geometry) and (surface.form.Left, surface.form.Top) == taskbar_anchor(*geometry, (surface.form.Width, surface.form.Height))
                    assertions["compact_height_fits_taskbar"] = bool(geometry) and surface.form.Height == round(40*taskbar_scale(windows_taskbar_dpi(), geometry)) and surface.form.Height < geometry[0][3]-geometry[0][1]-6
                    surface._drag, surface._drag_origin = (0, 0), (0, 0)
                    original_location = surface.form.Location
                    surface._move_handler(surface.form, None)
                    assertions["docked_widget_cannot_be_dragged"] = surface.form.Location == original_location and surface._placement == "taskbar" and surface._position is None
                    surface._drag = None
                invoke(inspect_layout)
                invoke(lambda: assertions.update(period_picker_removed_from_context_menu=not surface._menu.Items.Contains(surface._window_menu)))
                from System.Windows.Forms import MouseEventArgs, MouseButtons
                def click_once():
                    surface._drag, surface._drag_moved = (0, 0), False
                    surface._up_handler(surface.form, MouseEventArgs(MouseButtons.Left, 1, 0, 0, 0))
                def single_check():
                    click_once()
                    assertions["single_click_waits_for_double_click_window"] = surface._click_timer.Enabled and not home_opened.is_set()
                    surface._single_click_handler()
                invoke(single_check)
                assertions["single_click_opens_only_gpu_home"] = home_opened.wait(2) and not details_opened.is_set()
                home_opened.clear()
                def double_check():
                    click_once()
                    click_once()
                    assertions["double_click_cancels_pending_home"] = not surface._click_timer.Enabled
                    click_once()    # third click of a fast burst must not queue "GPU home" (closes Settings)
                    assertions["triple_click_keeps_settings_open"] = not surface._click_timer.Enabled
                invoke(double_check)
                assertions["double_click_opens_only_details"] = details_opened.wait(2) and not home_opened.is_set()
                time.sleep(0.05)
                assertions["triple_click_keeps_settings_open"] = (assertions["triple_click_keeps_settings_open"]
                                                                  and not home_opened.is_set())
                invoke(lambda: setattr(surface._click_burst, "until", 0.0))
                from unittest.mock import patch
                from System.Drawing import Point
                def right_click_check():
                    click_once()
                    surface._menu.Show(Point(surface.form.Left, surface.form.Top-300))
                    assertions["opening_menu_cancels_pending_single_click"] = not surface._click_timer.Enabled and not home_opened.is_set()
                    surface._menu.Close()
                invoke(right_click_check)
                with patch("vram_radar.usage_surface.windows_surface_obscured", return_value=True):
                    for _ in range(2):  # hides after 2 obscured ticks (debounced against flicker)
                        invoke(tick)
                    assertions["fullscreen_or_hidden_taskbar_hides_widget"] = not surface.form.Visible
                with patch("vram_radar.usage_surface.windows_surface_obscured", return_value=False):
                    invoke(tick)
                    assertions["widget_recovers_after_obstruction"] = surface.form.Visible
                with patch("vram_radar.usage_surface.windows_taskbar_dpi", return_value=192):
                    invoke(tick)
                    assertions["menu_rescales_without_restart"] = surface._menu_dpi == 192 and surface._dock_item.Width == 410 and surface._menu_font.Size == 24
                invoke(tick)
                import ctypes
                user32 = ctypes.windll.user32
                get_style = user32.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user32.GetWindowLongW
                get_style.restype = ctypes.c_ssize_t
                assertions["does_not_activate"] = bool(get_style(original_handle, -20) & 0x08000000)
                def capture():
                    bitmap = Bitmap(surface.form.Width, surface.form.Height)
                    try:
                        surface.form.DrawToBitmap(bitmap, Rectangle(0, 0, bitmap.Width, bitmap.Height))
                        bitmap.Save(str(args.output.resolve() / "taskbar-strip.png"), ImageFormat.Png)
                    finally:
                        bitmap.Dispose()
                invoke(capture)
                def capture_menu():
                    from System.Drawing import Point
                    surface._menu.Show(Point(surface.form.Left, surface.form.Top-400))
                    bitmap = Bitmap(surface._menu.Width, surface._menu.Height)
                    try:
                        surface._menu.DrawToBitmap(bitmap, Rectangle(0, 0, bitmap.Width, bitmap.Height))
                        bitmap.Save(str(args.output.resolve() / "context-menu.png"), ImageFormat.Png)
                        assertions["menu_uses_radar_palette"] = surface._menu.BackColor == surface._menu_back
                    finally:
                        bitmap.Dispose()
                        surface._menu.Close()
                invoke(capture_menu)
                def check_submenu():
                    samples = []
                    for x in (100, Screen.PrimaryScreen.WorkingArea.Right-10):
                        surface._menu.Show(Point(x, 200))
                        surface._display_menu.ShowDropDown()
                        parent = surface._menu.Bounds
                        child = surface._display_menu.DropDown.Bounds
                        gap = min(abs(child.Left-parent.Right), abs(parent.Left-child.Right))
                        samples.append({"parent": [parent.X, parent.Y, parent.Width, parent.Height],
                                        "child": [child.X, child.Y, child.Width, child.Height], "gap": gap})
                        surface._menu.Close()
                    result["submenu_geometry"] = samples
                    assertions["submenu_attaches_on_both_screen_edges"] = all(s["gap"] <= 2 for s in samples)
                invoke(check_submenu)
                def outside_focus_check():
                    # The user clicking another window changes this thread's
                    # active window; WinForms' ModalMenuFilter notices that on
                    # the next pumped message and closes the menu chain.
                    # Reproduce it thread-locally with SetActiveWindow (never
                    # the foreground: the old Form.Activate() variant needed
                    # real foreground rights, which a background validator or
                    # a locked session never has, so it always failed).
                    from System.Windows.Forms import Application as WinApp, Form, FormBorderStyle, FormStartPosition
                    u32 = ctypes.WinDLL("user32")
                    u32.SetActiveWindow.argtypes = [ctypes.c_void_p]
                    u32.SetActiveWindow.restype = ctypes.c_void_p
                    u32.GetActiveWindow.restype = u32.GetForegroundWindow.restype = ctypes.c_void_p
                    u32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
                    u32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
                    before = len(foreground_requests)
                    owner = int(surface.form.Handle.ToInt64())
                    u32.SetActiveWindow(owner)        # what the granted SetForegroundWindow(owner) leaves behind
                    surface._menu.Show(Point(100, 200))
                    surface._display_menu.ShowDropDown()
                    opened = surface._menu.Visible and surface._display_menu.DropDown.Visible
                    other = Form()
                    other.ShowInTaskbar = False
                    other.StartPosition = FormStartPosition.Manual
                    other.FormBorderStyle = getattr(FormBorderStyle, "None")
                    other.Location = Point(-20000, -20000)
                    other_handle = int(other.Handle.ToInt64())
                    u32.ShowWindow(other_handle, 4)   # SW_SHOWNOACTIVATE, off-screen
                    u32.SetActiveWindow(other_handle)
                    u32.PostMessageW(other_handle, 0, None, None)   # WM_NULL: pump once
                    WinApp.DoEvents()
                    closed = not surface._menu.Visible and not surface._display_menu.DropDown.Visible
                    foreground = u32.GetForegroundWindow()
                    other.Close()
                    other.Dispose()
                    surface._menu.Close()
                    return {"opened": opened, "closed": closed,
                            "owner_foreground_requested": len(foreground_requests) > before,
                            "foreground_untouched": foreground not in (owner, other_handle)}
                outside = []
                invoke(lambda: outside.append(outside_focus_check()))
                result["outside_dismissal"] = outside[0]
                assertions["outside_activation_closes_parent_and_submenu"] = all(outside[0].values())
                def outside_press_check():
                    # Fallback watcher on the real menu, button/cursor readers
                    # replaced (no real input): inside keeps it, outside closes.
                    from vram_radar.menu_dismiss import BUTTONS
                    watch = surface._outside_watch
                    real = watch._buttons, watch._cursor
                    up, left = (False,) * len(BUTTONS), (True,) + (False,) * (len(BUTTONS) - 1)
                    fake = {"b": up, "c": (-30000, -30000)}
                    watch._buttons, watch._cursor = (lambda: fake["b"]), (lambda: fake["c"])
                    def press(at):
                        fake["c"], fake["b"] = at, left
                        watch.tick()
                        fake["b"] = up
                        watch.tick()
                    try:
                        surface._menu.Show(Point(100, 200))
                        surface._display_menu.ShowDropDown()
                        started = watch.running
                        sub = surface._display_menu.DropDown.Bounds
                        press((sub.Left + 3, sub.Top + 3))
                        press((surface._menu.Left + 3, surface._menu.Top + 3))
                        inside_kept = surface._menu.Visible and surface._display_menu.DropDown.Visible
                        press((-30000, -30000))
                        closed = not surface._menu.Visible and not surface._display_menu.DropDown.Visible
                        return {"started": started, "inside_kept": inside_kept, "outside_closed": closed,
                                "stopped": not watch.running}
                    finally:
                        watch._buttons, watch._cursor = real
                        surface._menu.Close()
                pressed = []
                invoke(lambda: pressed.append(outside_press_check()))
                result["outside_press"] = pressed[0]
                assertions["outside_press_closes_menu_and_stops_polling"] = all(pressed[0].values())
                from unittest.mock import patch
                for bright, background, foreground in [(True, (243, 243, 243), (28, 28, 28)),
                                                        (False, (32, 32, 32), (240, 240, 240))]:
                    with patch("vram_radar.usage_surface.windows_taskbar_palette",
                               return_value=(background, foreground, (60, 60, 60), bright)):
                        surface._palette_at = 0
                        invoke(tick)
                        assertions["live_theme_light" if bright else "live_theme_dark"] = (
                            surface.form.BackColor.R == background[0] and surface._menu.BackColor == surface.form.BackColor
                            and surface._labels[0].ForeColor.G > surface._labels[0].ForeColor.R)
                invoke(tick)
                def select_weekly():
                    surface._window_menu.DropDownItems[1].PerformClick()
                    assertions["quota_window_selection_updates_text"] = (
                        surface._labels[0].Text == "42%" and surface._reading["percent"] == 42
                        and surface._window_menu.DropDownItems[1].Checked)
                    surface._window_menu.DropDownItems[0].PerformClick()
                    # Reference size, widened only as much as the unified countdown needs.
                    countdown = surface._countdowns[0]
                    assertions["reference_widget_size"] = (
                        surface.form.Width >= sum(round(n*surface._scale) for n in (5, 47, 2))
                        and surface.form.Width == round(5*surface._scale) + countdown.Width + round(2*surface._scale)
                        and countdown.GetPreferredSize(Size(0, 0)).Width <= countdown.Width
                        and surface.form.Height == round(40*surface._scale))
                # Full-size checks run with a roomy gap left of Start; the live
                # taskbar may leave less, which compacts the strip (checked below).
                def roomy_gap(width=2000):
                    return patch("vram_radar.usage_surface.left_gap",
                                 side_effect=lambda bar, elements, margin=6: (bar[0] + 100, bar[0] + 100 + width))
                with roomy_gap():
                    invoke(select_weekly)
                invoke(lambda: assertions.update(text_only_strip=surface._labels[0].Left == round(5*surface._scale)
                    and all(c[1] in {"usage_background", "usage_labels"} for c in surface._display_choices)))
                display["codex_time_format"] = "days"
                invoke(tick)
                invoke(lambda: assertions.update(unified_decimal_hours_ignore_legacy_preference=(
                    bool(RESET_RE.fullmatch(surface._countdowns[0].Text))),
                    countdown_matches_percent_font=(surface._countdowns[0].Font.Bold
                        and surface._countdowns[0].Font.Size == surface._labels[0].Font.Size)))
                display.update(codex_time_format="decimal")
                invoke(tick)
                looks = {}
                for style in ("transparent", "match", "dark", "light", "accent"):
                    display["usage_background"] = style
                    surface._palette_at = 0
                    invoke(tick)
                    def record(style=style):
                        keyed = surface.form.TransparencyKey == surface.form.BackColor
                        looks[style] = (keyed, surface._fill is not None)
                    invoke(record)
                assertions["background_styles_apply"] = (
                    looks["transparent"] == (True, False) and looks["match"][1] is False
                    and not looks["match"][0] and all(looks[s] == (True, True) for s in ("dark", "light", "accent")))
                invoke(lambda: assertions.update(style_keeps_non_activating_toolwindow=bool(
                    __import__("ctypes").WinDLL("user32").GetWindowLongW(int(surface.form.Handle.ToInt64()), -20)
                    & 0x08000080 == 0x08000080)))
                display["usage_background"] = "transparent"
                invoke(tick)
                original_state = dict(state)
                fit_results = []
                gap_patch = roomy_gap(); gap_patch.start()
                for sample in [
                    {"enabled": True, "state": "ready", "windows": [
                        {"window_minutes": 300, "remaining_percent": 100, "resets_at": time.time()+8200},
                        {"window_minutes": 10080, "remaining_percent": 0, "resets_at": time.time()+290000}]},
                    {"enabled": True, "state": "ready", "windows": [
                        {"window_minutes": 525600, "remaining_percent": None, "resets_at": None}]},
                    {"enabled": True, "state": "ready", "windows": [
                        {"window_minutes": 10080, "remaining_percent": 100, "resets_at": time.time()+604800}]},
                    {"enabled": True, "state": "ready", "windows": [
                        {"window_minutes": 300, "remaining_percent": 68, "resets_at": time.time()-1}]},
                    {"enabled": True, "state": "error", "code": "login_required", "windows": []},
                ]:
                    for locale in ("zh-CN", "en"):
                        state.clear()
                        state.update(sample)
                        language["value"] = locale
                        invoke(tick)
                        def inspect_edge_layout():
                            controls = [*surface._labels, *surface._captions, *surface._countdowns]
                            fit_results.append(all(control.GetPreferredSize(Size(0, 0)).Width <= control.Width
                                and control.GetPreferredSize(Size(0, 0)).Height <= control.Height
                                for control in controls if control.Visible))
                        invoke(inspect_edge_layout)
                assertions["full_empty_unknown_and_localized_error_text_fit"] = all(fit_results)
                gap_patch.stop()
                # Codex-only strip in a gap too small for it: smaller type, then
                # clipped, never wider than the gap (so it cannot reach Start).
                state.clear()
                state.update({"enabled": True, "state": "ready", "windows": [
                    {"window_minutes": 10080, "remaining_percent": 100, "resets_at": time.time() + 604800}]})
                gap_fits = []
                for gap_px in (round(56 * surface._scale), round(24 * surface._scale)):
                    with roomy_gap(gap_px):
                        invoke(tick)
                        invoke(lambda gap_px=gap_px: gap_fits.append(
                            0 < surface.form.ClientSize.Width <= gap_px
                            and all(c.Left + c.Width <= surface.form.ClientSize.Width
                                    for c in (*surface._labels, *surface._countdowns) if c.Visible)))
                assertions["single_strip_never_wider_than_gap"] = len(gap_fits) == 2 and all(gap_fits)
                state.clear()
                state.update(original_state)
                language["value"] = "zh-CN"
                invoke(tick)
                invoke(lambda: surface._menu.Items[0].PerformClick())
                assertions["refresh_menu_invokes_callback"] = refreshed.wait(2)
                invoke(lambda: surface._dock_item.PerformClick())
                assertions["menu_moves_above_taskbar"] = Screen.FromControl(surface.form).WorkingArea.Contains(surface.form.Bounds)
                invoke(lambda: surface._dock_item.PerformClick())
                assertions["menu_restores_default_dock"] = surface._placement == "taskbar" and surface._position is None
                # Simulate a drag outside the current screen and clamp it back.
                surface._placement = "free"
                surface._position = (-10000, -10000)
                invoke(tick)
                assertions["drag_is_clamped_to_visible_screen"] = Screen.FromControl(surface.form).Bounds.Contains(surface.form.Bounds)
            else:
                assertions["visible_with_main_window_hidden"] = surface.status_item is not None
                assertions["percent_and_countdown_visible"] = "%" in str(surface.status_item.button().title())
                assertions["countdowns_in_menu"] = bool(RESET_RE.search(str(surface.status_item.menu().itemAtIndex_(0).title())))
            if sys.platform == "win32":
                # Master switch off: strip and hover card go away on the first tick
                # (no 3-tick debounce), the leave-grace timer stops, and on again
                # restores the strip without a restart.  The card state is simulated
                # so no card is drawn on screen.
                from vram_radar import ui_dialogs
                def arm_card():
                    ui_dialogs._HOVER["shown"] = True
                    surface._leave_grace.Start()
                invoke(arm_card)
                overview["enabled"] = False
                invoke(tick)
                assertions["master_off_hides_strip_and_card_at_once"] = (
                    not surface.form.Visible and not ui_dialogs._HOVER.get("shown")
                    and not surface._leave_grace.Enabled and not surface._click_timer.Enabled)
                overview["enabled"] = True
                invoke(tick)
                assertions["master_on_restores_strip"] = surface.active and surface.form.Visible
            state["stale"] = True
            invoke(tick)
            captured_text = []
            invoke(lambda: captured_text.append(surface._labels[0].Text if sys.platform == "win32"
                                                else str(surface.status_item.button().title())))
            text = captured_text[0]
            assertions["stale_quota_not_shown_as_current"] = "68%" not in text and "—" in text
            state["enabled"] = False
            for _ in range(3):  # removed after 3 inactive ticks (debounced)
                invoke(tick)
            assertions["disable_removes_surface"] = not surface.active and (
                not surface.form.Visible if sys.platform == "win32" else surface.status_item is None)
            state["enabled"] = True
            invoke(tick)
            assertions["reenable_works"] = surface.active
            surface.stop()
            if sys.platform == "win32":
                assertions["cleanup_disposes_native_window"] = surface.form.IsDisposed
            else:
                wait_for(lambda: surface.status_item is None)
                assertions["cleanup_removes_status_item"] = surface.status_item is None
            result.update(ok=all(assertions.values()), assertions=assertions)
        except Exception as error:
            result.update(error=str(error))
            surface.stop()
        finally:
            timeout.cancel()
            (args.output / "native-surface.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
            window.destroy()

    with temporary_browser_directory(prefix="codex-surface-", directory=args.output) as temporary:
        timeout.start()
        webview.start(run, private_mode=True, storage_path=temporary)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
