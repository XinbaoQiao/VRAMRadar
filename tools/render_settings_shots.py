"""Headless screenshots of reorganized Settings panels (zh/en)."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from check_english_web_ui import find_browser, prepare  # noqa: E402

SCENE = r"""
<script>
window.addEventListener('load', () => setTimeout(() => {
  const params = new URLSearchParams(location.search);
  const lang = params.get('lang') === 'en' ? 'en' : 'zh-CN';
  window.VRAMRadarI18n?.setLanguage(lang);
  currentProfile = {
    ...(currentProfile || {}),
    ui_language: lang,
    cache_cleanup_enabled: true,
    cache_cleanup_interval_hours: 24,
    favorite_alert_enabled: true,
    task_completion_alert_enabled: true,
    servers: currentProfile?.servers || [{id: 'lab', display_name: 'lab'}],
  };
  openSettings({forceNormal: true});
  if (typeof showSettingsPane === 'function') showSettingsPane('servers', {scroll: false});
  document.querySelectorAll('details.settings-group').forEach((node) => { node.open = false; });
  const stack = document.getElementById('profile-settings');
  stack?.scrollIntoView({block: 'start'});
  const dialogContent = document.querySelector('#settings-dialog .dialog-content');
  if (dialogContent) dialogContent.scrollTop = 0;
  window.VRAMRadarI18n?.localizeTree?.(document.body);
}, 1200));
</script>
"""


def _command(browser: str, profile: Path, *extra: str) -> list[str]:
    return [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--allow-file-access-from-files",
        f"--user-data-dir={profile}",
        "--hide-scrollbars",
        "--window-size=1100,900",
        "--virtual-time-budget=9000",
        *extra,
    ]


def main(out: str) -> list[str]:
    browser = find_browser(None)
    if not browser:
        raise SystemExit("no headless Chromium-family browser found")
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    made: list[str] = []
    with tempfile.TemporaryDirectory(prefix="vram-radar-settings-shots-") as tmp:
        index = prepare(Path(tmp))
        markup = index.read_text(encoding="utf-8")
        index.write_text(markup.replace("</body>", SCENE + "</body>"), encoding="utf-8")
        for lang, tag in (("zh-CN", "zh"), ("en", "en")):
            target = folder / f"settings_{tag}.png"
            if target.exists():
                target.unlink()
            url = index.resolve().as_uri() + f"?lang={lang}"
            subprocess.run(
                _command(browser, Path(tmp) / f"profile-{tag}", f"--screenshot={target}", url),
                capture_output=True,
                timeout=180,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if not target.exists():
                raise SystemExit(f"screenshot missing: {target}")
            made.append(str(target))
    return made


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "build" / "settings_shots")
    for path in main(out):
        print(path)
