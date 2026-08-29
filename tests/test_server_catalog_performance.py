from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
from threading import Lock
import time
import unittest
from unittest.mock import patch

from vram_radar.server_catalog import (
    MAX_OPENSSH_FINGERPRINT_CACHE_ENTRIES,
    _OPENSSH_FINGERPRINT_CACHE,
    _OPENSSH_FINGERPRINT_CACHE_LOCK,
    _read_openssh_aliases,
    openssh_config_dependency_fingerprint,
)


class OpenSSHFingerprintCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        with _OPENSSH_FINGERPRINT_CACHE_LOCK:
            _OPENSSH_FINGERPRINT_CACHE.clear()

    def tearDown(self) -> None:
        with _OPENSSH_FINGERPRINT_CACHE_LOCK:
            _OPENSSH_FINGERPRINT_CACHE.clear()

    def test_repeated_fingerprints_read_dependency_graph_only_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fragments = root / "fragments"
            fragments.mkdir()
            for index in range(12):
                (fragments / f"gpu-{index:02d}.conf").write_text(
                    f"Host gpu-{index:02d}\n  HostName gpu-{index:02d}.example\n",
                    encoding="utf-8",
                )
            config = root / "config"
            config.write_text(
                f"Include {fragments.as_posix()}/*.conf\n",
                encoding="utf-8",
            )

            original_read_text = Path.read_text
            reads = 0

            def counted_read_text(path: Path, *args, **kwargs):
                nonlocal reads
                try:
                    path.resolve().relative_to(root.resolve())
                except ValueError:
                    pass
                else:
                    reads += 1
                return original_read_text(path, *args, **kwargs)

            with patch.object(Path, "read_text", counted_read_text):
                digests = [openssh_config_dependency_fingerprint(config) for _ in range(10)]

        self.assertEqual(len(set(digests)), 1)
        self.assertEqual(reads, 13)

    def test_changed_dependency_stat_invalidates_cached_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fragment = root / "fragment.conf"
            fragment.write_text("IdentityFile ~/.ssh/id_a\n", encoding="utf-8")
            config = root / "config"
            config.write_text(f"Host gpu\n  Include {fragment.as_posix()}\n", encoding="utf-8")
            before = openssh_config_dependency_fingerprint(config)

            fragment.write_text("IdentityFile ~/.ssh/id_longer_name\n", encoding="utf-8")
            after = openssh_config_dependency_fingerprint(config)

        self.assertNotEqual(before, after)

    def test_new_and_removed_wildcard_include_files_invalidate_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fragments = root / "conf.d"
            fragments.mkdir()
            first = fragments / "first.conf"
            second = fragments / "second.conf"
            first.write_text("Host first\n", encoding="utf-8")
            config = root / "config"
            config.write_text(
                f"Include {fragments.as_posix()}/*.conf\n",
                encoding="utf-8",
            )

            initial = openssh_config_dependency_fingerprint(config)
            second.write_text("Host second\n", encoding="utf-8")
            after_add = openssh_config_dependency_fingerprint(config)
            second.unlink()
            after_remove = openssh_config_dependency_fingerprint(config)

        self.assertNotEqual(initial, after_add)
        self.assertNotEqual(after_add, after_remove)
        self.assertEqual(initial, after_remove)

    def test_previously_missing_literal_include_is_detected_when_created(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fragment = root / "later.conf"
            config = root / "config"
            config.write_text(
                f"Include {fragment.as_posix()}\nHost base\n",
                encoding="utf-8",
            )

            before = openssh_config_dependency_fingerprint(config)
            fragment.write_text("Host later\n", encoding="utf-8")
            after = openssh_config_dependency_fingerprint(config)

        self.assertNotEqual(before, after)

    def test_environment_expansion_change_invalidates_include_watch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.conf"
            second = root / "second.conf"
            first.write_text("Host first\n", encoding="utf-8")
            second.write_text("Host second\n", encoding="utf-8")
            config = root / "config"
            config.write_text("Include $VRAM_RADAR_CACHE_TEST_INCLUDE\n", encoding="utf-8")

            with patch.dict(
                os.environ,
                {"VRAM_RADAR_CACHE_TEST_INCLUDE": str(first)},
                clear=False,
            ):
                before = openssh_config_dependency_fingerprint(config)
            with patch.dict(
                os.environ,
                {"VRAM_RADAR_CACHE_TEST_INCLUDE": str(second)},
                clear=False,
            ):
                after = openssh_config_dependency_fingerprint(config)

        self.assertNotEqual(before, after)

    def test_concurrent_callers_share_one_parse(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config"
            config.write_text("Host gpu\n  HostName gpu.example\n", encoding="utf-8")
            original_read = _read_openssh_aliases
            call_count = 0
            call_lock = Lock()

            def counted_read(*args, **kwargs):
                nonlocal call_count
                with call_lock:
                    call_count += 1
                time.sleep(0.02)
                return original_read(*args, **kwargs)

            with patch("vram_radar.server_catalog._read_openssh_aliases", side_effect=counted_read):
                with ThreadPoolExecutor(max_workers=8) as executor:
                    digests = list(
                        executor.map(
                            openssh_config_dependency_fingerprint,
                            [config] * 16,
                        )
                    )

        self.assertEqual(call_count, 1)
        self.assertEqual(len(set(digests)), 1)

    def test_cache_is_bounded_and_does_not_retain_config_text(self):
        sensitive_marker = "PRIVATE-MATERIAL-MUST-NOT-BE-CACHED"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(MAX_OPENSSH_FINGERPRINT_CACHE_ENTRIES + 5):
                config = root / f"config-{index}"
                config.write_text(
                    f"# {sensitive_marker}\nHost gpu-{index}\n",
                    encoding="utf-8",
                )
                openssh_config_dependency_fingerprint(config)
            with _OPENSSH_FINGERPRINT_CACHE_LOCK:
                cache_representation = repr(_OPENSSH_FINGERPRINT_CACHE)
                cache_size = len(_OPENSSH_FINGERPRINT_CACHE)

        self.assertEqual(cache_size, MAX_OPENSSH_FINGERPRINT_CACHE_ENTRIES)
        self.assertNotIn(sensitive_marker, cache_representation)


if __name__ == "__main__":
    unittest.main()
