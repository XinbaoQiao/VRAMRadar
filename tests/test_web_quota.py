"""The web UI uses the same quota colours and countdown wording as the strip."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import gen_quota_gradient  # noqa: E402
from vram_radar.reset_format import reset_short  # noqa: E402
from test_quota_colors import Countdown  # noqa: E402


def js_function(source, name):
    start = source.index(f"function {name}(")
    return source[start:source.index("\n}\n", start) + 2]


class WebQuota(unittest.TestCase):
    def test_gradient_table_is_current(self):
        text = (ROOT / "src/vram_radar/web/app.js").read_text(encoding="utf-8")
        self.assertIn(gen_quota_gradient.block(), text)
        self.assertIn("--quota-color", (ROOT / "src/vram_radar/web/app.css").read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_countdown_matches_python(self):
        source = (ROOT / "src/vram_radar/web/app.js").read_text(encoding="utf-8")
        seconds = [s for s, _, _ in Countdown.CASES]
        script = js_function(source, "resetCountdown") + (
            f"\nconst s = {json.dumps(seconds)};\n"
            "console.log(JSON.stringify(s.map(x => [resetCountdown(x, true), resetCountdown(x, false)])));")
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr[:200])
        self.assertEqual(json.loads(out.stdout), [[reset_short(s, True), reset_short(s)] for s in seconds])


if __name__ == "__main__":
    unittest.main()