## VRAM Radar 1.0.5 更新说明

新增「配置迁移」：用一张二维码把本机配置迁到新电脑。

- **配置迁移**：设置里新增独立的「配置迁移」页。本机生成二维码，复制或保存图片；新电脑粘贴或导入图片即可迁入服务器列表与偏好。
- **默认不含密码**：二维码默认不含登录密码与私钥；迁到新电脑后如需密码可再填写。
- **为手机迁移留好基础**：同一套迁移包也可作为后续手机导入的基础。

边界：Windows 安装程序未签名；macOS 应用未签名、未经公证。首次启动如遇提示，请使用 Finder 的「打开」；不要全局关闭 SmartScreen 或 Gatekeeper。容器等完全隔离环境仍可能无法确认 GPU 进程归属。

下载：`VRAMRadar-Setup-1.0.5.exe`（Windows x64）和 `VRAMRadar-1.0.5-macos.zip`（Apple Silicon / Intel）。已安装用户可在应用内检查更新。

<!-- vram-radar:whats-new
{"show_whats_new": true,
 "en": ["Settings → Transfer config: move your setup with a QR image.",
        "Copy or save the QR on this PC; paste or import it on a new one.",
        "Passwords and private keys are not included in the QR by default."],
 "zh": ["设置 → 配置迁移：用二维码把配置迁到新电脑。",
        "本机复制或保存二维码，新电脑粘贴或导入图片即可。",
        "二维码默认不含登录密码与私钥。"]}
-->

---

## VRAM Radar 1.0.5 release notes

Adds **Transfer config**: move this computer’s setup to a new PC with a QR image.

- **Transfer config**: A dedicated Settings pane generates a QR for your server list and preferences. Copy or save the image here; paste or import it on the new computer.
- **No passwords by default**: The QR does not include login passwords or private keys. Enter passwords again on the new computer if needed.
- **Ready for phone later**: The same transfer package is the foundation for a future phone import.

Limitations: the Windows installer is unsigned; macOS apps are unsigned and unnotarized. Use Finder’s Open action on first launch if prompted. Do not disable SmartScreen or Gatekeeper globally. Fully isolated environments may still report unknown GPU process ownership.

Downloads: `VRAMRadar-Setup-1.0.5.exe` (Windows x64) and `VRAMRadar-1.0.5-macos.zip` (Apple Silicon / Intel). Installed copies can check for updates in the app.
