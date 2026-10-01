# AI app providers (usage strip "Models" menu)

Right-click the taskbar usage strip → **显示模型 / Models** to choose, with
multi-select, which AI apps the strip shows. The choice is saved in the
Profile as `usage_providers` (default `["codex"]`, so existing installs keep
the Codex-only strip unchanged). **重新检测 / Detect again** rescans at once;
otherwise detection runs every 60 s on a background thread while the strip is
enabled. The Codex quota path is unchanged (local `codex app-server`, every
5 minutes) and is not started when Codex is deselected.

Each provider is one module in `src/vram_radar/providers/` exposing
`probe(env) -> dict`; `providers/__init__.py` holds the registry.

| Provider | Detected from | Status shown | Why no quota % |
|---|---|---|---|
| Codex | MSIX `OpenAI.Codex_*`, `find_codex()` CLI | Quota windows (existing) | — |
| DeepSeek Harness | Uninstall entry, `%LOCALAPPDATA%\Programs\DeepSeek Harness`, process | Local token totals and session count from `~\.dsh\storages\session_projcache\sessions\*.json`; API-credential file present (not read) | It uses your own API key; the balance exists only on the DeepSeek platform and is never queried locally |
| Grok (Grok Bot) | Uninstall entry, process, known folders | Signed in (from `%APPDATA%\Grok Bot\desktop-status.json`), running, version | The app stores no quota/credit data locally |
| Kimi | Uninstall entry (`DisplayIcon`), process | Membership level, exhausted / overdrawn / send-blocked and reset time, taken from Kimi's own `logs\main.log` refresh lines; signed in = the encrypted token store exists (not opened) | Kimi fetches quota online with an encrypted token; the log snapshot is only as fresh as Kimi's last run and is marked "As of" when older than 6 h |
| Claude, GLM 智谱清言, Qwen, 腾讯元宝 | Uninstall entries, MSIX, known folders, processes, sibling folders of other detected apps | Installed / version / running / last data change; "leftover data" when only an old data folder remains | No documented local usage data |

## Failure handling

* Install paths: per-user `AppData\Local\Programs`, Program Files, Squirrel
  `app-<version>` layouts, MSIX packages (via the per-user AppModel registry,
  no WindowsApps listing needed), running processes (portable copies), and
  folders next to other detected apps (custom roots such as `D:\Apps`).
  With several copies, a running one wins, then the newest version.
* Not running / not signed in / not installed are shown as states, never as
  errors; a failing probe is isolated and shown as "Probe failed".
* Locked files are read through a private temporary copy; JSON is size-bounded
  (2 MB) and logs are tail-bounded (4 MB); damaged files read as "unknown".
* No secrets are decrypted, read, logged or displayed; log lines are parsed for
  a fixed whitelist of fields only; exceptions are logged by type name only.
* Probes never run on the UI thread; the strip copies an in-memory snapshot.
* Unknown ids in `usage_providers` (from another release) are ignored, and an
  invalid value falls back to Codex, so a Profile always loads.
