import base64, json, tempfile, unittest
from pathlib import Path
from unittest import mock

from vram_radar import providers
from vram_radar.providers import grok_usage, kimi, kimi_usage


def jwt(exp):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'HS256'})}.{enc({'exp': exp, 'typ': 'access'})}.sig"


def isolate_last(test):
    """Point the persisted Kimi reading at a temp file for one test."""
    folder = tempfile.TemporaryDirectory()
    test.addCleanup(folder.cleanup)
    path = Path(folder.name) / "kimi-quota.json"
    patcher = mock.patch.object(kimi, "_last_path", lambda: path)
    patcher.start()
    test.addCleanup(patcher.stop)
    kimi.LAST.update(reading=None, at=None, loaded=False)
    test.addCleanup(lambda: kimi.LAST.update(reading=None, at=None, loaded=False))
    return path


class KimiSessionTests(unittest.TestCase):
    def setUp(self):
        isolate_last(self)
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
        self.assertEqual(result["reason"], "access_expired")   # login (refresh exp) still valid
        self.assertTrue(result["login_valid"])
        post.assert_not_called()

    def test_expired_login_is_relogin(self):
        plain = json.dumps({"tokens": {"access_token": jwt(1_000), "refresh_token": jwt(1_500)}})
        with mock.patch.multiple(grok_usage, read_os_crypt_key=mock.Mock(return_value=b"k" * 32),
                                 _decrypt_v10=mock.Mock(return_value=plain)):
            result = kimi_usage.fetch_usage(self.root, post=mock.Mock(), clock=lambda: 2_000)
        self.assertEqual((result["reason"], result["login_valid"]), ("expired", False))
        out = kimi.apply_session({"facts": []}, result, now=2_000)
        self.assertEqual(out["headline"]["zh"], "需登录")

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
        self.assertEqual(out["headline"]["zh"], "需登录")
        self.assertEqual(out["brief"]["zh"], "需重新登录（打开 Kimi 一次）")
        ok = kimi.apply_session({"facts": []}, {"status": "ok", "exhausted": False, "reset_at": 7200 + 100},
                                now=100)
        self.assertNotIn("quota", ok)          # no share known: reset only
        self.assertEqual(ok["reset_at"], 7300)
        self.assertEqual(ok["subline"]["zh"], "自动读取")
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


REAL_SUB = {"subscription": {"subscriptionId": "secret-id", "goods": {"id": "gid", "title": "Adagio",
            "membershipLevel": "LEVEL_FREE"}, "currentEndTime": "2026-10-03T03:22:15.544045Z", "active": True},
            "balances": [{"id": "bid", "feature": "FEATURE_OMNI", "unit": "UNIT_CREDIT", "amountUsedRatio": 0,
                          "expireTime": "2026-10-03T03:22:15.544045Z"}]}
REAL_STATS = {"subscriptionBalance": {"id": "bid", "feature": "FEATURE_OMNI", "unit": "UNIT_CREDIT",
                                      "amountUsedRatio": 0.25, "expireTime": "2026-10-03T03:22:15.544045Z"}}


class KimiParserTests(unittest.TestCase):
    def setUp(self):
        self.last_path = isolate_last(self)

    def test_last_reading_survives_restart_until_reset(self):
        reading = kimi_usage.parse_usage(REAL_SUB, REAL_STATS)
        kimi.apply_session({"facts": []}, reading, now=1_000)
        saved = json.loads(self.last_path.read_text(encoding="utf-8"))
        self.assertEqual(set(saved["reading"]) - set(kimi._KEEP), set())
        self.assertNotIn("bid", self.last_path.read_text(encoding="utf-8"))
        kimi.LAST.update(reading=None, at=None, loaded=False)   # app restart
        out = kimi.apply_session({"facts": [], "running": True}, {"status": "unauthorized", "login_valid": True}, now=2_000)
        self.assertEqual(out["quota"]["zh"], "75%")
        kimi.LAST.update(reading=None, at=None, loaded=False)
        late = kimi.apply_session({"facts": [], "running": True}, {"status": "unauthorized", "login_valid": True}, now=1790997735 + 10)
        self.assertEqual(late["headline"]["zh"], "待刷新")   # quota window already reset

    def test_real_response_shape(self):
        parsed = kimi_usage.parse_usage(REAL_SUB, REAL_STATS)
        self.assertEqual(parsed["used_percent"], 25.0)   # stats balance wins
        self.assertEqual(parsed["plan"], "Adagio")
        self.assertIs(parsed["is_member"], False)
        self.assertAlmostEqual(parsed["reset_at"], 1790997735.5, delta=1)
        self.assertNotIn("secret-id", json.dumps(parsed))
        self.assertNotIn("bid", json.dumps(parsed))
        self.assertEqual(kimi_usage.parse_usage(REAL_SUB, {})["used_percent"], 0.0)
        self.assertIsNone(kimi_usage.parse_usage({}, {}))
        self.assertEqual(kimi_usage._used_percent(1), 100.0)
        self.assertEqual(kimi_usage._used_percent(37), 37.0)

    def test_session_beats_log_status_and_shows_quota(self):
        log_state = {"headline": {"zh": "可用", "en": "OK"}, "subline": {"zh": "记录 09-03", "en": ""},
                     "stale": True, "facts": []}
        reading = kimi_usage.parse_usage(REAL_SUB, REAL_STATS)
        now = 1790997735 - 36 * 3600
        out = kimi.apply_session(log_state, reading, now=now)
        self.assertEqual(out["quota"]["zh"], "75%")    # remaining share; reset is a separate item
        self.assertEqual(out["brief"]["zh"].split(" · ")[0], "已用 25%")
        self.assertAlmostEqual(out["reset_at"], 1790997735.5, delta=1)
        self.assertFalse(out["stale"])

    def test_access_expired_keeps_last_reading(self):
        reading = kimi_usage.parse_usage(REAL_SUB, REAL_STATS)
        kimi.apply_session({"facts": []}, reading, now=1_000)
        out = kimi.apply_session({"facts": [], "running": True}, {"status": "unauthorized", "login_valid": True}, now=2_000)
        self.assertEqual(out["quota"]["zh"], "75%")
        self.assertTrue(out["subline"]["zh"].startswith("读于"))
        kimi.LAST["reading"] = None
        self.last_path.unlink()
        out = kimi.apply_session({"facts": [], "running": True}, {"status": "unauthorized", "login_valid": True}, now=2_000)
        self.assertEqual(out["headline"]["zh"], "待刷新")

    def test_kimi_closed_access_expired_says_open_kimi(self):
        # 10-02: Kimi closed 23:53, access credential expired 00:07, login
        # valid -> strip showed the old reading in quiet grey for 17 h.
        reading = kimi_usage.parse_usage(REAL_SUB, REAL_STATS)
        kimi.apply_session({"facts": []}, reading, now=1_000)
        out = kimi.apply_session({"facts": [], "running": False, "reset_at": 5_000},
                                 {"status": "unauthorized", "login_valid": True}, now=60_000)
        self.assertEqual((out["quota"]["zh"], out["quota"]["en"]), ("\u5f00Kimi", "Open Kimi"))
        self.assertTrue(out["needs_action"])
        self.assertFalse(out["stale"])
        self.assertNotIn("reset_at", out)
        self.assertIn("75%", out["brief"]["en"])          # last value kept in the tooltip
        from vram_radar import usage_surface as us
        from vram_radar.quota_colors import quota_color, usage_color
        info = us.provider_reading(out, {"id": "kimi", "name": "Kimi", "short": "Kimi"}, "zh-CN", now=60_000)
        self.assertTrue(info["action"])
        self.assertEqual((info["quota"], info["reset"]), ("\u5f00Kimi", ""))
        self.assertEqual(quota_color(None, warning=True, action=True), usage_color(0))
        self.assertNotEqual(quota_color(None, warning=True, action=True), quota_color(None, warning=True))

    def test_relogin_shows_sign_in_in_attention_colour(self):
        out = kimi.apply_session({"facts": []}, {"status": "unauthorized", "login_valid": False}, now=2_000)
        self.assertTrue(out["needs_action"] and out["session_relogin"])
        self.assertEqual(out["quota"], out["headline"])

    def test_new_token_file_bypasses_backoff(self):
        sig = [(1, 1)]
        fetch = mock.Mock(return_value={"status": "unauthorized"})
        now = [0.0]
        cache = grok_usage.UsageCache(fetch, clock=lambda: now[0], signature=lambda root: sig[0])
        cache.get(Path(".")); now[0] = 10; cache.get(Path("."))
        self.assertEqual(fetch.call_count, 1)
        sig[0] = (2, 1)   # Kimi wrote a fresh token
        cache.get(Path("."))
        self.assertEqual(fetch.call_count, 2)


if __name__ == "__main__":
    unittest.main()