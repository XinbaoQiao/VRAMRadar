"""Headless screenshots of Settings → Extensions (quota monitoring switch on/off, zh/en).

Uses the synthetic pywebview API (tools/web_ui_stub.js) in a headless
Chromium-family browser: no real hosts, no network, no visible window, no input.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from check_english_web_ui import find_browser, prepare  # noqa: E402

SCENE = """
<script>
window.addEventListener('load', () => setTimeout(() => {
  const params = new URLSearchParams(location.search);
  const on = params.get('monitor') === 'on';
  const now = Date.now() / 1000;
  const providers = {codex: {installed: true}, kimi: {installed: true, fetched_at: now - 60},
                     grok: {installed: true}, deepseek: {installed: true}, glm: {installed: false}};
  api.get_usage_providers = async () => ({enabled: on, selected: ['codex', 'kimi', 'grok', 'deepseek'], providers});
  api.get_codex_usage = async () => ({enabled: on, state: on ? 'ready' : 'disabled', fetched_at: now, windows: []});
  currentProfile = {...currentProfile, codex_usage_enabled: on};
  openSettings({forceNormal: true});
  ui.extensionsSettings.open = true;
  ui.codexEnabled.checked = on;
  clearTimeout(quotaMonitorTimer);
  renderQuotaMonitor({providers, primary: {fetched_at: now}});
  ui.extensionsSettings.scrollIntoView({block: 'start'});
}, 1200));
</script>
"""


def main(out: str) -> list[str]:
    browser = find_browser(None)
    if not browser:
        raise SystemExit("no headless Chromium-family browser found")
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    made = []
    with tempfile.TemporaryDirectory() as tmp:
        index = prepare(Path(tmp))
        markup = index.read_text(encoding="utf-8")
        index.write_text(markup.replace("</body>", SCENE + "</body>"), encoding="utf-8")
        for lang, tag in (("zh-CN", "zh"), ("en", "en")):
            for state in ("on", "off"):
                target = folder / f"extensions_{tag}_{state}.png"
                url = index.resolve().as_uri() + f"?lang={lang}&monitor={state}"
                command = [browser, "--headless=new", "--disable-gpu", "--no-first-run",
                           "--no-default-browser-check", "--disable-extensions", "--allow-file-access-from-files",
                           f"--user-data-dir={Path(tmp) / ('profile-' + tag + state)}", "--hide-scrollbars",
                           "--window-size=1100,760", "--virtual-time-budget=6000",
                           f"--screenshot={target}", url]
                subprocess.run(command, capture_output=True, timeout=180,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if target.exists():
                    made.append(str(target))
    return made


if __name__ == "__main__":
    for path in main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "build" / "extension_shots")):
        print(path)
