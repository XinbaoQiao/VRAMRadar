## VRAM Radar 1.0.0 更新说明

1.0.0 是首个正式版：在 GPU 监控之外，任务栏／菜单栏额度监测扩展为多个 AI 应用，并完成长时间运行的稳定性与更新可靠性加固。

- **多模型额度**：支持 Codex、Grok、Kimi、DeepSeek 等，最多同时显示 4 个；「模型」菜单按已选／已检测／未检测分组。DeepSeek 显示实时余额（含赠送额度）。
- **征得同意后自动读取**：Grok、Kimi 等需逐个确认后，才会通过应用自身的登录状态只读读取额度；不刷新登录、不保存凭据，可随时撤销。应用未运行或未登录时会说明原因，而不是反复请求授权。
- **名称显示：文字／图标**：任务栏条可用应用图标代替名称（高清图标，边缘不裁切），默认显示文字。
- **重置时间**：每个模型分别显示额度与重置倒计时（48 小时内按小时，否则按天）。
- **渐变颜色**：统一的 OKLab 连续渐变；未运行、需登录、已过期等状态以中性灰显示，登录过期用警示色。
- **跟随天气组件定位**：根据任务栏天气／资讯组件的实际可见内容宽度放置额度条，包括两行资讯与中文文本，不闪烁、不跳动；锁屏期间暂停测量。
- **中英双语界面**：对话框、提示、通知与网页界面均完整本地化，首次运行跟随系统语言。
- **内存泄漏修复**：缓存任务栏 UI Automation 元素、修复 ctypes 类型泄漏，并定期回收内存；长时间运行时内存保持平稳。
- **长期运行维护**：缓存与日志有上限，WebView2 配置独立存放，新增健康看门狗。
- **更新更可靠**：更新失败可恢复旧版本，能处理截断响应与网络中断，失败后自动退避重试。
- **macOS 修复**：正确识别 .app 安装位置，并新增 macOS 行为测试。
- 本地 GPU：支持 NVML，提供 nvidia-smi 回退，并通过 Windows 性能计数器支持任意显卡厂商。

**1.0.0 修订版（同版本重新发布）**：已安装最初 1.0.0 的用户会在应用内收到「重新安装」提示。

- 修复主窗口打开文件夹面板且服务器响应缓慢时，重复读取请求不断累积、线程与内存持续增长的问题。
- 仅选择 Codex 时，任务栏额度条也会在空间不足时先缩小字号、再裁切，不再遮挡「开始」按钮；多应用布局也不会超出可用空间。
- 英文界面补全剩余中文文本，单复数正确（如 1 GPU、1 server）；服务器导航提示不再重复状态（如「数据已过期，数据已过期」）。
- 更新 README、隐私说明与截图。

下载：Windows x64 为 `VRAMRadar-Setup-1.0.0.exe`，macOS 为包含 Apple Silicon 与 Intel 原生应用的 `VRAMRadar-1.0.0-macos.zip`。已安装 0.9.x 的 Windows 用户会在应用内收到更新提示（经 SHA-256 与文件大小校验）。

Windows 安装包未签名；macOS 应用未签名、未公证，首次启动如遇提示请使用 Finder 的「打开」。不要全局关闭 SmartScreen 或 Gatekeeper。

---

## VRAM Radar 1.0.0

1.0.0 is the first stable release. Beyond GPU monitoring, the taskbar/menu-bar quota display now covers multiple AI apps, and long-running stability and update reliability have been hardened.

- **Multi-model quota**: Codex, Grok, Kimi, DeepSeek and more, up to 4 at once. The Models menu groups chosen, detected and not-detected apps. DeepSeek shows its live balance, including gift credit.
- **Auto-read with consent**: Grok, Kimi and similar apps are read only after you consent for each app. Reading is read-only and uses the app's own sign-in: no token refresh, no stored credentials, revocable at any time. If an app is not running or signed out, VRAM Radar explains why instead of asking again.
- **Icon or text labels**: the strip can show app icons instead of names. Icons are high-resolution with uncut edges. Text is the default.
- **Reset time**: each model shows its quota and its reset countdown separately (hours under 48 h, days otherwise).
- **Gradient colours**: one continuous OKLab gradient. Not-running, sign-in-needed and stale states are shown in neutral grey; an expired sign-in uses the warning colour.
- **Weather-follow positioning**: the strip is placed from the visible content width of the taskbar weather/news widget, including two-line news and CJK text, without flicker or jumps. Measurement pauses while the session is locked.
- **Bilingual UI**: dialogs, notices, toasts and the web UI are fully localized in Chinese and English. The first run follows the system language.
- **Memory leak fixes**: taskbar UI Automation elements are cached, a ctypes type leak is fixed and memory is periodically reclaimed. Memory stays flat over long runs.
- **Housekeeping**: bounded caches and logs, a separate WebView2 profile and a health watchdog.
- **Updater robustness**: a failed update can restore the previous version. Truncated responses and dropped connections are handled, and failed checks back off before retrying.
- **macOS fixes**: the .app install location is detected correctly, with added macOS behaviour tests.
- Local GPU: NVML with an nvidia-smi fallback, plus Windows performance counters for any vendor.

**1.0.0 refresh (same version, rebuilt)**: installs of the original 1.0.0 are offered a reinstall in the app.

- Fixed repeated folder reads piling up threads and memory when a folder panel was open in the main window for a slow server.
- With only Codex selected, the taskbar strip now shrinks its type and then clips when space is short, so it no longer covers Start; the multi-app layout never exceeds the gap either.
- The English UI translates the remaining Chinese text and uses correct singular/plural counts (1 GPU, 1 server); the server navigator no longer repeats the state (e.g. "Data is stale, Data is stale").
- Refreshed README, privacy notes and screenshots.

Downloads: `VRAMRadar-Setup-1.0.0.exe` for Windows x64 and `VRAMRadar-1.0.0-macos.zip` containing native Apple Silicon and Intel apps. Windows installs of 0.9.x are offered this update in the app, verified by SHA-256 and size.

The Windows installer is unsigned; macOS apps are unsigned and unnotarized. Use Finder's **Open** action for first launch if prompted. Do not disable SmartScreen or Gatekeeper globally.
