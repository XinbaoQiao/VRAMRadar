"""The packaged-smoke timeout report says whether the app or a descendant held the pipes."""
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import validate_macos_bundle as bundle


@unittest.skipIf(sys.platform == "win32", "POSIX process tree")
class SmokeTimeoutDiagnosticsTests(unittest.TestCase):
    def run_fake(self, script: str) -> str:
        with tempfile.TemporaryDirectory() as folder:
            fake = Path(folder) / "VRAMRadar"
            fake.write_text("#!/bin/sh\n" + script, encoding="utf-8")
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            with mock.patch.object(bundle, "EXECUTABLE", fake), self.assertRaises(RuntimeError) as caught:
                bundle.run_bundle_smoke(Path(folder), "--gui-update-smoke", timeout=1.5)
            return str(caught.exception)

    def test_exited_app_with_descendant_holding_the_pipes(self):
        message = self.run_fake("sleep 30 &\nexit 0\n")
        self.assertIn("app=exited 0", message)
        self.assertIn("sleep", message)  # the helper still in the smoke group

    def test_app_still_running(self):
        message = self.run_fake("exec sleep 30\n")
        self.assertIn("app=running", message)


if __name__ == "__main__":
    unittest.main()
