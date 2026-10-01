"""Live-quota sources, full-name strip cells, taskbar placement and theme."""
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from vram_radar.providers import PROVIDERS, deepseek, deepseek_balance as balance
from vram_radar.usage_surface import left_slot, provider_reading, taskbar_palette

TOKEN = "tok_" + "S" * 60
CREDENTIALS = f"""version: 1
records:
  client-connection/browser-session:
    kind: grant
    payload:
      version: 1
      secret: {"Q" * 43}
  deepseek-account-platform/default:
    kind: grant
    payload:
      version: 1
      token: {TOKEN}
      issuer: https://platform.deepseek.com
"""


def summary(normal="0E-16", bonus="6.0000000000000000"):
    return {"code": 0, "msg": "", "data": {"biz_code": 0, "biz_msg": "", "biz_data": {
        "normal_wallets": [{"currency": "USD", "balance": normal}],
        "bonus_wallets": [{"currency": "USD", "balance": "0"}, {"currency": "CNY", "balance": bonus}]}}}


class DeepSeekBalanceTests(unittest.TestCase):
    def home(self, text=CREDENTIALS):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        home = Path(directory.name)
        (home / ".credentials.yaml").write_text(text, encoding="utf-8")
        return home

    def test_reads_grant_record_only_for_allowed_issuer(self):
        kind, secret, origin = balance.read_grant(self.home())
        self.assertEqual((kind, secret, origin), ("grant", TOKEN, "https://platform.deepseek.com"))
        evil = CREDENTIALS.replace("https://platform.deepseek.com", "https://evil.example")
        self.assertIsNone(balance.read_grant(self.home(evil)))
        mock = CREDENTIALS.replace(TOKEN, "dsh_mock_abc")
        self.assertIsNone(balance.read_grant(self.home(mock)))
        self.assertIsNone(balance.read_grant(self.home("x" * (balance.MAX_CREDENTIAL_BYTES + 10))))

    def test_api_key_record_uses_public_balance_endpoint(self):
        text = "version: 1\nrecords:\n  llm-deepseek/default:\n    kind: api-key\n    key: sk-test\n"
        self.assertEqual(balance.read_grant(self.home(text)), ("api-key", "sk-test", "https://api.deepseek.com"))
        parsed = balance.parse_api_balance({"is_available": True, "balance_infos": [
            {"currency": "CNY", "total_balance": "110.00", "granted_balance": "10.00", "topped_up_balance": "100.00"}]})
        self.assertEqual(balance.totals(parsed["wallets"]), {"CNY": Decimal("110.00")})
        self.assertTrue(parsed["available"])

    def test_summary_envelope_is_strict_and_never_zero_on_failure(self):
        parsed = balance.parse_summary(summary())
        self.assertEqual(balance.totals(parsed["wallets"]), {"USD": Decimal(0), "CNY": Decimal(6)})
        for bad in (None, {}, {"code": 1}, {"code": 0, "data": {"biz_code": 3, "biz_data": {}}},
                    {"code": 0, "data": {"biz_code": 0, "biz_data": {"normal_wallets": [{"currency": "EUR", "balance": "1"}]}}},
                    {"code": 0, "data": {"biz_code": 0, "biz_data": {"normal_wallets": [{"currency": "CNY", "balance": "1e99"}]}}}):
            result = balance.parse_summary(bad)
            self.assertTrue(result is None or result["wallets"] == [], bad)

    def test_fetch_sends_token_only_to_issuer_via_get_and_never_returns_it(self):
        calls = []
        def fake_get(url, headers):
            calls.append((url, headers))
            return 200, summary()
        with patch.object(balance, "_get", fake_get):
            result = balance.fetch_balance(self.home())
        self.assertEqual(calls[0][0], "https://platform.deepseek.com/api/v0/users/get_user_summary")
        self.assertEqual(calls[0][1], {"x-dsh-auth-token": TOKEN})
        self.assertEqual(result["status"], "ok")
        self.assertNotIn(TOKEN, repr(result))
        self.assertNotIn("Q" * 43, repr(result))

    def test_unauthorized_rate_limit_and_network_outcomes(self):
        for status, payload, expected in [(401, None, "unauthorized"), (200, {"code": 40003}, "unauthorized"),
                                          (429, None, "rate_limited"), (None, None, "network"),
                                          (500, None, "unavailable")]:
            with patch.object(balance, "_get", return_value=(status, payload)):
                self.assertEqual(balance.fetch_balance(self.home())["status"], expected)
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(balance.fetch_balance(Path(empty))["status"], "no_credential")

    def test_redirects_are_refused(self):
        handler = balance._NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example"))

    def test_cache_polls_conservatively_backs_off_and_keeps_last_good(self):
        clock = [1000.0]
        results = [{"status": "ok", "wallets": [], "source": "platform"}, {"status": "network"}, {"status": "network"}]
        calls = []
        def fetch(home):
            calls.append(clock[0])
            return results[min(len(calls) - 1, len(results) - 1)]
        cache = balance.BalanceCache(fetch, interval=300, clock=lambda: clock[0])
        self.assertEqual(cache.get(Path("."), 1)["status"], "ok")
        clock[0] += 299
        cache.get(Path("."), 1)
        self.assertEqual(len(calls), 1)            # within the interval: no request
        clock[0] += 2
        view = cache.get(Path("."), 1)               # failure: last good value is kept
        self.assertEqual((view["status"], view["stale_error"]), ("ok", "network"))
        clock[0] += 300
        cache.get(Path("."), 1)
        self.assertEqual(len(calls), 2)            # backoff doubled the wait after a failure
        cache.get(Path("."), 2)                     # credential changed: immediate re-read
        self.assertEqual(len(calls), 3)

    def test_probe_shows_live_balance_only_when_enabled(self):
        from tests.test_providers import FakeEnv
        with tempfile.TemporaryDirectory() as directory:
            env = FakeEnv(Path(directory))
            home = env.home / ".dsh"
            home.mkdir(parents=True)
            (home / ".credentials.yaml").write_text(CREDENTIALS, encoding="utf-8")
            fake = balance.BalanceCache(lambda h: {"status": "ok", "source": "platform",
                                                   **balance.parse_summary(summary())})
            with patch.object(deepseek, "BALANCE", fake):
                deepseek.NETWORK["enabled"] = False
                off = deepseek.probe(env)
                deepseek.NETWORK["enabled"] = True
                try:
                    on = deepseek.probe(env)
                finally:
                    deepseek.NETWORK["enabled"] = False
        self.assertIsNone(off["balance"])
        self.assertEqual(on["headline"]["en"], "¥6")      # gift money counts as usable, no qualifier
        self.assertEqual(on["brief"]["zh"], "¥6（赠送余额）")
        self.assertIn("Granted ¥6.00", " ".join(f["en"] for f in on["facts"]))
        self.assertTrue(on["quota_available"])
        self.assertNotIn(TOKEN, repr(on))

    def test_probe_all_enables_network_only_for_selected_deepseek(self):
        from tests.test_providers import FakeEnv
        from vram_radar.providers import probe_all
        seen = []
        from vram_radar.providers.base import ProviderSpec
        def fake(env):
            seen.append(deepseek.NETWORK["enabled"])
            return {"id": "deepseek", "installed": False}
        specs = (ProviderSpec("deepseek", "DeepSeek", "DeepSeek", fake),)
        with tempfile.TemporaryDirectory() as directory:
            env = FakeEnv(Path(directory))
            probe_all(env, specs=specs, network=("codex",))
            probe_all(env, specs=specs, network=("codex", "deepseek"))
        deepseek.NETWORK["enabled"] = False
        self.assertEqual(seen[0], False)
        self.assertEqual(seen[-1], True)


class StripTests(unittest.TestCase):
    def test_every_provider_has_a_readable_full_name(self):
        for spec in PROVIDERS:
            self.assertGreaterEqual(len(spec.short), 4 if spec.short.isascii() else 2, spec)
            self.assertNotIn(spec.short, {"Cx", "DS", "Cl", "Qw", "YB"})
        reading = provider_reading({"installed": True, "headline": {"en": "¥6.00"}, "subline": {"en": "0 tok"},
                                    "state": "ready"}, {"id": "deepseek", "name": "DeepSeek", "short": "DeepSeek"}, "en")
        self.assertEqual((reading["name"], reading["value"]), ("DeepSeek", "¥6.00"))


class PlacementTests(unittest.TestCase):
    BAR = (0, 1528, 2560, 1600)

    def test_centered_taskbar_uses_gap_between_widgets_and_start(self):
        elements = {"WidgetsButton": (15, 1528, 81, 1600), "StartButton": (368, 1528, 436, 1600),
                    "SearchButton": (439, 1540, 768, 1588)}
        self.assertEqual(left_slot(self.BAR, elements, (200, 60), 6), (87, 1534))
        x, _ = left_slot(self.BAR, elements, (200, 60), 6)
        self.assertGreater(x, 81)
        self.assertLessEqual(x + 200, 368 - 6)

    def test_too_narrow_gap_or_left_aligned_layout_falls_back(self):
        elements = {"WidgetsButton": (15, 1528, 81, 1600), "StartButton": (368, 1528, 436, 1600)}
        self.assertIsNone(left_slot(self.BAR, elements, (300, 60), 6))
        left_aligned = {"StartButton": (0, 1528, 68, 1600), "WidgetsButton": (2200, 1528, 2266, 1600)}
        self.assertIsNone(left_slot(self.BAR, left_aligned, (120, 60), 6))
        self.assertIsNone(left_slot(self.BAR, {}, (120, 60), 6))

    def test_widgets_off_starts_at_taskbar_edge(self):
        self.assertEqual(left_slot(self.BAR, {"StartButton": (1100, 1528, 1168, 1600)}, (200, 60), 6), (6, 1534))

    def test_secondary_monitor_coordinates(self):
        bar = (2560, 1380, 5120, 1440)
        elements = {"WidgetsButton": (2570, 1380, 2620, 1440), "StartButton": (3600, 1380, 3650, 1440)}
        self.assertEqual(left_slot(bar, elements, (200, 50), 6)[0], 2626)


class ThemeTests(unittest.TestCase):
    @staticmethod
    def contrast(a, b):
        def lum(rgb):
            def channel(v):
                v /= 255
                return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
            r, g, b_ = map(channel, rgb)
            return 0.2126 * r + 0.7152 * g + 0.0722 * b_
        high, low = sorted((lum(a), lum(b)), reverse=True)
        return (high + 0.05) / (low + 0.05)

    def test_follows_light_dark_accent_and_sampled_pixels_with_contrast(self):
        light = taskbar_palette({"SystemUsesLightTheme": 1})
        dark = taskbar_palette({"SystemUsesLightTheme": 0})
        accent = taskbar_palette({"SystemUsesLightTheme": 0, "ColorPrevalence": 1}, accent=(0, 90, 158))
        sampled = taskbar_palette({"SystemUsesLightTheme": 1}, sampled=(214, 226, 240))
        self.assertTrue(light[3] and not dark[3])
        self.assertEqual(accent[0], (0, 90, 158))
        self.assertEqual(sampled[0], (214, 226, 240))
        for background, foreground, _hover, _bright in (light, dark, accent, sampled):
            self.assertGreaterEqual(self.contrast(background, foreground), 4.5)


if __name__ == "__main__":
    unittest.main()
