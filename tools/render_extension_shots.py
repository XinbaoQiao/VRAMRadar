"""Headless screenshots of Settings → Extensions (quota monitoring switch on/off, zh/en).

``measure()`` renders the same scene with ``--dump-dom`` and returns the block's
geometry (gaps, alignment, padding) so spacing can be asserted in tests.

Uses the synthetic pywebview API (tools/web_ui_stub.js) in a headless
Chromium-family browser: no real hosts, no network, no visible window, no input.
"""
from __future__ import annotations

import json
import re
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
  setTimeout(writeExtensionMetrics, 400);
}, 1200));
function textBox(node) {
  const range = document.createRange();
  range.selectNodeContents(node);
  const rects = [...range.getClientRects()].filter((r) => r.width > 0);
  const all = range.getBoundingClientRect();
  return {first: rects[0] || all, all};
}
function writeExtensionMetrics() {
  let metrics;
  try { metrics = extensionMetrics(); } catch (error) { metrics = {error: String(error)}; }
  const out = document.createElement('pre');
  out.id = 'ext-metrics';
  out.hidden = true;
  out.textContent = JSON.stringify(metrics);
  document.body.append(out);
}
function extensionMetrics() {
  const body = document.querySelector('.quota-extension-body');
  const box = ui.codexEnabled.getBoundingClientRect();
  const title = textBox(document.querySelector('.quota-extension-row strong'));
  const desc = textBox(document.querySelector('.quota-extension-row small'));
  const status = textBox(document.getElementById('quota-usage-status'));
  const frame = body.getBoundingClientRect();
  const heading = textBox(document.getElementById('extensions-title')).first;
  const round = (value) => Math.round(value * 10) / 10;
  const metrics = {
    row_to_status_gap: round(status.first.top - desc.all.bottom),
    title_to_desc_gap: round(desc.first.top - title.all.bottom),
    title_to_desc_pitch: round(desc.first.top - title.first.top),
    status_left_minus_text_left: round(status.first.left - title.first.left),
    checkbox_center_minus_title_center: round((box.top + box.height / 2) - (title.first.top + title.first.height / 2)),
    checkbox_left_minus_heading_left: round(box.left - heading.left),
    top_padding: round(title.first.top - frame.top),
    bottom_padding: round(frame.bottom - status.all.bottom),
    title_font: getComputedStyle(document.querySelector('.quota-extension-row strong')).fontSize,
    desc_font: getComputedStyle(document.querySelector('.quota-extension-row small')).fontSize,
    status_font: getComputedStyle(document.getElementById('quota-usage-status')).fontSize,
    status_text: document.getElementById('quota-usage-status').textContent,
  };
  return metrics;
}
</script>
"""


def _command(browser: str, profile: Path, *extra: str) -> list[str]:
    return [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--disable-extensions", "--allow-file-access-from-files", f"--user-data-dir={profile}",
            "--hide-scrollbars", "--window-size=1100,760", "--virtual-time-budget=6000", *extra]


def _site(tmp: str) -> Path:
    index = prepare(Path(tmp))
    markup = index.read_text(encoding="utf-8")
    index.write_text(markup.replace("</body>", SCENE + "</body>"), encoding="utf-8")
    return index


def measure(browser: str | None = None) -> dict[str, dict] | None:
    """Geometry of the quota block for zh/en × on/off; ``None`` without a browser."""
    browser = find_browser(browser)
    if not browser:
        return None
    results: dict[str, dict] = {}
    with tempfile.TemporaryDirectory() as tmp:
        index = _site(tmp)
        for lang, tag in (("zh-CN", "zh"), ("en", "en")):
            for state in ("on", "off"):
                url = index.resolve().as_uri() + f"?lang={lang}&monitor={state}"
                try:
                    done = subprocess.run(_command(browser, Path(tmp) / f"m-{tag}{state}", "--dump-dom", url),
                                          capture_output=True, timeout=90,
                                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    output = done.stdout
                except subprocess.TimeoutExpired as exc:     # a stalled browser yields no metrics
                    output = exc.stdout or b""
                found = re.search(r'<pre id="ext-metrics" hidden="">(.*?)</pre>',
                                  output.decode("utf-8", "replace"), re.S)
                results[f"{tag}_{state}"] = json.loads(found.group(1).replace("&quot;", '"').replace("&amp;", "&")) if found else {}
    return results


def main(out: str) -> list[str]:
    browser = find_browser(None)
    if not browser:
        raise SystemExit("no headless Chromium-family browser found")
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    made = []
    with tempfile.TemporaryDirectory() as tmp:
        index = _site(tmp)
        for lang, tag in (("zh-CN", "zh"), ("en", "en")):
            for state in ("on", "off"):
                target = folder / f"extensions_{tag}_{state}.png"
                url = index.resolve().as_uri() + f"?lang={lang}&monitor={state}"
                subprocess.run(_command(browser, Path(tmp) / f"profile-{tag}{state}", f"--screenshot={target}", url),
                               capture_output=True, timeout=180,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if target.exists():
                    made.append(str(target))
    return made


if __name__ == "__main__":
    if sys.argv[1:2] == ["--measure"]:
        print(json.dumps(measure(), ensure_ascii=False, indent=1))
        raise SystemExit(0)
    for path in main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "build" / "extension_shots")):
        print(path)
