import json
import logging
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from vram_radar.providers import grok, grok_usage, probe_all, set_session_consent
from vram_radar.providers.base import Environment

TOKEN = "sk" "-SECRET-the-token-must-never-be-logged-123456"


class ParseTests(unittest.TestCase):
    def test_parse_camel_and_snake_and_timestamp_forms(self):
        iso = grok_usage.parse_usage({"usagePercent": 42.5, "nextResetTimestampUtc": "2026-10-05T00:00:00Z"})
        self.assertEqual(iso["status"], "ok")
        self.assertEqual(iso["used_percent"], 42.5)
        self.assertTrue(iso["reset_at"] > 0)
        snake = grok_usage.parse_usage({"usage_percent": 0, "next_reset_timestamp_utc": {"seconds": 1760000000}})
        self.assertEqual((snake["used_percent"], snake["reset_at"]), (0.0, 1760000000.0))

    def test_parse_rejects_out_of_range_or_missing(self):
        self.assertIsNone(grok_usage.parse_usage({"usagePercent": 150}))
        self.assertIsNone(grok_usage.parse_usage({"usagePercent": -1}))
        self.assertIsNone(grok_usage.parse_usage({}))
        self.assertIsNone(grok_usage.parse_usage("nope"))

    def test_usage_with_no_reset_is_still_ok(self):
        out = grok_usage.parse_usage({"usagePercent": 10})
        self.assertEqual(out["status"], "ok")
        self.assertIsNone(out["reset_at"])


class TokenReadTests(unittest.TestCase):
    def _store(self, tmp: Path):
        (tmp / "Local State").write_text(json.dumps(
            {"os_crypt": {"encrypted_key": "REALKEYISDPAPIWRAPPED"}}), encoding="utf-8")
        accounts = {"active": "acc1", "accounts": {"acc1": {
            "cursor-access-token": "scoped:v1:" + "a" * 64 + ":CIPHERTEXTB64",
            "cursor-refresh-token": "scoped:v1:" + "b" * 64 + ":REFRESHMUSTNOTBEREAD"}}}
        (tmp / "sand-secrets.json").write_text(json.dumps({"cursor-accounts": json.dumps(accounts)}),
                                               encoding="utf-8")

    def test_reads_active_access_token_only(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            self._store(tmp)
            seen = []

            def fake_decrypt(key, stored):
                seen.append(stored)
                if "REFRESH" in stored:
                    raise AssertionError("refresh token must never be decrypted")
                return TOKEN

            with patch.object(grok_usage, "read_os_crypt_key", return_value=b"k" * 32), \
                    patch.object(grok_usage, "_decrypt_v10", side_effect=fake_decrypt):
                token = (grok_usage.read_access_token(tmp))
            self.assertEqual(token, TOKEN)
            # cursor-accounts envelope is plain JSON here, so only the access token is decrypted.
            self.assertTrue(all("REFRESH" not in s for s in seen))

    def test_missing_store_returns_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(grok_usage.read_access_token(Path(directory)))


class FetchTests(unittest.TestCase):
    def test_no_credential_short_circuits_before_any_http(self):
        calls = []
        with patch.object(grok_usage, "read_access_token", return_value=None), \
                patch.object(grok_usage, "_post_usage", side_effect=lambda *a: calls.append(a)):
            out = grok_usage.fetch_usage(Path("x"))
        self.assertEqual(out["status"], "no_credential")
        self.assertEqual(calls, [])

    def test_unauthorized_maps_from_401(self):
        import urllib.error
        err = urllib.error.HTTPError("u", 401, "unauth", {}, None)
        with patch.object(grok_usage, "read_access_token", return_value=TOKEN), \
                patch.object(grok_usage._OPENER, "open", side_effect=err):
            out = grok_usage.fetch_usage(Path("x"))
        self.assertEqual(out["status"], "unauthorized")

    def test_ok_parses_body_and_token_never_logged(self):
        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n): return json.dumps({"usagePercent": 73,
                                                  "nextResetTimestampUtc": "2026-10-05T00:00:00Z"}).encode()
        with self.assertLogs("vram_radar", level="DEBUG") as logs, \
                patch.object(grok_usage, "read_access_token", return_value=TOKEN), \
                patch.object(grok_usage._OPENER, "open", return_value=Resp()):
            logging.getLogger("vram_radar").debug("probing")  # ensure the context has >=1 record
            out = grok_usage.fetch_usage(Path("x"))
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["used_percent"], 73.0)
        self.assertFalse(any(TOKEN in m for m in logs.output))

    def test_request_is_readonly_post_with_bearer(self):
        captured = {}
        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n): return b'{"usagePercent": 1}'
        def fake_open(request, timeout=None):
            captured["url"] = request.full_url
            captured["data"] = request.data
            captured["auth"] = request.get_header("Authorization")
            return Resp()
        with patch.object(grok_usage, "read_access_token", return_value=TOKEN), \
                patch.object(grok_usage._OPENER, "open", side_effect=fake_open):
            grok_usage.fetch_usage(Path("x"))
        self.assertTrue(captured["url"].endswith("/aiserver.v1.DashboardService/GetSandUsageStatus"))
        self.assertEqual(captured["data"], b"{}")
        self.assertEqual(captured["auth"], f"Bearer {TOKEN}")


class CacheTests(unittest.TestCase):
    def test_throttles_and_backs_off(self):
        now = {"t": 1000.0}
        results = iter([{"status": "ok", "used_percent": 5, "reset_at": None},
                        {"status": "error"}])
        got = []
        def fake_fetch(userdata, backend=grok_usage.DEFAULT_BACKEND):
            v = next(results); got.append(v); return v
        cache = grok_usage.UsageCache(fetch=fake_fetch, clock=lambda: now["t"])
        with patch.object(grok_usage, "_signature", return_value=(1, 1)):
            a = cache.get(Path("x"))
            self.assertEqual(a["status"], "ok")
            now["t"] += 60                       # within MIN_INTERVAL: served from cache, no fetch
            b = cache.get(Path("x"))
            self.assertEqual(b["used_percent"], 5)
            self.assertEqual(len(got), 1)
            now["t"] += grok_usage.MIN_INTERVAL  # due again: fetch errors, last good is served stale
            c = cache.get(Path("x"))
            self.assertEqual(c["status"], "ok")
            self.assertEqual(c.get("stale_error"), "error")

    def test_signature_change_forces_refetch(self):
        now = {"t": 0.0}
        got = []
        def fake_fetch(userdata, backend=grok_usage.DEFAULT_BACKEND):
            got.append(1); return {"status": "unauthorized"}
        cache = grok_usage.UsageCache(fetch=fake_fetch, clock=lambda: now["t"])
        sig = {"v": (1, 1)}
        with patch.object(grok_usage, "_signature", side_effect=lambda p: sig["v"]):
            cache.get(Path("x"))
            cache.get(Path("x"))             # throttled
            self.assertEqual(len(got), 1)
            sig["v"] = (2, 2)                # token rotated / re-login
            cache.get(Path("x"))
            self.assertEqual(len(got), 2)


class ConsentGateTests(unittest.TestCase):
    def tearDown(self):
        set_session_consent(())
        grok.SESSION["enabled"] = False

    def test_probe_all_enables_session_only_with_consent_and_selection(self):
        env = Environment()
        with patch.object(grok, "probe", return_value={"id": "grok", "installed": False, "facts": []}), \
                patch("vram_radar.providers." "codex.probe", return_value={"id": "codex", "installed": False}):
            set_session_consent(())
            probe_all(env, specs=tuple(s for s in __import__("vram_radar.providers", fromlist=["PROVIDERS"]).PROVIDERS if s.id in ("grok",)), network=("grok",))
            self.assertFalse(grok.SESSION["enabled"])     # selected but no consent
            set_session_consent({"grok": True})
            probe_all(env, specs=tuple(s for s in __import__("vram_radar.providers", fromlist=["PROVIDERS"]).PROVIDERS if s.id in ("grok",)), network=())
            self.assertFalse(grok.SESSION["enabled"])     # consented but not selected
            probe_all(env, specs=tuple(s for s in __import__("vram_radar.providers", fromlist=["PROVIDERS"]).PROVIDERS if s.id in ("grok",)), network=("grok",))
            self.assertTrue(grok.SESSION["enabled"])      # both

    def test_probe_makes_no_network_call_without_consent(self):
        env = Environment()
        with patch.object(grok, "_session_usage") as session:
            grok.SESSION["enabled"] = False
            grok.probe(env)
            session.assert_not_called()


class ApplyTests(unittest.TestCase):
    def _state(self):
        return {"id": "grok", "facts": [], "headline": {}, "subline": {},
                "installed": True, "signed_in": True}

    def test_expired_shows_relogin_and_keeps_screen_fallback(self):
        state = grok._apply_session(self._state(), {"status": "unauthorized"})
        self.assertTrue(state["session_relogin"])
        self.assertEqual(state["session_quota"], "expired")
        joined = " ".join(f["zh"] for f in state["facts"])
        self.assertIn("重新登录", joined)
        # Screen overlay still wins because session isn't the primary source.
        merged = grok.overlay(state, {"seen_at": time.time(), "percent_used": 12, "reset_text": ""})
        self.assertEqual(merged["quota_source"], "screen")

    def test_ok_sets_strip_label_and_tooltip(self):
        state = grok._apply_session(self._state(), {"status": "ok", "used_percent": 66,
                                                    "reset_at": 1760000000})
        self.assertEqual(state["headline"]["zh"], "已用 66%")
        self.assertEqual(state["quota_source"], "session")
        self.assertIn("自动读取", state["brief"]["zh"])
        # A later screen reading does not clobber the consented session reading.
        kept = grok.overlay(state, None)
        self.assertEqual(kept["quota_source"], "session")


if __name__ == "__main__":
    unittest.main()
