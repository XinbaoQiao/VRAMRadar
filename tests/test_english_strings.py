"""English UI mode must not show CJK text (U+3000-U+9FFF, U+FF00-U+FFEF)."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
CJK = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")


def english_values(obj, path=""):
    """Every "en" string inside provider states (pair() = {"zh", "en"})."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "en" and isinstance(value, str):
                yield path, value
            yield from english_values(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from english_values(value, f"{path}[{index}]")


class EnglishStringTests(unittest.TestCase):
    def test_dialog_notice_menu_texts(self):
        import check_english_ui
        bad = [t for t in check_english_ui.dialog_strings(True) if CJK.search(t)]
        self.assertEqual(bad, [])
        self.assertTrue(any(CJK.search(t) for t in check_english_ui.dialog_strings(False)))

    def test_provider_names_states_and_tooltip(self):
        from vram_radar.providers import PROVIDERS
        from vram_radar.providers.base import Environment
        from vram_radar.usage_surface import concise_tooltip, provider_reading
        names = [spec.label(True) for spec in PROVIDERS]
        self.assertEqual([n for n in names if CJK.search(n)], [])
        self.assertIn("Yuanbao", names)
        env, rows, bad = Environment(), [], []
        for spec in PROVIDERS:
            state = spec.probe(env)
            for variant in (state, {**state, "installed": False}, {**state, "signed_in": False}):
                bad += [(spec.id, p, v) for p, v in english_values(variant) if CJK.search(v)]
                row = provider_reading(variant, {"id": spec.id, "name": spec.label(True), "short": spec.label(True)}, "en")
                bad += [(spec.id, k, v) for k, v in row.items() if isinstance(v, str) and CJK.search(v)]
                rows.append(row)
        tip = concise_tooltip([], rows, "en")
        bad += [("tooltip", line) for line in tip.splitlines() if CJK.search(line)]
        self.assertEqual(bad, [])

    @unittest.skipUnless(sys.platform == "win32", "native strip/menu")
    def test_rendered_strip_and_menus_have_no_cjk(self):
        import check_english_ui
        out = check_english_ui.run("en")
        self.assertNotIn("error", out)
        self.assertGreater(out["strings"], 60)
        self.assertEqual(out["hits"], [])


if __name__ == "__main__":
    unittest.main()