## VRAM Radar 1.0.7 更新说明

服务器列表更好整理，长时间运行也更省内存。

- **多选整理**：在「已添加的服务器」中进入多选，可全选或勾选多台，批量暂停 / 恢复监控，或一次移除。
- **更顺手的整理栏**：整理操作与标题同行切换，列表不跳动；勾选与拖动手柄共用同一位置。
- **更省内存**：WebView 更精简，悬停详情离开即释放；闲置时回收界面占用，磁盘缓存也有上限。
- **更新检查更克制**：网络超时或不畅时放缓重试，避免频繁打扰。
- **正式安装更易识别**：完整安装目录可正常使用应用内更新。

边界：Windows 安装程序未签名；macOS 应用未签名、未经公证。首次启动如遇提示，请使用 Finder 的「打开」；不要全局关闭 SmartScreen 或 Gatekeeper。容器等完全隔离环境仍可能无法确认 GPU 进程归属。

下载：`VRAMRadar-Setup-1.0.7.exe`（Windows x64）和 `VRAMRadar-1.0.7-macos.zip`（Apple Silicon / Intel）。已安装用户可在应用内检查更新。

<!-- vram-radar:whats-new
{"show_whats_new": true,
 "en": ["Batch tidy the server list: select many, then pause, resume, or remove.",
        "Leaner memory: hover cards release on leave; WebView runs lighter.",
        "Gentler update checks when the network is slow or offline."],
 "zh": ["服务器列表支持多选整理：可批量暂停、恢复或移除。",
        "更省内存：悬停详情离开即释放，界面更精简。",
        "网络不畅时更新检查更克制，减少频繁重试。"]}
-->

---

## VRAM Radar 1.0.7 release notes

**Server list tidy** and a leaner everyday footprint.

- **Batch tidy**: Enter multi-select on Added servers to select many, then pause or resume monitoring, or remove them in one step.
- **Steady tidy bar**: Tidy actions morph into the heading so the list does not jump; checkbox and drag handle share one lead slot.
- **Leaner memory**: A lighter WebView, hover detail that releases when the pointer leaves, idle reclaim, and a capped disk cache.
- **Gentler update checks**: Longer backoff when checks time out or the network fails, so retries stay quiet.
- **Clearer installed builds**: Full packaged Windows trees are recognized for in-app update.

Limitations: the Windows installer is unsigned; macOS apps are unsigned and unnotarized. Use Finder’s Open action on first launch if prompted. Do not disable SmartScreen or Gatekeeper globally. Fully isolated environments may still report unknown GPU process ownership.

Downloads: `VRAMRadar-Setup-1.0.7.exe` (Windows x64) and `VRAMRadar-1.0.7-macos.zip` (Apple Silicon / Intel). Installed copies can check for updates in the app.
