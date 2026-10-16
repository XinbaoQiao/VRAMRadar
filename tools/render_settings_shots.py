"""Headless screenshots of reorganized Settings panels (zh/en)."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from check_english_web_ui import find_browser, prepare  # noqa: E402

SCENE_SERVERS = r"""
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

SCENE_MIGRATE = r"""
<script>
window.addEventListener('load', () => setTimeout(() => {
  const params = new URLSearchParams(location.search);
  const lang = params.get('lang') === 'en' ? 'en' : 'zh-CN';
  window.VRAMRadarI18n?.setLanguage(lang);
  currentProfile = {
    ...(currentProfile || {}),
    ui_language: lang,
    display_name: 'demo',
    servers: [
      {id: 'lab', display_name: 'Lab GPU', host: 'gpu.example.org'},
      {id: 'train', display_name: 'Train', host: 'train.example.org'},
    ],
  };
  window.chrome = window.chrome || {};
  window.chrome.webview = window.chrome.webview || {};
  const demoToken = 'VR1:eNqdj8FOwzAMhl8F-Vy6ptKY1mfgDRCKvMTtItKmstPCNJVnxxkc4MABbs6v35-_XMExYSZvMUNnDsfWPLT7w7GCPvFYMlgZR0aPfD-GgbW7Ww1UECYXF09ihRSRBboeo1AFM4q8JvbWpWVSQPMtkphKs0Sc-hAJuisEr1c89bjErGBxZxrRrsQS0qRSGhGXJ3RPVzihe6HpthKYXLYiZ93yQeaIFzvhqFB4xNNdo_E5SfnDMC9NTW84zpHqxEPxLwjhtbnNNOWQL_ZTCd53tVJ3wVvy7X5vjtpZVOILjjE4gq36i4z5IWN-kTH_lnnetg8OYZnm';
  api = {
    ...(api || {}),
    list_migrate_export_targets: async () => ({
      ok: true,
      servers: [
        {id: 'lab', display_name: 'Lab GPU', host: 'gpu.example.org'},
        {id: 'train', display_name: 'Train', host: 'train.example.org'},
      ],
    }),
    export_migrate_bundle: async () => ({
      ok: true,
      token: demoToken,
      qr_eligible: true,
      file_text: '{}',
      file_name: 'demo.vramradar-migrate',
      preview: {display_name: 'demo', server_count: 2, includes_secrets: false, includes_preferences: true},
      includes_secrets: false,
    }),
    import_migrate_bundle: async () => ({ok: false, error: 'shot'}),
    preview_migrate_payload: async () => ({ok: true, needs_passphrase: false, preview: {display_name: 'demo', server_count: 2}}),
    start_lan_migrate_transfer: async () => ({ok: false, error: 'shot'}),
    lan_migrate_transfer_status: async () => ({ok: true, transfer: {active: false}}),
    stop_lan_migrate_transfer: async () => ({ok: true, transfer: {active: false}}),
    fetch_lan_migrate_payload: async () => ({ok: false, error: 'shot'}),
  };
  openSettings({forceNormal: true});
  if (typeof showSettingsPane === 'function') showSettingsPane('migrate', {scroll: false});
  setTimeout(async () => {
    if (typeof loadMigrateExportTargets === 'function') await loadMigrateExportTargets();
    if (typeof drawMigrateQr === 'function') {
      const drew = drawMigrateQr(demoToken);
      if (typeof lastMigrateQrReady !== 'undefined') lastMigrateQrReady = drew;
      const wrap = document.getElementById('migrate-qr-wrap');
      if (wrap) wrap.hidden = !drew;
    }
    const status = document.getElementById('migrate-export-status');
    const fileBtn = document.getElementById('migrate-export-file');
    if (status) { status.hidden = true; status.textContent = ''; status.classList.remove('is-error'); }
    if (fileBtn) fileBtn.hidden = true;
    if (typeof syncMigrateSecretFields === 'function') syncMigrateSecretFields();
    if (typeof updateMigrateSummary === 'function') {
      updateMigrateSummary({display_name: 'demo', server_count: 2, includes_secrets: false, includes_preferences: true});
    }
    window.VRAMRadarI18n?.localizeTree?.(document.body);
  }, 500);
  const stack = document.getElementById('profile-settings');
  stack?.scrollIntoView({block: 'start'});
}, 250));
</script>
"""

SCENE_MIGRATE_SECRETS = r"""
<script>
window.addEventListener('load', () => setTimeout(() => {
  const params = new URLSearchParams(location.search);
  const lang = params.get('lang') === 'en' ? 'en' : 'zh-CN';
  window.VRAMRadarI18n?.setLanguage(lang);
  currentProfile = {
    ...(currentProfile || {}),
    ui_language: lang,
    servers: currentProfile?.servers || [{id: 'lab', display_name: 'lab', host: 'gpu.example.org'}],
  };
  window.chrome = window.chrome || {};
  window.chrome.webview = window.chrome.webview || {};
  const demoToken = 'VR1:eNqdj8FOwzAMhl8F-Vy6ptKY1mfgDRCKvMTtItKmstPCNJVnxxkc4MABbs6v35-_XMExYSZvMUNnDsfWPLT7w7GCPvFYMlgZR0aPfD-GgbW7Ww1UECYXF09ihRSRBboeo1AFM4q8JvbWpWVSQPMtkphKs0Sc-hAJuisEr1c89bjErGBxZxrRrsQS0qRSGhGXJ3RPVzihe6HpthKYXLYiZ93yQeaIFzvhqFB4xNNdo_E5SfnDMC9NTW84zpHqxEPxLwjhtbnNNOWQL_ZTCd53tVJ3wVvy7X5vjtpZVOILjjE4gq36i4z5IWN-kTH_lnnetg8OYZnm';
  api = {
    ...(api || {}),
    export_migrate_bundle: async () => ({
      ok: true,
      token: demoToken,
      qr_eligible: true,
      file_text: '{}',
      file_name: 'demo.vramradar-migrate',
      preview: {display_name: 'demo', server_count: 2, includes_secrets: true},
      includes_secrets: true,
    }),
    import_migrate_bundle: async () => ({ok: false, error: 'shot'}),
    preview_migrate_payload: async () => ({ok: true, needs_passphrase: true}),
  };
  openSettings({forceNormal: true});
  if (typeof showSettingsPane === 'function') showSettingsPane('migrate', {scroll: false});
  setTimeout(() => {
    const include = document.getElementById('migrate-include-secrets');
    const pass = document.getElementById('migrate-passphrase');
    const confirm = document.getElementById('migrate-passphrase-confirm');
    if (include) include.checked = true;
    if (pass) pass.value = 'demo-pass-word';
    if (confirm) confirm.value = 'demo-pass-word';
    if (typeof syncMigrateSecretFields === 'function') syncMigrateSecretFields();
    if (typeof updateMigrateSummary === 'function') {
      updateMigrateSummary({display_name: 'demo', server_count: 2, includes_secrets: true});
    }
    if (typeof drawMigrateQr === 'function') {
      const drew = drawMigrateQr(demoToken);
      if (typeof lastMigrateQrReady !== 'undefined') lastMigrateQrReady = drew;
      const wrap = document.getElementById('migrate-qr-wrap');
      if (wrap) wrap.hidden = !drew;
    }
    const status = document.getElementById('migrate-export-status');
    if (status) { status.hidden = true; status.textContent = ''; }
    const fileBtn = document.getElementById('migrate-export-file');
    if (fileBtn) fileBtn.hidden = true;
    window.VRAMRadarI18n?.localizeTree?.(document.body);
  }, 400);
  document.getElementById('profile-settings')?.scrollIntoView({block: 'start'});
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
        for scene_name, scene in (("servers", SCENE_SERVERS), ("migrate", SCENE_MIGRATE), ("migrate_secrets", SCENE_MIGRATE_SECRETS)):
            index = prepare(Path(tmp) / scene_name)
            markup = index.read_text(encoding="utf-8")
            index.write_text(markup.replace("</body>", scene + "</body>"), encoding="utf-8")
            for lang, tag in (("zh-CN", "zh"), ("en", "en")):
                target = folder / (
                    f"settings_{tag}.png" if scene_name == "servers"
                    else f"settings_migrate_secrets_{tag}.png" if scene_name == "migrate_secrets"
                    else f"settings_migrate_{tag}.png"
                )
                if target.exists():
                    target.unlink()
                url = index.resolve().as_uri() + f"?lang={lang}"
                subprocess.run(
                    _command(browser, Path(tmp) / f"profile-{scene_name}-{tag}", f"--screenshot={target}", url),
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
