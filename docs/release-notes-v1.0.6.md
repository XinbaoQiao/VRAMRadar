## VRAM Radar 1.0.6 更新说明

「配置迁移」更好用：可选带走哪些内容，导入前能预览，也可在同一局域网里短时传送。

- **勾选要带走的内容**：可只导出选中的服务器和偏好，二维码更干净。
- **导入前预览，合并或覆盖**：先看会写入什么，再选择合并进现有配置，或整表覆盖。
- **可选带上密码**：勾选后用口令加密；带密码的包有时效，导入后本机作废。可另选是否含 SSH 私钥。
- **同一局域网短时传送**：两台电脑在同一 Wi‑Fi 时，可用短时口令互传，不经公网。
- **导入后自检**：标出缺密码、缺私钥的项，方便补全。
- **更清楚的布局与说明**：导出 / 导入分成两块，文案更短更好懂。

边界：Windows 安装程序未签名；macOS 应用未签名、未经公证。首次启动如遇提示，请使用 Finder 的「打开」；不要全局关闭 SmartScreen 或 Gatekeeper。容器等完全隔离环境仍可能无法确认 GPU 进程归属。

下载：`VRAMRadar-Setup-1.0.6.exe`（Windows x64）和 `VRAMRadar-1.0.6-macos.zip`（Apple Silicon / Intel）。已安装用户可在应用内检查更新。

<!-- vram-radar:whats-new
{"show_whats_new": true,
 "en": ["Transfer config: choose which servers and preferences to take.",
        "Preview before import; merge into existing setup or replace it.",
        "Optional passwords (passphrase-sealed, time-limited) and same-Wi‑Fi handoff."],
 "zh": ["配置迁移：可勾选要带走的服务器和偏好。",
        "导入前可预览，支持合并或覆盖。",
        "可选带密码（口令加密、有时效），同一局域网可短时传送。"]}
-->

---

## VRAM Radar 1.0.6 release notes

**Transfer config** is more useful: pick what to take, preview before import, and optionally hand off on the same LAN.

- **Choose what to take**: Export only selected servers and preferences for a cleaner QR.
- **Preview, then merge or replace**: See what will be written, then merge into the current setup or replace it.
- **Optional passwords**: Include login passwords sealed with a passphrase; sealed packages expire and are voided after import. SSH private keys stay optional.
- **Same-Wi‑Fi handoff**: On the same local network, move a package with a short-lived code—no public cloud.
- **Import check**: Flag items missing a password or private key so you can fill them in.
- **Clearer layout and copy**: Export and import are two cards, with shorter, plainer wording.

Limitations: the Windows installer is unsigned; macOS apps are unsigned and unnotarized. Use Finder’s Open action on first launch if prompted. Do not disable SmartScreen or Gatekeeper globally. Fully isolated environments may still report unknown GPU process ownership.

Downloads: `VRAMRadar-Setup-1.0.6.exe` (Windows x64) and `VRAMRadar-1.0.6-macos.zip` (Apple Silicon / Intel). Installed copies can check for updates in the app.
