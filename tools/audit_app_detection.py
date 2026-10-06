"""Read-only detection audit: which sources find each AI desktop app here.

Never launches anything.  Prints one JSON line per provider.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vram_radar.providers import DETECTION_SPECS  # noqa: E402
from vram_radar.providers.base import Environment, detect_install  # noqa: E402


def main() -> int:
    env = Environment()
    for provider_id, spec in DETECTION_SPECS.items():
        found = detect_install(env, **spec)
        print(json.dumps({"id": provider_id, "installed": found.installed, "source": found.source,
                          "sources": found.sources, "path": found.path,
                          "launch": [found.launch_kind, found.launch_target]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
