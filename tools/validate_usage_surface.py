"""Exercise the native Codex display with synthetic quota and no account access."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from vram_radar.usage_surface import CodexUsageSurface, windows_taskbar_geometry, taskbar_anchor, taskbar_scale, windows_taskbar_dpi
from benchmark_webview_ui import FakeApi, _wait_until_ready


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
    display = {"codex_show_disks": True, "codex_time_format": "decimal"}
    def save_display(key, value):
        display[key] = value
        return {"ok": True}
    surface = CodexUsageSurface(window, lambda: dict(state), language=lambda: language["value"],
                                open_settings=details_opened.set, open_home=home_opened.set, refresh=refreshed.set,
                                display_options=lambda: dict(display), save_display=save_display,
                                disable=lambda: state.update(enabled=False), quit_application=lambda: None)
    result = {"ok": False, "synthetic_only": True, "remote_connections": 0, "platform": sys.platform}
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
                        "h" in label.Text for label in surface._countdowns[:2])
                    text_controls = [*surface._labels[:2], *surface._captions[:2], *surface._countdowns[:2]]
                    assertions["both_text_lines_fit"] = all(label.GetPreferredSize(Size(0, 0)).Height <= label.Height
                        and label.GetPreferredSize(Size(0, 0)).Width <= label.Width for label in text_controls)
                    assertions["no_extra_taskbar_button"] = not surface.form.ShowInTaskbar
                    geometry = windows_taskbar_geometry()
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
                invoke(double_check)
                assertions["double_click_opens_only_details"] = details_opened.wait(2) and not home_opened.is_set()
                from unittest.mock import patch
                from System.Drawing import Point
                def right_click_check():
                    click_once()
                    surface._menu.Show(Point(surface.form.Left, surface.form.Top-300))
                    assertions["opening_menu_cancels_pending_single_click"] = not surface._click_timer.Enabled and not home_opened.is_set()
                    surface._menu.Close()
                invoke(right_click_check)
                with patch("vram_radar.usage_surface.windows_surface_obscured", return_value=True):
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
                        upper, lower = surface._disk_bounds
                        assertions["both_disks_have_identical_pixel_diameters"] = upper[2:] == lower[2:] == (round(14*surface._scale),)*2
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
                        assertions["menu_uses_radar_palette"] = surface._menu.BackColor == surface.form.BackColor
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
                    from System.Windows.Forms import Form
                    other = Form()
                    other.Text = "Synthetic outside-click target"
                    other.ShowInTaskbar = False
                    other.Show()
                    surface._menu.Show(Point(100, 200))
                    surface._display_menu.ShowDropDown()
                    other.Activate()
                    return other
                other_forms = []
                invoke(lambda: other_forms.append(outside_focus_check()))
                time.sleep(0.15)
                def inspect_dismissal():
                    assertions["outside_activation_closes_parent_and_submenu"] = (
                        not surface._menu.Visible and not surface._display_menu.DropDown.Visible)
                    other_forms[0].Close()
                    other_forms[0].Dispose()
                    surface._menu.Close()
                invoke(inspect_dismissal)
                from unittest.mock import patch
                for bright, background, foreground in [(True, (243, 243, 243), (28, 28, 28)),
                                                        (False, (32, 32, 32), (240, 240, 240))]:
                    with patch("vram_radar.usage_surface.windows_taskbar_palette",
                               return_value=(background, foreground, (60, 60, 60), bright)):
                        invoke(tick)
                        assertions["live_theme_light" if bright else "live_theme_dark"] = (
                            surface.form.BackColor.R == background[0] and surface._menu.BackColor == surface.form.BackColor
                            and surface._labels[0].ForeColor.G > surface._labels[0].ForeColor.R)
                invoke(tick)
                def select_weekly():
                    surface._window_menu.DropDownItems[1].PerformClick()
                    assertions["quota_window_selection_updates_both_disks"] = (
                        surface._labels[0].Text == "42%" and surface._reading["percent"] == 42
                        and 47 < surface._reading["time_percent"] < 49
                        and surface._window_menu.DropDownItems[1].Checked)
                    surface._window_menu.DropDownItems[0].PerformClick()
                    assertions["reference_widget_size"] = (surface.form.Width == sum(round(n*surface._scale) for n in (25, 47, 2))
                                                           and surface.form.Height == round(40*surface._scale))
                invoke(select_weekly)
                invoke(lambda: surface._display_choices[0][0].PerformClick())
                wait_for(lambda: display["codex_show_disks"] is False)
                invoke(tick)
                invoke(lambda: assertions.update(pure_text_option_removes_icon_column=not surface._show_disks and surface._labels[0].Left == round(5*surface._scale)))
                display["codex_time_format"] = "days"
                invoke(tick)
                invoke(lambda: assertions.update(unified_decimal_hours_ignore_legacy_preference=(
                    len(surface._display_choices) == 2 and surface._countdowns[0].Text.endswith("h")
                    and "." in surface._countdowns[0].Text),
                    countdown_matches_percent_font=(surface._countdowns[0].Font.Bold
                        and surface._countdowns[0].Font.Size == surface._labels[0].Font.Size)))
                display.update(codex_show_disks=True, codex_time_format="decimal")
                invoke(tick)
                original_state = dict(state)
                fit_results = []
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
                assertions["countdowns_in_menu"] = "h" in str(surface.status_item.menu().itemAtIndex_(0).title())
            state["stale"] = True
            invoke(tick)
            captured_text = []
            invoke(lambda: captured_text.append(surface._labels[0].Text if sys.platform == "win32"
                                                else str(surface.status_item.button().title())))
            text = captured_text[0]
            assertions["stale_quota_not_shown_as_current"] = "68%" not in text and "—" in text
            state["enabled"] = False
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

    with tempfile.TemporaryDirectory(prefix="codex-surface-", dir=args.output) as temporary:
        timeout.start()
        webview.start(run, private_mode=True, storage_path=temporary)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
