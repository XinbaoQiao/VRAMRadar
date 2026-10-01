"""macOS-neutrality checks, run with ``sys.platform`` mocked as "darwin".

Each case runs in a fresh interpreter where ``sys.platform = "darwin"`` is
set before any vram_radar import and Windows-only modules (winreg, clr,
ctypes.windll/WinDLL) are made unavailable, so an unguarded Windows probe
fails loudly.  This verifies imports/probes only -- not any macOS UI.
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")

PRELUDE = textwrap.dedent("""
    import sys, ctypes
    # stdlib modules that branch on sys.platform at import time are loaded
    # first (urllib.request would look for macOS-only _scproxy).
    import urllib.request, ssl, subprocess, platform, json, pathlib, logging, tempfile, shutil, threading, decimal
    import asyncio, http.client, email, mimetypes, uuid, sqlite3, webbrowser
    sys.platform = "darwin"
    for name in ("winreg", "_winreg", "clr", "pythonnet"):
        sys.modules[name] = None          # ImportError on import
    for attr in ("windll", "WinDLL", "oledll", "OleDLL"):
        if hasattr(ctypes, attr):
            delattr(ctypes, attr)
    sys.path.insert(0, {src!r})
""")


def run_darwin(body: str, home: str) -> dict:
    code = PRELUDE.format(src=os.path.abspath(os.path.join(ROOT, "src"))) + textwrap.dedent(body)
    # A Mac has no Windows folder variables; drop them so probes cannot find
    # this machine's real Windows installs.
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in {"APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMW6432", "PROGRAMFILES(X86)"}}
    env.update(HOME=home, USERPROFILE=home)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120)
    if out.returncode != 0:
        raise AssertionError(out.stderr[-2000:])
    return json.loads(out.stdout.strip().splitlines()[-1])


class DarwinNeutralityTests(unittest.TestCase):
    def test_registry_and_probes_do_not_touch_windows_apis(self):
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, "Applications", "Kimi.app"))
            os.makedirs(os.path.join(home, "Applications", "Grok.app"))
            result = run_darwin("""
                import json
                from pathlib import Path
                from vram_radar import providers
                from vram_radar.providers.base import Environment, list_msix_packages, list_processes, list_uninstall_entries
                env = Environment(); env.home = Path.home()
                states = {}
                for mod in (getattr(providers, "codex"), providers.grok, providers.deepseek, providers.kimi):
                    st = mod.probe(env)
                    states[st["id"]] = {"installed": bool(st.get("installed")), "path": st.get("install_path") or ""}
                print(json.dumps({"states": states, "facts": [list_processes(), list_uninstall_entries(), list_msix_packages()]}))
            """, home)
        self.assertEqual(result["facts"], [[], [], []])
        self.assertTrue(result["states"]["kimi"]["installed"])
        self.assertTrue(result["states"]["kimi"]["path"].endswith("Kimi.app"))
        self.assertTrue(result["states"]["grok"]["path"].endswith("Grok.app"))
        self.assertFalse(result["states"]["deepseek"]["installed"])

    def test_profile_selection_and_labels_are_platform_neutral(self):
        with tempfile.TemporaryDirectory() as home:
            result = run_darwin("""
                import json
                from vram_radar.models import Profile, normalize_usage_labels
                from vram_radar.usage_surface import provider_reading, compact_value
                raw = Profile.empty("mac").to_dict()
                raw.pop("usage_labels")
                p = Profile.from_dict({**raw, "usage_icons": True})
                reading = provider_reading({"installed": True, "remaining": None}, {"id": "kimi", "name": "Kimi", "short": "Kimi"}, "zh-CN")
                print(json.dumps({"labels": p.usage_labels, "reading_name": reading["name"], "compact": compact_value("48.3h", 2)}))
            """, home)
        self.assertEqual(result["labels"], "icons")
        self.assertEqual(result["reading_name"], "Kimi")

    def test_session_token_readers_are_off_on_mac(self):
        with tempfile.TemporaryDirectory() as home:
            result = run_darwin("""
                import json
                from vram_radar.providers import grok_usage
                fn = grok_usage._dpapi_unprotect
                print(json.dumps({"dpapi": None if fn is None else fn(b"x")}))
            """, home)
        self.assertIsNone(result["dpapi"])


    def test_app_modules_import_without_windows_apis(self):
        with tempfile.TemporaryDirectory() as home:
            result = run_darwin("""
                import json, importlib
                out = {}
                for m in ("vram_radar.usage_surface", "vram_radar.ui_dialogs", "vram_radar.tray",
                          "vram_radar.usage_monitor", "vram_radar.shell"):
                    importlib.import_module(m); out[m] = True
                print(json.dumps(out))
            """, home)
        self.assertEqual(len(result), 5)


if __name__ == "__main__":
    unittest.main()