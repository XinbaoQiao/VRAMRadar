## VRAM Radar 1.0.4 更新说明

设置与日常使用体验的一次整理：分类更清楚，常用操作更好找，界面更顺。

- **设置左侧分类导航**：设置改为左侧分类，服务器、显示与语言、通知、扩展等常用项排在前面；缓存清理、应用更新、诊断与日志放在后面。默认打开「服务器」。
- **缓存清理**：可在设置里配置自动清理间隔，也可一键清理本地缓存。
- **设置文案与层级**：标题统一为「设置」，说明更短更清楚，字段间距与层级更易阅读。
- **更顺畅的设置操作**：打开设置、切换分类与语言预览时更顺畅，尽量少打断主界面刷新。
- **额度条左侧固定**：可将 AI 额度条固定在任务栏左侧，跨不同任务栏布局更稳定。
- **重置倒计时**：额度旁显示重置倒计时，窄空间下也会尽量保留。
- **功能更新提示（可选）**：仅当本版本在发布说明中标记时，更新后才会弹出简短「新功能」提示；纯修复版默认不弹。
- 包含 1.0.3 的进程关注、排队原因完整查看与归属识别改进。

边界：Windows 安装程序未签名；macOS 应用未签名、未经公证。首次启动如遇提示，请使用 Finder 的「打开」；不要全局关闭 SmartScreen 或 Gatekeeper。容器等完全隔离环境仍可能无法确认 GPU 进程归属。

下载：`VRAMRadar-Setup-1.0.4.exe`（Windows x64）和 `VRAMRadar-1.0.4-macos.zip`（Apple Silicon / Intel）。已安装用户可在应用内检查更新。

<!-- vram-radar:whats-new
{"show_whats_new": true,
 "en": ["Settings use a left category nav with frequent items first.",
        "Configurable cache cleanup with one-click clean.",
        "Shorter settings copy and clearer hierarchy.",
        "Smoother settings open and language preview.",
        "Pin the AI quota strip on the left.",
        "Show reset countdowns beside quotas."],
 "zh": ["设置改为左侧分类，常用项排在前面。",
        "可配置缓存清理，并支持一键清理。",
        "设置说明更短更清楚，层级更易读。",
        "打开设置与切换语言预览更顺畅。",
        "额度条可固定在任务栏左侧。",
        "额度旁显示重置倒计时。"]}
-->

---

## VRAM Radar 1.0.4 release notes

A settings and everyday-use polish: clearer categories, easier common actions, and smoother interaction.

- **Settings left category nav**: Settings use a left-hand category list. Frequent panes—Servers, Display & language, Notifications, Extensions—come first; Cache cleanup, App updates, and Diagnostics & logs follow. Servers opens by default.
- **Cache cleanup**: Configure an automatic cleanup interval in Settings, or run a one-click cleanup.
- **Clearer settings copy and hierarchy**: The window title is Settings; help text is shorter, with spacing and hierarchy that are easier to scan.
- **Smoother settings use**: Opening Settings, switching categories, and previewing a language stay responsive and avoid unnecessary main-window redraws.
- **Left-anchored quota strip**: Optionally pin the AI quota strip to a stable left taskbar anchor across layouts.
- **Reset countdowns**: Show reset countdowns beside quotas and keep them visible when space is tight.
- **Optional What’s new card**: A short feature summary appears after update only when this Release opts in; bugfix-only releases stay silent by default.
- Includes the process-follow, pending-reason, and ownership improvements from 1.0.3.

Limitations: the Windows installer is unsigned; macOS apps are unsigned and unnotarized. Use Finder’s Open action on first launch if prompted. Do not disable SmartScreen or Gatekeeper globally. Fully isolated environments may still report unknown GPU process ownership.

Downloads: `VRAMRadar-Setup-1.0.4.exe` (Windows x64) and `VRAMRadar-1.0.4-macos.zip` (Apple Silicon / Intel). Installed copies can check for updates in the app.
