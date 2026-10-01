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

| Provider | Detected from | Status / quota shown | Notes |
|---|---|---|---|
| Codex | MSIX `OpenAI.Codex_*`, `find_codex()` CLI | Quota windows (existing) | — |
| DeepSeek Harness | Uninstall entry, `%LOCALAPPDATA%\Programs\DeepSeek Harness`, process | **Live account balance** (topped-up + granted wallets, CNY/USD), plus local token totals from `~\.dsh\storages\session_projcache\sessions\*.json` | Same read-only `GET /api/v0/users/get_user_summary` the app uses (or public `GET /user/balance` for an API key); every 5 min, backoff on errors, only while selected |
| Grok (Grok Bot) | Uninstall entry, process, known folders | Signed in (from `%APPDATA%\Grok Bot\desktop-status.json`), running, version | Grok's weekly usage comes from a Connect RPC POST (`DashboardService/GetSandUsageStatus`) authenticated by a DPAPI-encrypted token and is kept only in memory; Radar does not decrypt the token or impersonate the app. Instead, while Grok is the foreground window and its account (avatar) menu is open, the usage row it renders ("NN%" + reset hint) is read via UI Automation (menus only) and shown as "已用 NN%（HH:MM 读取）" until a newer reading |
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
* Encrypted secrets are never decrypted; the DeepSeek sign-in token is read
  only in memory for its read-only wallet GET and never logged or displayed;
  log lines are parsed for
  a fixed whitelist of fields only; exceptions are logged by type name only.
* Probes never run on the UI thread; the strip copies an in-memory snapshot.
* Unknown ids in `usage_providers` (from another release) are ignored, and an
  invalid value falls back to Codex, so a Profile always loads.

## Taskbar placement and theme

* Docked, the strip sits in the empty area of a centered taskbar: right of the
  Widgets/weather button, left of Start and Search. Their positions come from
  Explorer's UI Automation tree (`WidgetsButton`, `StartButton`,
  `SearchButton`) in physical pixels and are re-read every 5 s and whenever
  the taskbar window or size changes (Explorer restart, scale/DPI change,
  alignment change). When the gap is too narrow, the taskbar is left-aligned,
  or UI Automation is unavailable, it falls back to the old spot before the
  notification area (and before Widgets when Widgets sits on the right).
* The background is the colour actually painted by the taskbar (median of a
  few pixels away from icons, refreshed every 5 s and immediately when
  `SystemUsesLightTheme` / `AppsUseLightTheme` / `EnableTransparency` /
  `ColorPrevalence` change), falling back to the theme default or accent
  colour. Text colours are picked for contrast against that background.

## Strip options (round 3)

* **Tooltip**: one line per selected app — name, key quota/balance, reset if any.
* **At most 6 apps** (`providers.MAX_SELECTED`): the Models menu disables further
  ticks with a hint; the strip lays them out in three two-row columns and shrinks
  the type (down to 72 %) to stay inside the empty area left of Start.
* **Background** (Display options > Background, saved as `usage_background`):
  `transparent` (default; colour-keyed so only text is drawn over the real
  taskbar — clicks between glyphs fall through to the taskbar), `match`
  (solid sampled taskbar colour), `dark` / `light` pills and `accent` tint
  (from `HKCU\Software\Microsoft\Windows\DWM\AccentColor`).

## Local GPU backends

NVML (ctypes, no child process, watchdog thread) → nvidia-smi (bounded, kills
the tree on timeout) → Windows DXGI + PDH `GPU Adapter Memory` / `GPU Engine`
counters (any vendor; integrated GPUs report shared memory; English counter
names so the locale does not matter) → Linux amdgpu sysfs.  Machines with no
readable GPU (RDP/VM with only the Basic Render Driver) get `local_gpu_missing`.
