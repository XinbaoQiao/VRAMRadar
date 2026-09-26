## VRAM Radar 0.9.9

This release adds optional Codex quota monitoring and improves the server navigator.

- Enable **Settings → Extensions → Codex usage** to automatically detect a local Codex installation signed in to ChatGPT. No API key or manual path is needed; settings save immediately and monitoring resumes after restart.
- Windows gets a compact taskbar quota strip with matching bold percentage/countdown text, decimal-hour abbreviations, continuous colors, optional disks and a single dock/free-move action. Click to open GPU home; double-click for quota details. Menus follow the system theme, attach correctly and close when focus moves elsewhere.
- macOS gets a native menu-bar quota display with countdowns and actions. The display remains available when the main window is hidden.
- Drag the server navigator's edges to resize it. Dimensions are saved, and the panel stays within the viewport. Fix sticky server headings jittering at the bottom of the list.
- Quota monitoring is off by default, does not connect to GPU servers, and never reads Codex credential files. Account details and raw RPC errors are not retained. Unknown or stale quota is not shown as current.

Downloads: `VRAMRadar-Setup-0.9.9.exe` for Windows x64 and `VRAMRadar-0.9.9-macos.zip` containing native Apple Silicon and Intel apps.

Publication is gated on source tests, native UI checks, Windows installer/update checks, both native Mac builds and validation of the combined Mac archive on both architectures. The Windows installer is unsigned; macOS apps are unsigned and unnotarized. Use Finder's **Open** action for first launch if prompted. Do not disable SmartScreen or Gatekeeper globally.

See [Codex usage](subscription-usage.md) and [third-party notices](third-party-notices.md) for details and attribution.

---

## VRAM Radar 0.9.9 更新说明

本次新增可选的 Codex 额度监测，并改善服务器侧栏体验。

- 在「设置 → 扩展功能 → Codex 额度」开启即可自动查找已登录 ChatGPT 的本地 Codex，无需填写 API 密钥或手动配置路径。设置即时保存，重启后自动恢复。
- Windows 新增紧凑任务栏额度条：百分比与倒计时使用一致粗体，小数小时统一用 `h` 缩写，颜色连续渐变，可选饼图，通过一个菜单项切换固定／自由移动。单击打开 GPU 主页，双击查看额度详情；菜单跟随系统主题，子菜单贴合显示，失去焦点后自动关闭。
- macOS 使用原生菜单栏显示额度、倒计时与操作菜单，隐藏主窗口后仍可查看。
- 服务器侧栏支持拖动边框调整宽高并保存，尺寸限制在可见区域内；修复列表底部服务器悬浮标题抖动。
- 额度监测默认关闭，不触发 GPU 服务器连接，不读取 Codex 凭据文件，也不保留账号详情或原始 RPC 错误；未知或过期额度不会冒充当前数据。

下载：Windows x64 为 `VRAMRadar-Setup-0.9.9.exe`，macOS 为包含 Apple Silicon 与 Intel 原生应用的 `VRAMRadar-0.9.9-macos.zip`。

发布需通过源码测试、原生界面检查、Windows 安装与更新检查、两种 Mac 原生构建，以及合并下载包在两种 Mac 架构上的验证。Windows 安装包未签名；macOS 应用未签名、未公证，首次启动如遇提示请使用 Finder 的「打开」。不要全局关闭 SmartScreen 或 Gatekeeper。

使用方式及来源见 [Codex 额度说明](subscription-usage.md)与 [第三方声明](third-party-notices.md)。
