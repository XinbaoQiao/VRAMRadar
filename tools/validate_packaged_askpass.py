from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from vram_radar.askpass import PasswordBroker  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the packaged one-time SSH password helper")
    parser.add_argument(
        "--bundle",
        type=Path,
        default=PROJECT_ROOT / "dist" / "VRAMRadar",
        help="Windows VRAMRadar onedir bundle",
    )
    args = parser.parse_args()
    helper = args.bundle.resolve() / "VRAMRadarAskPass.exe"
    if not helper.is_file():
        raise SystemExit(f"packaged password helper is missing: {helper}")

    generated_value = secrets.token_urlsafe(48)
    with PasswordBroker(generated_value) as broker:
        environment = os.environ.copy()
        environment.update(broker.environment)
        if generated_value in repr(environment):
            raise SystemExit("password leaked into helper environment")
        completed = subprocess.run(
            [str(helper), "OpenSSH password prompt"],
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=10,
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    if completed.returncode != 0 or completed.stdout != generated_value or completed.stderr:
        raise SystemExit("packaged password helper failed its private loopback exchange")
    print("packaged askpass validation passed; secret was not printed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
