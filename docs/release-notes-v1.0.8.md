## VRAM Radar 1.0.8 更新说明

配置迁移更好上手，说明与截图也跟得上；经中转连接的服务器在链路波动时更稳。

- **更省事的配置迁移**：主路径更短——先出二维码、再按需展开进阶选项；二维码更大，复制按钮更醒目。
- **说明与截图同步**：README 迁移截图换成当前界面；空状态与引导文案更短、更好懂。
- **中转链路更稳**：走 ProxyJump 等中转时，短暂波动会自动重试，并继续显示最近一次可用数据。

边界：Windows 安装程序未签名；macOS 应用未签名、未经公证。首次启动如遇提示，请使用 Finder 的「打开」；不要全局关闭 SmartScreen 或 Gatekeeper。容器等完全隔离环境仍可能无法确认 GPU 进程归属。

下载：`VRAMRadar-Setup-1.0.8.exe`（Windows x64）和 `VRAMRadar-1.0.8-macos.zip`（Apple Silicon / Intel）。已安装用户可在应用内检查更新。

<!-- vram-radar:whats-new
{"show_whats_new": true,
 "en": ["Easier config transfer: shorter main path, larger QR, advanced options tucked away.",
        "README screenshots match the new migrate UI; empty-state hints are shorter.",
        "Jump-host links keep recent data on screen and retry quietly when the path blips."],
 "zh": ["配置迁移更省事：主路径更短，二维码更大，进阶选项收进「更多」。",
        "README 截图与迁移界面同步，空状态提示更短更清楚。",
        "经中转连接时短暂波动会自动重试，并继续显示最近可用数据。"]}
-->

---

## VRAM Radar 1.0.8 release notes

**Easier config transfer**, matching docs, and steadier monitoring through jump hosts.

- **Foolproof migrate**: A shorter primary path—QR first, advanced options folded away; larger QR and a clearer copy button.
- **Docs and empty states**: README migrate screenshots match the current UI; empty-state and onboarding copy is shorter and plainer.
- **Steadier jump-host links**: Brief ProxyJump blips auto-retry while the last good reading stays on screen.

Limitations: the Windows installer is unsigned; macOS apps are unsigned and unnotarized. Use Finder’s Open action on first launch if prompted. Do not disable SmartScreen or Gatekeeper globally. Fully isolated environments may still report unknown GPU process ownership.

Downloads: `VRAMRadar-Setup-1.0.8.exe` (Windows x64) and `VRAMRadar-1.0.8-macos.zip` (Apple Silicon / Intel). Installed copies can check for updates in the app.
