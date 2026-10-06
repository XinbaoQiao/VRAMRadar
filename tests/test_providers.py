import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from vram_radar.models import Profile
from vram_radar.providers import (DEFAULT_PROVIDERS, PROVIDER_IDS, ProviderMonitor, normalize_selection, probe_all,
                                  PROVIDERS)
from vram_radar.providers import deepseek, grok, kimi, generic
from vram_radar.providers.base import Environment, Process, detect_install, read_json, read_tail_lines
from vram_radar.usage_surface import provider_reading

SECRET = "sk" "-THIS-MUST-NEVER-LEAK-0123456789abcdef"


class FakeEnv(Environment):
    def __init__(self, root: Path, *, processes=(), uninstall=(), packages=(), shortcuts=(),
                 app_paths=None, drive_roots=()):
        super().__init__()
        self.home = root / "home"
        self.appdata = root / "home/AppData/Roaming"
        self.localappdata = root / "home/AppData/Local"
        self.programfiles = [root / "Program Files"]
        self.environ = {}
        self._processes = list(processes)
        self._uninstall = list(uninstall)
        self._packages = list(packages)
        # Hermetic: never read this machine's Start Menu, App Paths, drives or Spotlight.
        self._shortcuts = list(shortcuts)
        self._app_paths = dict(app_paths or {})
        self._drive_roots = list(drive_roots)
        self.mdfind_runner = lambda query: []
        for folder in (self.home, self.appdata, self.localappdata, *self.programfiles):
            folder.mkdir(parents=True, exist_ok=True)


def touch(path: Path, content: str | bytes = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


class SelectionTests(unittest.TestCase):
    def test_normalize_keeps_registry_order_and_drops_unknown(self):
        self.assertEqual(normalize_selection(["kimi", "codex", "nope", "kimi"]), ("codex", "kimi"))
        self.assertEqual(normalize_selection([]), DEFAULT_PROVIDERS)
        self.assertEqual(normalize_selection("codex"), DEFAULT_PROVIDERS)
        self.assertEqual(len(PROVIDER_IDS), len(set(PROVIDER_IDS)))

    def test_profile_migration_default_roundtrip_and_tolerance(self):
        legacy = Profile.empty("test").to_dict()
        legacy.pop("usage_providers")
        self.assertEqual(Profile.from_dict(legacy).usage_providers, ("codex",))
        for bad in ("codex", 5, None, [1, 2], ["unknown-future-app"]):
            self.assertEqual(Profile.from_dict({**legacy, "usage_providers": bad}).usage_providers, ("codex",))
        profile = Profile.from_dict({**legacy, "usage_providers": ["grok", "deepseek", "future"]})
        self.assertEqual(profile.usage_providers, ("deepseek", "grok"))
        self.assertEqual(Profile.from_dict(profile.to_dict()), profile)


class DetectionTests(unittest.TestCase):
    def test_registry_process_msix_known_and_sibling_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kimi_exe = touch(root / "D/Apps/Kimi/Kimi.exe")
            env = FakeEnv(root, processes=[Process(1, "explorer.exe")], uninstall=[{"DisplayName": "Kimi", "DisplayVersion": "3.2.4",
                                            "DisplayIcon": f"{kimi_exe},0"}],
                          packages=[("OpenAI.Codex_26.1.0.0_x64__abc", str(root / ("WindowsApps/OpenAI." "Codex")))])
            found = detect_install(env, uninstall=[r"^Kimi\b"], executables=["Kimi.exe"], processes=["kimi.exe"])
            self.assertTrue(found.installed)
            self.assertEqual((found.version, found.source, found.running), ("3.2.4", "registry", False))
            package = detect_install(env, packages=[r"^OpenAI\.Codex_"])
            self.assertEqual((package.installed, package.version, package.source), (True, "26.1.0.0", "msix"))
            # A second app in the same custom root is found through sibling search.
            touch(root / "D/Apps/Qwen/Qwen.exe")
            env.extra_roots.append(root / "D/Apps")
            sibling = detect_install(env, executables=["Qwen.exe"], sibling_names=["Qwen"])
            self.assertEqual((sibling.installed, sibling.source), (True, "sibling_folder"))
            touch(env.localappdata / "Programs/Grok Bot/Grok Bot.exe")
            known = detect_install(env, executables=["Grok Bot.exe"], folders=["{localappdata}/Programs/Grok Bot"])
            self.assertEqual(known.source, "known_path")

    def test_running_copy_preferred_and_squirrel_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = touch(root / "old/Claude.exe")
            new = touch(root / "home/AppData/Local/AnthropicClaude/app-1.2.3/claude.exe")
            env = FakeEnv(root, processes=[Process(10, "claude.exe", str(old))])
            found = detect_install(env, processes=["claude.exe"], executables=["claude.exe"],
                                   folders=["{localappdata}/AnthropicClaude"])
            self.assertEqual((found.path, found.source, found.running, found.candidates), (str(old), "process", True, 2))
            env._processes = []
            found = detect_install(env, processes=["claude.exe"], executables=["claude.exe"],
                                   folders=["{localappdata}/AnthropicClaude"])
            self.assertEqual((found.path, found.version), (str(new), "1.2.3"))
            self.assertIsNone(found.running)  # process list unavailable: unknown, not "stopped"

    def test_non_ascii_paths_and_damaged_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "用户 数据"
            env = FakeEnv(root)
            exe = touch(env.localappdata / "Programs/智谱清言/智谱清言.exe")
            state = generic.glm(env)
            self.assertTrue(state["installed"])
            self.assertEqual(state["install_path"], str(exe))
            broken = touch(root / "bad.json", b"\xff{not json")
            self.assertIsNone(read_json(broken))
            touch(root / "huge.json", "[" + "1," * 2_000_000 + "1]")
            self.assertIsNone(read_json(root / "huge.json"))
            self.assertIsNone(read_json(root / "missing.json"))
            self.assertEqual(read_tail_lines(root / "missing.log"), [])


class ProviderProbeTests(unittest.TestCase):
    def test_kimi_reads_only_refresh_results_and_never_the_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = FakeEnv(root, processes=[Process(1, "explorer.exe")])
            data = env.appdata / "kimi-desktop"
            touch(data / "bridge-store/token-store.json", json.dumps({"encryption": "safeStorage", "data": SECRET * 4}))
            now = time.time()
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 60))
            reset = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now + 7200))
            touch(data / "logs/main.log", "\n".join([
                f"[{stamp}.100] [info]  \x1b[36m[KimiTransport]\x1b[39m  request start authorization=Bearer {SECRET}",
                f"[{stamp}.200] [info]  \x1b[36m[SubscriptionManager]\x1b[39m  refreshed(sub): level=20 isMember=true "
                f"omniRatio=0.5 exhausted=false resetAt={reset} token={SECRET}",
                f"[{stamp}.300] [info]  \x1b[36m[SubscriptionManager]\x1b[39m  refreshed(stats): overdrawn=false "
                f"sendBlocked=true balanceEvent=none",
            ]))
            state = kimi.probe(env, now=now)
            dumped = json.dumps(state, ensure_ascii=False)
            self.assertNotIn(SECRET, dumped)
            self.assertNotIn("safeStorage", dumped)
            self.assertTrue(state["signed_in"])
            self.assertFalse(state["stale"])
            self.assertEqual(state["headline"]["en"], "Exhausted")  # sendBlocked
            self.assertEqual(state["quota"]["en"], "Exhausted")   # used up is a known quota
            self.assertIn("reset_at", state)
            self.assertIn("Membership level 20 (member)", [f["en"] for f in state["facts"]])

    def test_kimi_stale_and_signed_out(self):
        with tempfile.TemporaryDirectory() as directory:
            env = FakeEnv(Path(directory))
            data = env.appdata / "kimi-desktop"
            touch(data / "logs/main.log", "[2020-01-02 03:04:05.000] [info] [SubscriptionManager]  refreshed(sub): "
                                          "level=10 isMember=false exhausted=false resetAt=2020-02-01T00:00:00Z\n")
            state = kimi.probe(env)
            self.assertTrue(state["stale"])
            self.assertFalse(state["signed_in"])
            self.assertEqual(state["headline"]["en"], "OK")
            self.assertTrue(state["subline"]["en"].startswith("As of"))

    def test_deepseek_aggregates_session_tokens_without_reading_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            env = FakeEnv(Path(directory))
            home = env.home / ".dsh"
            touch(home / ".credentials.yaml", f"llm-deepseek:\n  api_key: {SECRET}\n")
            sessions = home / "storages/session_projcache/sessions"
            for index, (tokens, turns) in enumerate([(1000, 2), (500, 0)]):
                touch(sessions / f"session-{index}.json", json.dumps({"version": 1, "record": {"rows": {
                    "tokenUsage": {"val": {"totals": {"uncachedInputTokens": tokens, "outputTokens": 10,
                                                      "cacheReadTokens": True, "cacheWriteTokens": -5}}},
                    "sessionStats": {"val": {"turns": turns}},
                    "sessionListMetadata": {"val": {"lastPromptAt": int(time.time() * 1000) if turns else None}}}}}))
            touch(sessions / "broken.json", "{")
            with patch.object(Path, "read_text", side_effect=AssertionError("unexpected read_text")):
                state = deepseek.probe(env)
            self.assertNotIn(SECRET, json.dumps(state))
            self.assertTrue(state["signed_in"])
            self.assertEqual(state["usage"]["total_tokens"], 1520)
            self.assertEqual((state["usage"]["sessions"], state["usage"]["active_sessions"]), (2, 1))
            self.assertEqual(state["headline"]["en"], "1.5k tok")
            self.assertFalse(state["quota_available"])
            self.assertEqual(deepseek.probe(env)["usage"], state["usage"])  # cache path

    def test_grok_status_file_and_stale_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            env = FakeEnv(Path(directory), processes=[Process(4242, "grok bot.exe")])
            touch(env.appdata / "Grok Bot/desktop-status.json",
                  json.dumps({"version": 1, "pid": 4242, "appVersion": "0.63.0", "startedAtMs": 1, "signedIn": True}))
            state = grok.probe(env)
            self.assertTrue(state["signed_in"] and state["running"] and state["installed"])
            self.assertEqual(state["version"], "0.63.0")
            env._processes = [Process(1, "explorer.exe")]
            state = grok.probe(env)
            self.assertFalse(state["running"])
            self.assertIn("Sign-in state is from the last run", [f["en"] for f in state["facts"]])

    def test_missing_apps_and_leftover_data_are_graceful(self):
        with tempfile.TemporaryDirectory() as directory:
            env = FakeEnv(Path(directory))
            (env.appdata / "Qwen").mkdir(parents=True)
            results = probe_all(env)
            self.assertEqual(set(results), set(PROVIDER_IDS))
            self.assertEqual(results["qwen"]["code"], "leftover_data")
            for provider_id in ("claude", "glm", "grok", "kimi", "deepseek"):
                self.assertFalse(results[provider_id]["installed"], provider_id)
                reading = provider_reading(results[provider_id], {"id": provider_id, "name": provider_id,
                                                                    "short": provider_id})
                self.assertTrue(reading["warning"])

    def test_one_failing_probe_does_not_hide_others(self):
        from vram_radar.providers.base import ProviderSpec
        def boom(env):
            raise RuntimeError(SECRET)
        specs = (ProviderSpec("grok", "Grok", "G", boom), PROVIDERS[3])
        with tempfile.TemporaryDirectory() as directory, self.assertLogs("vram_radar", "WARNING") as logs:
            results = probe_all(FakeEnv(Path(directory)), specs=specs)
        self.assertEqual(results["grok"]["code"], "probe_failed")
        self.assertIn("kimi", results)
        self.assertNotIn(SECRET, "\n".join(logs.output))

    def test_provider_reading_is_localized_and_tolerates_missing_state(self):
        spec = {"id": "kimi", "name": "Kimi", "short": "Kimi"}
        self.assertEqual(provider_reading(None, spec, "en")["countdown"], "Scanning")
        state = {"installed": True, "version": "3.2.4", "running": False, "signed_in": True, "last_used": 1.0,
                 "headline": {"zh": "可用", "en": "OK"}, "subline": {"zh": "记录 09-03", "en": "As of 09-03"},
                 "facts": [{"zh": "会员等级 10", "en": "Membership level 10"}], "stale": True, "state": "ready"}
        reading = provider_reading(state, spec, "zh-CN")
        self.assertEqual((reading["name"], reading["value"], reading["countdown"]), ("Kimi", "可用", "记录 09-03"))
        self.assertTrue(reading["warning"])
        self.assertIn("会员等级 10", reading["detail"])


class MonitorTests(unittest.TestCase):
    def test_background_probe_refresh_floor_and_close(self):
        calls = []
        done = threading.Event()
        def fake(**kwargs):
            calls.append(kwargs)
            done.set()
            return {"grok": {"id": "grok", "installed": True}}
        monitor = ProviderMonitor(probe=fake, interval=3600)
        try:
            self.assertEqual(monitor.snapshot(), {})
            monitor.configure(True, "")
            self.assertTrue(done.wait(5))
            deadline = time.monotonic() + 5
            while not monitor.snapshot() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(monitor.snapshot()["grok"]["installed"])
            snapshot = monitor.snapshot()
            snapshot["grok"]["installed"] = False
            self.assertTrue(monitor.snapshot()["grok"]["installed"])  # deep copy
            done.clear()
            monitor.refresh()
            self.assertTrue(done.wait(5))
            count = len(calls)
            monitor.refresh()  # inside the floor: coalesced
            time.sleep(0.2)
            self.assertEqual(len(calls), count)
        finally:
            monitor.close()
        self.assertFalse(monitor.worker.is_alive())


class ApiTests(unittest.TestCase):
    def test_save_usage_providers_persists_and_gates_codex(self):
        from vram_radar.shell import AppApi
        from vram_radar.storage import ProfileStore, storage_paths
        with tempfile.TemporaryDirectory() as directory:
            paths = storage_paths(Path(directory))
            profile = Profile.from_dict({**Profile.empty("test").to_dict(), "codex_usage_enabled": True})
            api = AppApi(profile, ProfileStore(paths), paths, Mock(), automatic_import_enabled=False)
            try:
                with patch.object(api._codex_usage, "configure") as configure, \
                        patch.object(api._usage_providers, "configure") as providers:
                    self.assertFalse(api.save_usage_providers("kimi")["ok"])
                    self.assertTrue(api.save_usage_providers(["kimi", "bogus"])["ok"])
                    self.assertEqual(api.store.load("test").usage_providers, ("kimi",))
                    configure.assert_called_with(False, "")  # Codex deselected: its app-server is not started
                    providers.assert_called_with(True, "", ("kimi",))
                    self.assertTrue(api.save_usage_providers(["codex", "kimi"])["ok"])
                    configure.assert_called_with(True, "")
                overview = api.get_usage_providers()
                self.assertEqual(overview["selected"], ["codex", "kimi"])
                self.assertEqual([item["id"] for item in overview["registry"]], list(PROVIDER_IDS))
            finally:
                api._codex_usage.close()
                api._usage_providers.close()



class SessionConsentTests(unittest.TestCase):
    def tearDown(self):
        from vram_radar import providers
        providers.set_session_consent(())

    def test_capability_flag_only_on_session_providers(self):
        from vram_radar.providers import PROVIDERS, SESSION_CONSENT_IDS
        self.assertEqual(SESSION_CONSENT_IDS, ("grok", "kimi"))
        flags = {spec.id: spec.needs_session_consent for spec in PROVIDERS}
        self.assertFalse(flags["codex"] or flags["deepseek"] or flags["claude"])
        grok_spec = next(spec for spec in PROVIDERS if spec.id == "grok")
        self.assertTrue(grok_spec.session_app and grok_spec.session_server)

    def test_migration_default_is_no_consent_and_roundtrips(self):
        legacy = Profile.empty("test").to_dict()
        legacy.pop("usage_session_consent", None)
        self.assertEqual(Profile.from_dict(legacy).usage_session_consent, ())
        for bad in ("grok", 3, None, {"grok": "yes"}, {"grok": 1}, {"codex": True, "future": True}):
            self.assertEqual(Profile.from_dict({**legacy, "usage_session_consent": bad}).usage_session_consent, ())
        profile = Profile.from_dict({**legacy, "usage_session_consent": {"kimi": True, "grok": True, "bogus": True}})
        self.assertEqual(profile.usage_session_consent, ("grok", "kimi"))
        self.assertEqual(profile.to_dict()["usage_session_consent"], {"grok": True, "kimi": True})
        self.assertEqual(Profile.from_dict(profile.to_dict()), profile)
        revoked = Profile.from_dict({**legacy, "usage_session_consent": {"grok": False, "kimi": True}})
        self.assertEqual(revoked.usage_session_consent, ("kimi",))

    def test_api_persists_grant_and_revoke_and_exposes_to_providers(self):
        from vram_radar import providers
        from vram_radar.shell import AppApi
        from vram_radar.storage import ProfileStore, storage_paths
        with tempfile.TemporaryDirectory() as directory:
            paths = storage_paths(Path(directory))
            profile = Profile.from_dict({**Profile.empty("test").to_dict(), "codex_usage_enabled": True})
            api = AppApi(profile, ProfileStore(paths), paths, Mock(), automatic_import_enabled=False)
            try:
                with patch.object(api._codex_usage, "configure"), patch.object(api._usage_providers, "configure"):
                    self.assertFalse(providers.session_consent("grok"))
                    self.assertFalse(api.save_usage_session_consent("codex", True)["ok"])
                    self.assertFalse(api.save_usage_session_consent("grok", "yes")["ok"])
                    self.assertTrue(api.save_usage_session_consent("grok", True)["ok"])
                    self.assertTrue(api.save_usage_session_consent("kimi", True)["ok"])
                    self.assertEqual(api.store.load("test").usage_session_consent, ("grok", "kimi"))
                    self.assertTrue(providers.session_consent("grok"))
                    # Other preference saves keep the consent map.
                    self.assertTrue(api.save_usage_providers(["codex", "grok"])["ok"])
                    self.assertEqual(api.store.load("test").usage_session_consent, ("grok", "kimi"))
                    self.assertTrue(api.save_usage_session_consent("grok", False)["ok"])
                    self.assertEqual(api.store.load("test").usage_session_consent, ("kimi",))
                    self.assertFalse(providers.session_consent("grok"))
                    self.assertTrue(providers.session_consent("kimi"))
                overview = api.get_usage_providers()
                self.assertEqual(overview["session_consent"], ["kimi"])
                capable = [item["id"] for item in overview["registry"] if item["needs_session_consent"]]
                self.assertEqual(capable, ["grok", "kimi"])
            finally:
                api._codex_usage.close()
                api._usage_providers.close()


if __name__ == "__main__":
    unittest.main()
