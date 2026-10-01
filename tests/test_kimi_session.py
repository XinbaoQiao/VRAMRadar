import base64, json, tempfile, unittest
from pathlib import Path
from unittest import mock

from vram_radar import providers
from vram_radar.providers import grok_usage, kimi, kimi_usage


def jwt(exp):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'HS256'})}.{enc({'exp': exp, 'typ': 'access'})}.sig"


class KimiSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "bridge-store").mkdir()
        (self.root / "bridge-store" / "token-store.json").write_text(
            json.dumps({"encryption": "safeStorage.v1", "data": "djEwAAAA"}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def tokens(self, exp):
        plain = json.dumps({"origin": "x", "tokens": {"access_token": jwt(exp), "refresh_token": jwt(exp + 9e6)}})
        return mock.patch.multiple(grok_usage, read_os_crypt_key=mock.Mock(return_value=b"k" * 32),
                                   _decrypt_v10=mock.Mock(return_value=plain))

    def test_expired_token_is_relogin_without_network(self):
        post = mock.Mock()
        with self.tokens(1_000):
            result = kimi_usage.fetch_usage(self.root, post=post, clock=lambda: 2_000)
        self.assertEqual(result["status"], "unauthorized")
        self.assertEqual(result["reason"], "expired")
        post.assert_not_called()

    def test_valid_token_calls_only_the_two_read_only_methods(self):
        calls = []
        def post(method, token, backend):
            calls.append((method, backend))
            self.assertTrue(token.count(".") == 2)
            if method == "GetSubscription":
                return "ok", {"subscription": {"level": 10, "isMember": False, "omniRatio": 0,
                                               "exhausted": False, "resetAt": "2026-10-03T03:22:15Z"}}
            return "ok", {"stats": {"overdrawn": False, "sendBlocked": False, "secretish": "x"}}
        with self.tokens(10_000):
            result = kimi_usage.fetch_usage(self.root, post=post, clock=lambda: 2_000)
        self.assertEqual([m for m, _ in calls], ["GetSubscription", "GetSubscriptionStats"])
        self.assertTrue(all(b == "https://www.kimi.com" for _, b in calls))
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["exhausted"])
        self.assertIs(result["is_member"], False)
        self.assertNotIn("secretish", json.dumps(result))
        self.assertAlmostEqual(result["reset_at"], 1790997735, delta=1)

    def test_server_rejection_maps_to_unauthorized(self):
        with self.tokens(10_000):
            result = kimi_usage.fetch_usage(self.root, post=lambda *a: ("unauthorized", None), clock=lambda: 2_000)
        self.assertEqual(result["status"], "unauthorized")

    def test_missing_store_is_no_credential(self):
        (self.root / "bridge-store" / "token-store.json").unlink()
        self.assertEqual(kimi_usage.fetch_usage(self.root, post=mock.Mock())["status"], "no_credential")

    def test_refresh_token_never_returned(self):
        with self.tokens(10_000):
            token, exp = kimi_usage.read_access(self.root)
        self.assertEqual(exp, 10_000)
        self.assertIn("eyJ", token)
        self.assertNotIn(str(int(10_000 + 9e6)), str(exp))

    def test_apply_session_relogin_and_ok(self):
        state = {"headline": {"zh": "可用", "en": "OK"}, "facts": []}
        out = kimi.apply_session(state, {"status": "unauthorized", "reason": "expired", "expired_at": 1.0})
        self.assertEqual(out["headline"]["zh"], "需重新登录")
        self.assertEqual(out["brief"]["zh"], "需重新登录（打开 Kimi 一次）")
        ok = kimi.apply_session({"facts": []}, {"status": "ok", "exhausted": False, "reset_at": 7200 + 100},
                                now=100)
        self.assertEqual(ok["headline"]["zh"], "可用")
        self.assertEqual(ok["subline"]["zh"], "2.0h")
        self.assertEqual(ok["quota_source"], "session")

    def test_session_gated_by_consent_and_selection(self):
        spec = tuple(s for s in providers.PROVIDERS if s.id == "codex")
        providers.set_session_consent([])
        providers.probe_all(specs=spec, network=("kimi",))
        self.assertFalse(kimi.SESSION["enabled"])
        providers.set_session_consent(["kimi"])
        try:
            providers.probe_all(specs=spec, network=())
            self.assertFalse(kimi.SESSION["enabled"])
            providers.probe_all(specs=spec, network=("kimi",))
            self.assertTrue(kimi.SESSION["enabled"])
        finally:
            providers.set_session_consent([])
            kimi.SESSION["enabled"] = False

    def test_cache_polls_at_most_every_five_minutes_with_backoff(self):
        self.assertGreaterEqual(kimi.CACHE.interval, 300)
        now = [0.0]
        fetch = mock.Mock(return_value={"status": "error"})
        cache = grok_usage.UsageCache(fetch, clock=lambda: now[0], signature=lambda root: (1, 1))
        cache.get(self.root); now[0] = 299; cache.get(self.root)
        self.assertEqual(fetch.call_count, 1)
        now[0] = 599; cache.get(self.root)
        self.assertEqual(fetch.call_count, 1)  # backoff doubled the wait after an error
        self.assertEqual(kimi_usage.TIMEOUT_SECONDS, 10)


if __name__ == "__main__":
    unittest.main()