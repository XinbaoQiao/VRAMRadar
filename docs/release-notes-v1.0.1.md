## VRAM Radar 1.0.1 更新说明

1.0.1 让任务栏额度条更好用：悬停详情卡更完整、可直接打开 AI 应用，设置更简单，并修复若干问题。

- **悬停详情卡**：鼠标悬停额度条约 0.4 秒显示详情卡；卡片宽度随内容自适应。点击额度条或打开右键菜单时卡片立即隐藏，鼠标重新移入后才会再次出现。
- **用量走势图**：在本机记录各应用额度变化，详情卡名称行显示近 7 天的迷你走势图；可在右键菜单「显示选项 → 显示走势图」中开关。只有真实用量变化时才显示。
- **按紧迫程度排序**：可选按剩余额度与重置时间排序；排序带防抖，不会来回跳动。余额类（如 DeepSeek）排在按百分比重置的应用之后。
- **从详情卡打开应用**：点击详情卡中本机已检测到的应用所在行，即可打开该应用或切换到它的窗口。指针悬停时该行高亮、按下时加深、显示手形指针，打开成功会短暂闪烁确认；打开失败显示中性灰色，不会误导。
- **点击额度条打开 VRAM Radar**：单击打开 GPU 主页，双击打开设置中的「额度监控」开关；额度条本身不再启动其他应用。
- **单一「额度监控」开关**：设置 → 扩展功能 只保留一个总开关和一行状态，统一控制任务栏额度条与所有 AI 应用；关闭再开启会保留已选应用与授权。布局更紧凑。
- **修复**
  - 走势数据文件原先每秒重写一次；现在只在数据变化时写入，额度监控关闭时不再记录。
  - 快速三击额度条不再出现「设置刚打开又被关闭」。
  - 关闭额度监控时，额度条与详情卡立即隐藏，不会残留。
  - 设置中的状态行只在设置窗口打开时刷新，数量与任务栏显示的应用一致。
  - 旧版本保存的 Codex 路径失效后，会自动改用自动检测。
  - 打开应用时不会误把 VRAM Radar 自身或无关程序的窗口切到前台。

下载：Windows x64 为 `VRAMRadar-Setup-1.0.1.exe`，macOS 为包含 Apple Silicon 与 Intel 原生应用的 `VRAMRadar-1.0.1-macos.zip`。已安装 1.0.0 的 Windows 用户会在应用内收到更新提示（经 SHA-256 与文件大小校验）。

Windows 安装包未签名；macOS 应用未签名、未公证，首次启动如遇提示请使用 Finder 的「打开」。不要全局关闭 SmartScreen 或 Gatekeeper。

---

## VRAM Radar 1.0.1

1.0.1 makes the taskbar quota strip more useful: a richer hover card that can open your AI apps, simpler settings, and a set of fixes.

- **Hover card**: hover the strip for about 0.4 s to see the detail card; its width now fits the content. Clicking the strip or opening its menu hides the card at once, and it comes back only when the pointer re-enters.
- **Usage trend sparklines**: quota changes are recorded locally and shown as a compact 7-day sparkline on each app's name row. Turn it on or off under right-click **Display options → Show trend chart**. A sparkline appears only when there is real usage change.
- **Sort by urgency**: optionally order apps by remaining quota and reset time, with hysteresis so the order does not jump around. Balance-based apps (such as DeepSeek) rank after percentage quotas that reset.
- **Open apps from the hover card**: click the row of an app detected on this computer to open it or bring its window to the front. The row highlights under the pointer, deepens while pressed and shows a hand cursor; a short flash confirms the app opened, and a failure shows a neutral grey instead.
- **Strip clicks open VRAM Radar**: a single click opens the GPU home page and a double-click opens the **Quota monitoring** switch in Settings. The strip itself no longer launches other apps.
- **One Quota monitoring switch**: Settings → Extensions now has a single master switch and one status line for the taskbar strip and all AI apps. Turning it off and on again keeps your chosen apps and consents. The layout is more compact.
- **Fixes**
  - The trend data file was rewritten every second; it is now written only when the data changes, and nothing is recorded while quota monitoring is off.
  - A fast triple-click on the strip no longer opens Settings and closes it again.
  - Turning quota monitoring off hides the strip and the hover card immediately.
  - The status line in Settings refreshes only while Settings is open, and its count matches the apps shown on the strip.
  - A Codex path saved by an earlier version falls back to automatic detection once it no longer exists.
  - Opening an app never brings VRAM Radar itself or an unrelated program to the front.

Downloads: `VRAMRadar-Setup-1.0.1.exe` for Windows x64 and `VRAMRadar-1.0.1-macos.zip` containing native Apple Silicon and Intel apps. Windows installs of 1.0.0 are offered this update in the app, verified by SHA-256 and size.

The Windows installer is unsigned; macOS apps are unsigned and unnotarized. Use Finder's **Open** action for first launch if prompted. Do not disable SmartScreen or Gatekeeper globally.
