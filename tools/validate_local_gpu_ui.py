"""Render local GPU settings and capacity with synthetic data and no SSH."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import threading
import time
import sys

from benchmark_webview_ui import FakeApi, _wait_until_ready

ROOT = Path(__file__).resolve().parents[1]

CHECKS = r"""(() => {
  const assertions = {};
  const local = {id:'local-5060',display_name:'本地 RTX 5060 · 仅轻量工作',backend:'local',enabled:true};
  acceptProfile({...currentProfile, servers:[local]});
  const sample = {...local,server_id:local.id,view_kind:'live-memory',
    connection:{state:'online',data_origin:'live',data_revision:1},total_gpus:1,
    total_vram_gib:7.96,free_vram_gib:7.2,
    gpus:[{gpu_index:'0',gpu_uuid:'GPU-synthetic',gpu_type:'NVIDIA GeForce RTX 5060 Laptop GPU',
      memory_total_gib:7.96,memory_used_gib:0.5,memory_free_gib:7.2,utilization_percent:11,temperature_c:42}],
    processes:{supported:false,active:[],warning:'本地模式仅采集显存、GPU 利用率和温度。'}};
  const card = document.createElement('div'); card.innerHTML = renderServer(sample);
  assertions.local_gpu_visible = card.textContent.includes('RTX 5060') && card.textContent.includes('7.96');
  assertions.no_ssh_actions = !card.querySelector('.copy-server-ssh,.open-terminal');
  assertions.no_remote_directory = !card.querySelector('.directory-module');
  assertions.local_startup_message = !renderConfiguring(sample).includes('SSH');
  settingsServerDrafts = [serverDraftFromValue(local)];
  ui.editorList.replaceChildren();
  const editor = addServerEditor(settingsServerDrafts[0], {draftIndex:0});
  const found = editor || ui.editorList.querySelector('.server-editor');
  assertions.local_choice_selected = found.querySelector('[data-field="backend"]').value === 'local';
  assertions.no_ssh_address_required = invalidServerDraft() === null;
  assertions.ssh_address_hidden = found.querySelector('[data-field="ssh_alias"]').closest('label').hidden;
  assertions.key_setup_hidden = found.querySelector('.ssh-key-setup').hidden;
  const saved = collectProfile().servers[0];
  assertions.local_survives_save = saved.backend === 'local' && !saved.host && !saved.ssh_alias && !saved.auto_detect_backend;
  const backend = found.querySelector('[data-field="backend"]');
  backend.value = 'slurm_ssh'; backend.dispatchEvent(new Event('change'));
  assertions.slurm_privacy_still_visible = !found.querySelector('.server-command-setting').hidden;
  assertions.slurm_address_visible = !found.querySelector('[data-field="ssh_alias"]').closest('label').hidden;
  assertions.slurm_environment_visible = [...found.querySelectorAll('[data-slurm-environment]')].every(field => !field.hidden);
  backend.value = 'direct_ssh'; backend.dispatchEvent(new Event('change'));
  assertions.direct_ssh_privacy_still_visible = !found.querySelector('.server-command-setting').hidden;
  const now = new Date().toISOString();
  render({servers:[sample],notices:[],fetched_at:now,
    profile:{refresh_seconds:60},monitoring:{revision:20,in_flight:false,paused:false,data_updated_at:now},
    summary:{revision:20,free_vram_gib:7.2,total_vram_gib:7.96,total_servers:1,online_servers:1,total_gpus:1}});
  if (ui.dialog.open) ui.dialog.close();
  ui.list.querySelector('.server-card').scrollIntoView({block:'start'});
  return JSON.stringify({ok:Object.values(assertions).every(Boolean),assertions,synthetic_only:true,remote_connections:0});
})()"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'work/local-gpu-ui')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    import webview
    api = FakeApi()
    # Expose both remote actions to prove the local card hides them.
    api.get_ssh_command = lambda _server_id: {'ok': False}
    api.open_terminal = lambda _server_id: {'ok': False}
    window = webview.create_window('Synthetic local GPU validation',
        url=(ROOT / 'src/vram_radar/web/index.html').as_uri(), js_api=api,
        hidden=True, focus=False, width=1100, height=800)
    result = {'ok': False}
    timer = threading.Timer(45, window.destroy)

    def run():
        try:
            _wait_until_ready(window, time.monotonic() + 40)
            raw = window.evaluate_js(CHECKS)
            result.update(json.loads(raw) if isinstance(raw, str) else raw)
            if sys.platform == 'win32':
                from System import Action
                from System.IO import FileStream, FileMode, FileAccess
                from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat
                window.show()
                time.sleep(0.4)
                window.evaluate_js("document.querySelector('.server-card').scrollIntoView({block:'center',behavior:'instant'});")
                time.sleep(0.2)
                stream = FileStream(str((args.output / 'local-gpu-preview.png').resolve()),
                                    FileMode.Create, FileAccess.Write)
                try:
                    tasks = []
                    def capture():
                        tasks.append(window.native.webview.CoreWebView2.CapturePreviewAsync(
                            CoreWebView2CapturePreviewImageFormat.Png, stream))
                    window.native.Invoke(Action(capture))
                    if not tasks[0].Wait(10000):
                        raise TimeoutError('local GPU preview capture timed out')
                    result['screenshot'] = 'local-gpu-preview.png'
                finally:
                    stream.Dispose()
        except Exception as error:
            result['error'] = str(error)
        finally:
            timer.cancel()
            (args.output / 'native-local-gpu-ui.json').write_text(
                json.dumps(result, indent=2) + '\n', encoding='utf-8')
            window.destroy()

    with tempfile.TemporaryDirectory(dir=args.output) as temporary:
        timer.start()
        webview.start(run, private_mode=True, storage_path=temporary)
    print(json.dumps(result))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
