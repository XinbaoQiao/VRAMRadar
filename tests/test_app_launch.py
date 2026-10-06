import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vram_radar import app_launch
from vram_radar.app_launch import (AppLauncher, LaunchTarget, RowFeedback, find_app_window, launch_targets,
                                   row_cursor, target_from_state)
from vram_radar.providers import DETECTION_SPECS
from vram_radar.providers.base import detect_install, launch_facts, msix_family
from vram_radar import ui_dialogs
from vram_radar.ui_dialogs import hover_row_at, row_highlight_rect, row_highlight_rgb

from tests.test_providers import FakeEnv, touch


def state(kind="exe", target=r"C:\Apps\Kimi\Kimi.exe", folder=r"C:\Apps\Kimi", installed=True):
    return {"installed": installed, "launch": {"kind": kind, "target": target, "folder": folder}}


class TargetTests(unittest.TestCase):
    def test_target_requires_installed_and_known_kind(self):
        self.assertEqual(target_from_state("kimi", state()),
                         LaunchTarget("kimi", "exe", r"C:\Apps\Kimi\Kimi.exe", r"C:\Apps\Kimi"))
        self.assertIsNone(target_from_state("kimi", state(installed=False)))
        self.assertIsNone(target_from_state("kimi", state(kind="url")))
        self.assertIsNone(target_from_state("kimi", state(target="  ")))
        self.assertIsNone(target_from_state("kimi", {"installed": True}))
        self.assertIsNone(target_from_state("kimi", None))

    def test_launch_targets_only_for_selected_detected(self):
        states = {"kimi": state(), "grok": state(installed=False), "codex": state("aumid", "OpenAI.X_p!App", "")}
        self.assertEqual(sorted(launch_targets(states, ["kimi", "grok", "codex", "glm"])), ["codex", "kimi"])
        self.assertEqual(launch_targets(None, ["kimi"]), {})

    def test_hover_row_bands(self):
        hits = [("kimi", 0, 30), ("", 30, 40), ("grok", 40, 80)]
        self.assertEqual(hover_row_at(hits, 0), "kimi")
        self.assertIsNone(hover_row_at(hits, 35))
        self.assertEqual(hover_row_at(hits, 79), "grok")
        self.assertIsNone(hover_row_at(hits, 80))
        self.assertIsNone(hover_row_at(None, 1))


class WindowLookupSafetyTests(unittest.TestCase):
    def test_never_focuses_vram_radar_itself(self):
        paths = {7: r"D:\Download\VRAM Radar\VRAMRadar.exe", 8: r"D:\Download\Kimi\Kimi.exe"}
        target = LaunchTarget("kimi", "exe", r"D:\Download\Kimi\Kimi.exe", r"D:\Download\Kimi")
        self.assertEqual(find_app_window(target, windows=[(70, 7)], image_path=paths.get, own_pid=7), 0)
        self.assertEqual(find_app_window(target, windows=[(70, 7), (80, 8)], image_path=paths.get, own_pid=7), 80)

    @unittest.skipUnless(sys.platform == "win32", "Windows path semantics")
    def test_broad_folders_do_not_match_unrelated_programs(self):
        import os
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        paths = {1: os.path.join(program_files, "Other", "other.exe"), 2: r"D:\Some\thing.exe"}
        loose = LaunchTarget("kimi", "exe", os.path.join(program_files, "Kimi.exe"), program_files)
        self.assertEqual(find_app_window(loose, windows=[(10, 1)], image_path=paths.get, own_pid=0), 0)
        root = LaunchTarget("kimi", "exe", r"D:\Kimi.exe", "D:\\")
        self.assertEqual(find_app_window(root, windows=[(20, 2)], image_path=paths.get, own_pid=0), 0)
        self.assertFalse(app_launch.own_folder(program_files))
        self.assertFalse(app_launch.own_folder("D:\\"))
        self.assertTrue(app_launch.own_folder(os.path.join(program_files, "Kimi")))

    def test_vanished_app_reports_failed_and_logs_only_the_error_type(self):
        target = LaunchTarget("kimi", "exe", "C:\\Gone \u7a0b\u5e8f\\Kimi.exe", "C:\\Gone \u7a0b\u5e8f")
        outcomes = []

        def start(_target):
            raise FileNotFoundError(2, "secret detail", "C:\\Gone \u7a0b\u5e8f\\Kimi.exe")
        launcher = AppLauncher(start=start, find=lambda _t: 0, focus=lambda _h: False,
                               platform="win32", background=False)
        with self.assertLogs("vram_radar", level="INFO") as logs:
            self.assertEqual(launcher.open(target, outcomes.append), "failed")
        self.assertEqual(outcomes, ["failed"])
        text = "\n".join(logs.output)
        self.assertIn("FileNotFoundError", text)
        self.assertNotIn("secret detail", text)
        self.assertNotIn("Gone", text)


class StripClickTests(unittest.TestCase):
    """The taskbar strip always opens VRAM Radar; only hover-card rows open apps."""

    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "src" / "vram_radar"
        self.surface = (root / "usage_surface.py").read_text(encoding="utf-8")
        self.dialogs = (root / "ui_dialogs.py").read_text(encoding="utf-8")

    def test_strip_click_and_double_click_open_vram_radar(self):
        start = self.surface.index("def single_click(*_):")
        body = self.surface[start:self.surface.index("click_timer.Tick += single_click", start)]
        self.assertIn("self._action(self.open_home)", body)
        self.assertNotIn("_open_app", body)
        up = self.surface[self.surface.index("def mouse_up(sender, event):"):start]
        self.assertIn("self._action(self.open_settings)", up)
        self.assertNotIn("_open_app", up)

    def test_strip_keeps_arrow_cursor_and_no_model_highlight(self):
        self.assertNotIn("Cursors.Hand", self.surface)
        self.assertNotIn("model_at_cursor", self.surface)
        self.assertNotIn("openApp", self.surface)

    def test_card_resolves_click_before_hiding(self):
        start = self.dialogs.index("def _attach_hover_mouse(form)")
        body = self.dialogs[start:self.dialogs.index("def _cancel_hover_delay", start)]
        self.assertNotIn("hide_hover_card", body)
        self.assertIn("form.MouseDown += on_down", body)
        click = self.surface[self.surface.index("def card_row_click(pid):"):]
        click = click[:click.index("self._card_row_click = card_row_click")]
        self.assertNotIn("_suspend_hover()", click)       # hidden by the result handler, after the flash
        self.assertIn("then=self._suspend_hover", click)


class RowFeedbackTests(unittest.TestCase):
    def test_hover_press_click_flash(self):
        fb = RowFeedback()
        self.assertTrue(fb.move("kimi", True))
        self.assertEqual(fb.highlight(), ("kimi", "hover"))
        self.assertFalse(fb.move("kimi", True))            # no re-render while still on the row
        self.assertTrue(fb.down("kimi", True))
        self.assertEqual(fb.highlight(), ("kimi", "pressed"))
        self.assertEqual(fb.up("kimi"), "kimi")
        self.assertEqual(fb.highlight(), ("kimi", "hover"))
        self.assertEqual(fb.finish("kimi", "launched"), "flash")
        self.assertEqual(fb.highlight(), ("kimi", "flash"))
        self.assertFalse(fb.down("kimi", True))            # no new press during the flash
        fb.reset()
        self.assertIsNone(fb.highlight())

    def test_outcomes(self):
        fb = RowFeedback()
        self.assertEqual(fb.finish("kimi", "focused"), "flash")
        self.assertEqual(fb.finish("kimi", "failed"), "failed")
        self.assertEqual(fb.highlight(), ("kimi", "failed"))
        self.assertIsNone(fb.finish("kimi", "debounced"))   # never a misleading success flash
        self.assertIsNone(fb.highlight())

    def test_undetected_rows_get_nothing(self):
        fb = RowFeedback()
        self.assertFalse(fb.move("glm", False))
        self.assertFalse(fb.down("glm", False))
        self.assertIsNone(fb.up("glm"))
        self.assertIsNone(fb.highlight())

    def test_release_elsewhere_or_leave_cancels(self):
        fb = RowFeedback()
        fb.down("kimi", True)
        self.assertIsNone(fb.up("grok"))
        fb.down("kimi", True)
        fb.move("grok", True)                              # dragged to another row
        self.assertEqual(fb.highlight(), ("grok", "hover"))
        self.assertIsNone(fb.up("grok"))
        fb.move("kimi", True)
        self.assertTrue(fb.leave())
        self.assertIsNone(fb.highlight())

    def test_row_cursor(self):
        targets = {"kimi"}
        clickable = lambda pid: pid in targets
        self.assertEqual(row_cursor("kimi", clickable), "hand")
        self.assertEqual(row_cursor("glm", clickable), "arrow")
        self.assertEqual(row_cursor(None, clickable), "arrow")
        self.assertEqual(row_cursor("kimi", None), "arrow")
        self.assertEqual(row_cursor("kimi", lambda pid: 1 / 0), "arrow")


class RowHighlightGeometryTests(unittest.TestCase):
    layout = {"width": 200, "row_spans": [("kimi", 12, 50), ("glm", 60, 90)],
              "row_hits": [("kimi", 0, 55), ("glm", 55, 102)]}

    def test_rect_stays_inside_its_band(self):
        x, y, w, h = row_highlight_rect(self.layout, "kimi", 1.0)
        self.assertEqual((x, w), (4, 192))
        self.assertGreaterEqual(y, 1)
        self.assertLessEqual(y + h, 54)
        x, y, w, h = row_highlight_rect(self.layout, "glm", 1.0)
        self.assertGreaterEqual(y, 56)
        self.assertLessEqual(y + h, 101)
        self.assertIsNone(row_highlight_rect(self.layout, "grok", 1.0))
        self.assertIsNone(row_highlight_rect(None, "kimi", 1.0))

    def test_colours_contrast_aware(self):
        dark, light = (44, 44, 44), (255, 255, 255)
        hover_dark = row_highlight_rgb(dark, "hover")
        self.assertTrue(all(h > d for h, d in zip(hover_dark, dark)))          # lighter on dark
        hover_light = row_highlight_rgb(light, "hover")
        self.assertTrue(all(h < v for h, v in zip(hover_light, light)))        # darker on light
        self.assertAlmostEqual(hover_dark[0], round(44 + (255 - 44) * 0.12), delta=1)
        self.assertAlmostEqual(hover_light[0], round(255 * 0.92), delta=1)
        self.assertGreater(row_highlight_rgb(dark, "pressed")[0], hover_dark[0])
        self.assertLess(row_highlight_rgb(light, "pressed")[0], hover_light[0])
        self.assertNotEqual(row_highlight_rgb(dark, "flash", (96, 205, 255)), row_highlight_rgb(dark, "pressed"))
        grey = row_highlight_rgb(dark, "failed")
        self.assertEqual(len(set(grey)), 1)                                      # neutral grey


@unittest.skipUnless(sys.platform == "win32", "WinForms card handlers")
class CardMouseHandlerTests(unittest.TestCase):
    """In-process: drives the card's handlers with synthetic event objects (no real input)."""

    def setUp(self):
        import clr
        clr.AddReference("System.Drawing")
        clr.AddReference("System.Windows.Forms")
        from System.Windows.Forms import Cursors, MouseButtons
        self.Cursors, self.Buttons = Cursors, MouseButtons

        class Event(list):
            def __iadd__(self, handler):
                self.append(handler)
                return self

        class FakeForm:
            def __init__(self):
                self.Cursor = Cursors.Default
                for name in ("MouseMove", "MouseEnter", "MouseDown", "MouseUp", "MouseLeave"):
                    setattr(self, name, Event())

        self.form = FakeForm()
        self.saved = dict(ui_dialogs._HOVER)
        self.clicked = []
        ui_dialogs._HOVER.update(shown=False, feedback=None, render=None, form=None,
                                 layout={"width": 200, "row_spans": [("kimi", 12, 50), ("glm", 60, 90)],
                                         "row_hits": [("kimi", 0, 55), ("glm", 55, 102)]})
        ui_dialogs.set_hover_handlers(on_row_click=self.clicked.append, on_leave=None,
                                      clickable=lambda pid: pid == "kimi")
        ui_dialogs._attach_hover_mouse(self.form)

    def tearDown(self):
        ui_dialogs._HOVER.clear()
        ui_dialogs._HOVER.update(self.saved)

    def fire(self, name, y, button=None):
        event = type("E", (), {"Y": y, "Button": button if button is not None else self.Buttons.Left})()
        for handler in getattr(self.form, name):
            handler(self.form, event)

    def test_hand_cursor_only_on_detected_rows(self):
        self.fire("MouseMove", 20)
        self.assertEqual(self.form.Cursor, self.Cursors.Hand)
        self.assertEqual(ui_dialogs._feedback().highlight(), ("kimi", "hover"))
        self.fire("MouseMove", 70)
        self.assertEqual(self.form.Cursor, self.Cursors.Default)
        self.assertIsNone(ui_dialogs._feedback().highlight())

    def test_click_is_hit_tested_on_the_card(self):
        self.fire("MouseDown", 20)
        self.assertEqual(ui_dialogs._feedback().highlight(), ("kimi", "pressed"))
        self.fire("MouseUp", 21)
        self.assertEqual(self.clicked, ["kimi"])
        self.fire("MouseDown", 70)
        self.fire("MouseUp", 70)
        self.fire("MouseDown", 20, self.Buttons.Right)
        self.fire("MouseUp", 20, self.Buttons.Right)
        self.assertEqual(self.clicked, ["kimi"])

    def test_result_flash_then_callback(self):
        pending, done = [], []
        schedule = lambda ms, fn: pending.append((ms, fn))
        self.assertEqual(ui_dialogs.hover_row_result("kimi", "launched", then=lambda: done.append(1),
                                                     schedule=schedule), "flash")
        self.assertEqual(ui_dialogs._feedback().highlight(), ("kimi", "flash"))
        self.assertEqual(pending[0][0], 200)                 # ~150-250 ms confirmation
        pending[0][1]()
        self.assertEqual(done, [1])
        self.assertIsNone(ui_dialogs._feedback().highlight())

    def test_failed_and_debounced_results(self):
        pending, done = [], []
        schedule = lambda ms, fn: pending.append((ms, fn))
        self.assertEqual(ui_dialogs.hover_row_result("kimi", "failed", then=lambda: done.append("f"),
                                                     schedule=schedule), "failed")
        self.assertEqual(ui_dialogs._feedback().highlight(), ("kimi", "failed"))
        self.assertIsNone(ui_dialogs.hover_row_result("kimi", "debounced", then=lambda: done.append("d"),
                                                      schedule=schedule))
        self.assertIsNone(ui_dialogs._feedback().highlight())  # no flash for a debounced click
        pending[0][1]()                                         # superseded: does nothing
        pending[1][1]()
        self.assertEqual(done, ["d"])


class WindowLookupTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows path semantics (window lookup is Windows-only)")
    def test_find_by_folder_or_exe_name(self):
        paths = {1: r"C:\Apps\Kimi\resources\helper.exe", 2: r"C:\Other\Kimi.exe", 3: r"C:\Apps\KimiX\a.exe"}
        target = LaunchTarget("kimi", "exe", r"C:\Apps\Kimi\Kimi.exe", r"C:\Apps\Kimi")
        self.assertEqual(find_app_window(target, windows=[(30, 3), (10, 1)], image_path=paths.get), 10)
        no_folder = LaunchTarget("kimi", "exe", r"C:\Apps\Kimi\Kimi.exe", "")
        self.assertEqual(find_app_window(no_folder, windows=[(30, 3), (20, 2)], image_path=paths.get), 20)
        aumid = LaunchTarget("codex", "aumid", "OpenAI.X_p!App", "")
        self.assertEqual(find_app_window(aumid, windows=[(10, 1), (20, 2)], image_path=paths.get), 0)


class LauncherTests(unittest.TestCase):
    def make(self, *, hwnd=0, focus_ok=True, start_error=None, platform="win32"):
        self.now = 100.0
        self.calls = []

        def start(target):
            self.calls.append(("start", target.provider_id))
            if start_error:
                raise start_error

        def focus(handle):
            self.calls.append(("focus", handle))
            return focus_ok

        return AppLauncher(start=start, find=lambda t: hwnd, focus=focus, clock=lambda: self.now,
                           platform=platform, background=False)

    target = LaunchTarget("kimi", "exe", r"C:\Apps\Kimi\Kimi.exe", r"C:\Apps\Kimi")

    def test_focuses_existing_window_else_launches(self):
        self.assertEqual(self.make(hwnd=42).open(self.target), "focused")
        self.assertEqual(self.calls, [("focus", 42)])
        self.assertEqual(self.make(hwnd=0).open(self.target), "launched")
        self.assertEqual(self.calls, [("start", "kimi")])

    def test_focus_refused_falls_back_to_launch(self):
        self.assertEqual(self.make(hwnd=42, focus_ok=False).open(self.target), "launched")
        self.assertEqual(self.calls, [("focus", 42), ("start", "kimi")])

    def test_debounce_per_app(self):
        launcher = self.make()
        self.assertEqual(launcher.open(self.target), "launched")
        self.now += 0.5
        self.assertEqual(launcher.open(self.target), "debounced")
        other = LaunchTarget("grok", "exe", r"C:\G\Grok.exe", r"C:\G")
        self.assertEqual(launcher.open(other), "launched")
        self.now += app_launch.LAUNCH_DEBOUNCE_SECONDS
        self.assertEqual(launcher.open(self.target), "launched")

    def test_on_done_reports_final_outcome(self):
        results = []
        self.assertEqual(self.make(hwnd=42).open(self.target, results.append), "focused")
        self.assertEqual(self.make().open(self.target, results.append), "launched")
        with self.assertLogs("vram_radar", "INFO"):
            self.assertEqual(self.make(start_error=OSError("x")).open(self.target, results.append), "failed")
        launcher = self.make()
        launcher.open(self.target)
        self.assertEqual(launcher.open(self.target, results.append), "debounced")
        self.assertEqual(results, ["focused", "launched", "failed"])

    def test_failures_are_quiet(self):
        with self.assertLogs("vram_radar", "INFO") as logs:
            self.assertEqual(self.make(start_error=OSError("secret path")).open(self.target), "failed")
        self.assertNotIn("secret path", "\n".join(logs.output))
        self.assertEqual(self.make().open(None), "none")

    def test_mac_never_looks_up_windows(self):
        launcher = self.make(platform="darwin")
        launcher.find = lambda t: self.fail("window lookup on macOS")
        self.assertEqual(launcher.open(LaunchTarget("kimi", "bundle", "com.moonshot.kimi")), "launched")


class LaunchFactsTests(unittest.TestCase):
    def test_exe_squirrel_and_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plain = touch(root / "Kimi" / "Kimi.exe")
            self.assertEqual(launch_facts(str(plain), "registry"), ("exe", str(plain), str(plain.parent)))
            versioned = touch(root / "Grok" / "app-1.2.3" / "Grok.exe")
            self.assertEqual(launch_facts(str(versioned), "folder")[2], str(root / "Grok"))
            stub = touch(root / "Grok" / "Grok.exe")
            self.assertEqual(launch_facts(str(versioned), "folder")[:2], ("exe", str(stub)))
        self.assertEqual(launch_facts("/usr/bin/codex", "cli"), ("", "", ""))
        self.assertEqual(launch_facts("", "registry"), ("", "", ""))

    def test_msix_aumid_from_manifest(self):
        self.assertEqual(msix_family("OpenAI.Codex_26.9.1.0_x64__2p2nqsd0c76g0"), "OpenAI.Codex_2p2nqsd0c76g0")
        self.assertEqual(msix_family("bad"), "")
        with tempfile.TemporaryDirectory() as tmp:
            touch(Path(tmp) / "AppxManifest.xml",
                  '<Package><Applications><Application Id="App" Executable="app\\Codex.exe"/></Applications></Package>')
            kind, target, folder = launch_facts(tmp, "msix", package="OpenAI.Codex_26.9.1.0_x64__2p2nqsd0c76g0")
        self.assertEqual((kind, target, folder), ("aumid", "OpenAI.Codex_2p2nqsd0c76g0!App", tmp))


class DetectionSpecTests(unittest.TestCase):
    def test_every_provider_has_a_spec(self):
        self.assertEqual(set(DETECTION_SPECS),
                         {"codex", "deepseek", "grok", "kimi", "claude", "glm", "qwen", "yuanbao"})

    def test_kimi_share_target_package_is_not_the_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = FakeEnv(Path(tmp), packages=[("KimiShareTarget_1.0.0.0_x64__abc", str(Path(tmp) / "pkg"))])
            self.assertFalse(detect_install(env, **DETECTION_SPECS["kimi"]).installed)

    def test_hermetic_env_finds_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = FakeEnv(Path(tmp))
            for provider_id, spec in DETECTION_SPECS.items():
                self.assertFalse(detect_install(env, **spec).installed, provider_id)


if __name__ == "__main__":
    unittest.main()
