from pathlib import Path
import re
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]


class VersionContractTests(unittest.TestCase):
    def test_release_version_is_consistent_across_packaging_sources(self) -> None:
        version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        package_source = (ROOT / "src" / "vram_radar" / "__init__.py").read_text(encoding="utf-8")
        windows_info = (ROOT / "packaging" / "version_info.txt").read_text(encoding="utf-8")
        installer = (ROOT / "packaging" / "vram-radar.iss").read_text(encoding="utf-8")
        lock = (ROOT / "uv.lock").read_text(encoding="utf-8")

        self.assertIn(f'__version__ = "{version}"', package_source)
        self.assertIn(f"filevers=({', '.join(version.split('.'))}, 0)", windows_info)
        self.assertIn(f"prodvers=({', '.join(version.split('.'))}, 0)", windows_info)
        self.assertIn(f"StringStruct(u'FileVersion', u'{version}')", windows_info)
        self.assertIn(f"StringStruct(u'ProductVersion', u'{version}')", windows_info)
        self.assertIn(f'#define MyAppVersion "{version}"', installer)
        self.assertRegex(
            lock,
            rf'\[\[package\]\]\s+name = "vram-radar"\s+version = "{re.escape(version)}"',
        )
        self.assertTrue((ROOT / "docs" / f"release-notes-v{version}.md").is_file())


if __name__ == "__main__":
    unittest.main()
