"""Offline behavioral checks for the ACP editor and honest platform rendering."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

WEB = Path(__file__).parents[1] / "src/vram_radar/web"


@unittest.skipUnless(shutil.which("node"), "Node is required for UI behavior checks")
class AcpUiTests(unittest.TestCase):
    def run_js(self, names, body):
        source = (WEB / "app.js").read_text(encoding="utf8")
        snippets = []
        for name in names:
            start = source.index("function " + name + "(")
            end = source.index("\n}\n", start) + 3
            snippets.append(source[start:end])
        result = subprocess.run(
            [shutil.which("node"), "-e", "\n".join(snippets) + "\n" + body],
            capture_output=True, text=True, encoding="utf8", timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_switching_ssh_draft_does_not_serialize_credentials_or_ssh(self):
        result = self.run_js(["acpProfileFromDraft"], """
const draft = {id:'acp',display_name:'ACP', acp_workspace:' space ', acp_cluster:'cluster',
 acp_zone:'cn-sh-01g',acp_profile:'mine',ssh_alias:'legacy',host:'legacy-host',
 auth_ref:'legacy-auth',identity_file:'legacy-id',
 slurm_module:'slurm',default_work_directory:'/old',auto_detect_backend:true};
console.log(JSON.stringify(acpProfileFromDraft(draft)));
""")
        self.assertEqual(result["acp_workspace"], "space")
        self.assertEqual(result["acp_sco_path"], "sco")
        self.assertEqual(result["backend"], "sensecore_acp")
        default = self.run_js(["acpProfileFromDraft"], "console.log(JSON.stringify(acpProfileFromDraft({id:'a'})));" )
        self.assertEqual(default["acp_profile"], "default")
        for key in ("auth_ref", "identity_file", "_password", "ssh_alias", "host", "slurm_module", "auto_detect_backend", "default_work_directory"):
            self.assertNotIn(key, result)

    def test_platform_is_never_advertised_as_free_gpu_capacity(self):
        result = self.run_js(["serverNavigatorHasAvailableResource"], """
console.log(JSON.stringify(serverNavigatorHasAvailableResource({backend:'sensecore_acp',
 connection:{state:'online'},free_vram_gib:999,free_gpus:99})));
""")
        self.assertFalse(result)

    def test_acp_editor_preserves_protected_auth_without_credential_paths(self):
        result = self.run_js(["acpProfileFromDraft"], """
console.log(JSON.stringify([
acpProfileFromDraft({id:'same',acp_protected_auth:false}, {acp_protected_auth:true}),
acpProfileFromDraft({id:'renamed',acp_protected_auth:true}),
acpProfileFromDraft({id:'new'})]));
""")
        self.assertEqual([row["acp_protected_auth"] for row in result], [True, True, False])

    def test_platform_table_escapes_server_content_and_labels_requested_not_free(self):
        result = self.run_js(["renderAcpPlatform"], """
const escapeHtml = value => String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const number = value => String(value);
const icon = (name, cls='') => `<i class="${cls||name}"></i>`;
const moduleOpen = () => '';
const taskGroupOpen = (_serverId, _module, group, defaultOpen=false) => defaultOpen ? ' open' : '';
const taskStateLabel = state => ({PENDING:'排队', QUEUEING:'排队', WAIT_QUOTA:'待配额', STARTING:'启动', RUNNING:'运行', SUCCEEDED:'完成', FAILED:'失败', UNKNOWN:'未知'})[state] || state;
const taskStateClass = state => ({RUNNING:'running', PENDING:'pending', SUCCEEDED:'completed', FAILED:'failed'}[state] || 'pending');
const taskStateIcon = () => 'clock';
console.log(JSON.stringify(renderAcpPlatform({server_id:'h100', platform:{workspace:'<script>',cluster:'c',zone:'z'},
 jobs:[
  {job_id:'r1',name:'run-a',state:'RUNNING',requested_gpus:1},
  {job_id:'j',name:'<img onerror=x>',state:'PENDING',requested_gpus:4,reason:'<script>'},
  {job_id:'d1',name:'done-a',state:'SUCCEEDED',requested_gpus:1},
  {job_id:'d2',name:'done-b',state:'FAILED',requested_gpus:2},
 ]})));
""")
        self.assertNotIn("<script>", result)
        self.assertNotIn("<img onerror", result)
        self.assertIn(">GPU<", result)
        self.assertIn("云任务", result)
        self.assertIn("只读", result)
        self.assertIn("acp-platform-tag", result)
        self.assertIn("acp-job-main", result)
        self.assertIn("acp-state", result)
        self.assertIn("&lt;script&gt;", result)
        self.assertIn("排队", result)
        self.assertIn(">运行中<", result)
        self.assertIn(">排队<", result)
        self.assertIn(">已结束<", result)
        self.assertNotIn("排队与启动", result)
        self.assertNotIn("当前只显示", result)
        self.assertIn('data-task-module="acp-jobs"', result)
        self.assertIn('data-task-group="acp-running"', result)
        self.assertIn('data-task-group="acp-pending"', result)
        self.assertIn('data-task-group="acp-done"', result)
        self.assertIn("task-group-chevron", result)
        # Active buckets default open; finished stays collapsed.
        self.assertRegex(result, r'data-task-group="acp-running"[^>]* open')
        self.assertRegex(result, r'data-task-group="acp-pending"[^>]* open')
        self.assertNotRegex(result, r'data-task-group="acp-done"[^>]* open')
        self.assertNotIn("acp-page-note", result)


    def test_acp_has_no_directory_module_or_account_ssh_summary(self):
        result = self.run_js(["renderDirectoryModule", "renderAccountOverview"], """
console.log(JSON.stringify([renderDirectoryModule({backend:'sensecore_acp'}),
renderAccountOverview({backend:'sensecore_acp'})]));
""")
        self.assertEqual(result, ["", ""])


if __name__ == "__main__":
    unittest.main()
