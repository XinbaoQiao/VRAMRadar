import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vram_radar import app_launch
from vram_radar.app_launch import (AppLauncher, LaunchTarget, click_action, find_app_window, hit_model,
                                   launch_targets, open_app_menu_entries, target_from_state)
from vram_radar.providers import DETECTION_SPECS
from vram_radar.providers.base import detect_install, launch_facts, msix_family
from vram_radar.ui_dialogs import hover_row_at

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

    def test_click_action_and_hit_model(self):
        targets = {"kimi": object()}
        self.assertEqual(click_action(None, targets), "home")
        self.assertEqual(click_action("kimi", targets), "open_app")
        self.assertEqual(click_action("grok", targets), "nothing")
        cells = [("kimi", (0, 0, 50, 20)), ("grok", (50, 0, 100, 20))]
        self.assertEqual(hit_model(cells, (10, 5)), "kimi")
        self.assertEqual(hit_model(cells, (50, 5)), "grok")
        self.assertIsNone(hit_model(cells, (100, 5)))
        self.assertIsNone(hit_model(None, (1, 1)))

    def test_mac_menu_entries_localized(self):
        states = {"kimi": state(), "grok": state(installed=False)}
        names = {"kimi": "Kimi", "grok": "Grok"}
        self.assertEqual(open_app_menu_entries(["kimi", "grok"], states, "en", names), [("Open Kimi", "kimi")])
        self.assertEqual(open_app_menu_entries(["kimi"], states, "zh-CN", names), [("\u6253\u5f00 Kimi", "kimi")])

    def test_hover_row_bands(self):
        hits = [("kimi", 0, 30), ("", 30, 40), ("grok", 40, 80)]
        self.assertEqual(hover_row_at(hits, 0), "kimi")
        self.assertIsNone(hover_row_at(hits, 35))
        self.assertEqual(hover_row_at(hits, 79), "grok")
        self.assertIsNone(hover_row_at(hits, 80))
        self.assertIsNone(hover_row_at(None, 1))


class WindowLookupTests(unittest.TestCase):
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
