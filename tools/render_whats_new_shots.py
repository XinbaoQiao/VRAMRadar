"""Headless What's New dialog previews (zh/en) for design review."""
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
  const notes = {
    show: true,
    version: '1.0.4',
    en: [
      'Pin the AI quota strip on the left.',
      'Show reset countdowns beside quotas.',
      'Safer one-click updates from GitHub.',
    ],
    zh: [
      '额度条可固定在任务栏左侧。',
      '额度旁显示重置倒计时。',
      '支持从 GitHub 安全一键更新。',
    ],
  };
  // Avoid competing settings/alias modals from the stub boot path.
  try { ui.dialog?.close?.(); } catch (_e) {}
  try { ui.aliasChoiceDialog?.close?.(); } catch (_e) {}
  whatsNewPromptedVersion = '';
  whatsNewActiveVersion = '';
  openWhatsNewDialog(notes);
}, 900));
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
        "--window-size=960,720",
        "--virtual-time-budget=7000",
        *extra,
    ]


def main(out: str) -> list[str]:
    browser = find_browser(None)
    if not browser:
        raise SystemExit("no headless Chromium-family browser found")
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    made: list[str] = []
    with tempfile.TemporaryDirectory(prefix="vram-radar-whats-new-") as tmp:
        index = prepare(Path(tmp))
        markup = index.read_text(encoding="utf-8")
        index.write_text(markup.replace("</body>", SCENE + "</body>"), encoding="utf-8")
        for lang, tag in (("zh-CN", "zh"), ("en", "en")):
            target = folder / f"whats_new_{tag}.png"
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
    out = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "build" / "whats_new_shots")
    for path in main(out):
        print(path)
