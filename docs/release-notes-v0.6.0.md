## VRAM Radar 0.6.0

A stable Windows, Apple Silicon, and Intel macOS release focused on reliable
first use, window recovery, SSH configuration, and large-workspace performance.

### Downloads

- Windows: `VRAMRadar-Setup-0.6.0.exe`. Install normally for the current user;
  in-place upgrades preserve the existing Start-menu and desktop shortcuts.
- macOS: `VRAMRadar-0.6.0-macos.zip`. It contains separately built and validated
  Apple Silicon and Intel applications in one download.

### Highlights

- Added one-click PowerShell and macOS Terminal launchers to the SSH discovery
  guide. The app opens an empty command window and never executes the copied
  configuration command automatically.
- Simplified progressive disclosure: dashboard and Settings keep only
  **Collapse all**; bulk-expand and restore-default controls were removed.
- Fixed taskbar, notification-area, off-screen, and WebView restore races.
  Windows now starts at a useful size, enforces a minimum size, and remembers
  only valid normal-window geometry.
- Added bounded lazy directory caching, request coalescing, lightweight remote
  freshness probes, changed-root refresh, and a hard deep-refresh deadline.
  Reopening an unchanged code directory no longer repeats a full SSH scan.
- Strengthened SSH import and connection evidence. Imported servers retain the
  exact OpenSSH source, deleted aliases stay ignored, newly added aliases remain
  discoverable, and an entry is not reported as monitoring-ready until the live
  collector succeeds.
- Copy SSH now includes a statically provable address, user, and port while
  preserving the original OpenSSH config and alias. Dynamic or conditional
  configurations fall back safely without executing `Match exec`.
- Added guided SSH Key setup, secure password fallback, actionable connection
  errors, complete redacted diagnostics, and real system-terminal launching.
- The Windows installer preflights custom-directory create/write/delete access
  before replacing an existing installation, avoiding partial upgrades on
  protected folders.

### Platform security boundary

No Windows Authenticode or Apple Developer ID credentials are configured for
this repository. Windows may show SmartScreen for the new installer; verify the
GitHub source before choosing **More info → Run anyway**. On macOS, extract the
archive, right-click the matching app, choose **Open**, and confirm once. Do not
disable SmartScreen or Gatekeeper globally. The release is created only after
Windows, Apple Silicon, Intel, and final combined-package validation succeeds.

---

## VRAM Radar 0.6.0 中文说明

这是面向 Windows、Apple Silicon Mac 与 Intel Mac 的正式稳定更新，重点提升
首次配置、窗口恢复、SSH 可靠性和大型工作目录性能。

### 下载

- Windows：`VRAMRadar-Setup-0.6.0.exe`。按当前用户正常安装即可；覆盖升级后，
  原开始菜单和桌面快捷方式继续有效。
- macOS：`VRAMRadar-0.6.0-macos.zip`。一个下载包内包含分别构建、分别验证的
  Apple Silicon 与 Intel 应用。

### 主要更新

- SSH 查找教程增加“一键打开 PowerShell / 终端”。应用只打开空命令窗口，绝不
  自动执行用户复制的配置命令。
- 精简多级内容控制：资源页和设置页只保留“一键收起”，移除“全部展开”和
  “恢复默认”。
- 修复任务栏、通知区域、屏幕外窗口与 WebView 恢复竞态；设置合理默认 / 最小
  尺寸，并且只保存有效的普通窗口尺寸。
- 代码工作目录使用有界懒加载缓存、同请求合并、轻量远端变化检查、按变化目录
  刷新与最长期限深度校验；二次打开不再重复完整 SSH 扫描。
- 加固 SSH 导入与真实连接状态：保留原始 OpenSSH 文件，用户删除的旧别名不会
  自动复活，新别名仍可发现，只有实时采集链路成功后才显示“监控就绪”。
- “复制 SSH”在可以静态证明时带上地址、用户与端口，同时保留原始配置与别名；
  动态规则安全回退，不执行 `Match exec`。
- 增加 SSH Key 引导、密码安全回退、明确连接错误、完整脱敏诊断和真实系统终端。
- Windows 安装器在替换旧版本前检查自定义目录的创建、写入和删除权限，避免在
  受保护目录中留下半完成安装。

### 平台安全边界

当前仓库没有配置 Windows Authenticode 或 Apple Developer ID 分发凭据。
Windows 新安装包可能显示 SmartScreen；请先核对下载来自本 GitHub 仓库，再选择
“更多信息 → 仍要运行”。macOS 解压后，右击对应应用，选择“打开”，再确认一次。
不要全局关闭 SmartScreen 或 Gatekeeper。只有 Windows、Apple Silicon、Intel 与
最终合包验证全部通过后，正式 Release 才会创建。
