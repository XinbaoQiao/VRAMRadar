import os
import tempfile
import time
import unittest
from pathlib import Path

from vram_radar.housekeeping import prune_legacy_update_stages


def _stage(root: Path, name: str, age: float) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "VRAMRadar-Setup.exe").write_bytes(b"x")
    stamp = time.time() - age
    os.utime(folder, (stamp, stamp))
    return folder


class LegacyUpdateStageTests(unittest.TestCase):
    def test_prunes_old_stages_in_other_default_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy = base / "legacy" / "updates"
            old = _stage(legacy, "a" * 32, 5 * 86400)
            fresh = _stage(legacy, "b" * 32, 60)
            other = legacy / "keep-me"
            other.mkdir()
            self.assertEqual(prune_legacy_update_stages(base / "cache", legacy_root=legacy), 1)
            self.assertFalse(old.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(other.exists())

    def test_skips_when_default_cache_is_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            old = _stage(cache / "updates", "c" * 32, 5 * 86400)
            self.assertEqual(prune_legacy_update_stages(cache, legacy_root=cache / "updates"), 0)
            self.assertTrue(old.exists())


if __name__ == "__main__":
    unittest.main()
