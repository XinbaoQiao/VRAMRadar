"""Folder reads against a slow server must not snowball (live 1.0.0: ~2,400 threads)."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def js_function(source, name):
    start = source.index(f"function {name}(")
    prefix = "async " if source[max(0, start - 6):start] == "async " else ""
    return prefix + source[start:source.index("\n}\n", start) + 2]


HARNESS = r"""
const directoryTrees = new Map(), directoryRequestTokens = new Map(), directoryLoadsInFlight = new Map();
const directoryFreshnessDeadlines = new Map();
let directoryRequestSequence = 0;
const DIRECTORY_FRESHNESS_ERROR_RETRY_MS = 30000;
const repaintDirectory = () => {}, scheduleDirectoryFreshnessValidation = () => {};
const directoryStateFromAccount = (account, cache) => ({status: 'loaded', account, cache, entries: []});
const mergeDirectoryAccount = (_id, latest) => latest;
let calls = 0, release = [];
const api = {inspect_account_directory: () => { calls += 1; return new Promise(done => release.push(done)); }};
(async () => {
  directoryTrees.set('s1', {status: 'loaded', entries: []});
  directoryFreshnessDeadlines.set(directoryRequestKey('s1'), Date.now() - 1000);  // overdue
  // Renders, toggle events and the freshness timer while the server hangs.
  const joined = Array.from({length: 50}, () => loadDirectoryTree('s1'));
  const afterBurst = calls;
  release.splice(0).forEach(done => done({ok: true, account: {}, cache: {revalidate_after_seconds: 15}}));
  await Promise.all(joined);
  const deadlineReset = directoryFreshnessDeadlines.get(directoryRequestKey('s1')) > Date.now();
  // Once settled and fresh, a plain trigger reads nothing; an explicit refresh does.
  const next = loadDirectoryTree('s1'); const forced = loadDirectoryTree('s1', true);
  const afterForce = calls;
  release.splice(0).forEach(done => done({ok: true, account: {}, cache: {revalidate_after_seconds: 15}}));
  await Promise.all([next, forced]);
  // A subfolder is its own read.
  const sub = loadDirectoryTree('s1', false, 'proj/a');
  const afterSub = calls;
  release.splice(0).forEach(done => done({ok: true, account: {directory_tree: {supported: true}}, cache: {}}));
  await sub;
  process.stdout.write(JSON.stringify({afterBurst, deadlineReset, afterForce, afterSub,
                                       pending: directoryLoadsInFlight.size, status: directoryTrees.get('s1').status}));
})().catch(error => { console.error(error); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class DirectoryLoadFloodTests(unittest.TestCase):
    def test_repeated_triggers_share_one_pending_read(self):
        source = (ROOT / "src/vram_radar/web/app.js").read_text(encoding="utf-8")
        names = ("directoryRequestKey", "rememberDirectoryFreshness", "deferDirectoryFreshness", "clearDirectoryFreshness",
                 "invalidateDirectoryRequests", "loadDirectoryTree", "loadDirectoryTreeNow")
        script = "\n".join(js_function(source, name) for name in names) + HARNESS
        out = subprocess.run([shutil.which("node"), "-e", script], capture_output=True, text=True, encoding="utf-8",
                             timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr[:400])
        self.assertEqual(json.loads(out.stdout), {"afterBurst": 1, "deadlineReset": True, "afterForce": 2,
                                                  "afterSub": 3, "pending": 0, "status": "loaded"})


if __name__ == "__main__":
    unittest.main()
