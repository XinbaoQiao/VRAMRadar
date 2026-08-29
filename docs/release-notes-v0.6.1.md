## VRAM Radar 0.6.1

This stable maintenance release makes update notifications independent from
server availability.

### Downloads

- Windows: `VRAMRadar-Setup-0.6.1.exe`.
- macOS: `VRAMRadar-0.6.1-macos.zip`, containing separately built and validated
  Apple Silicon arm64 and Intel x86_64 applications.

### Fixed

- The GitHub update check now starts immediately after the desktop bridge is
  ready instead of waiting for the first SSH/GPU refresh to finish.
- A failed GitHub check is no longer silently discarded. The app shows a short
  failure message and a visible retry action without interrupting monitoring.
- Failed checks retry after five minutes. Successful checks repeat every six
  hours, and returning to a long-running app triggers a fresh check when the
  previous result is old.
- The notification still only offers a download. VRAM Radar never silently
  downloads or installs executable code.

### Platform security boundary

The Windows installer is not Authenticode signed. The macOS apps are not
Developer ID signed or notarized because distribution credentials are not
configured for this project. Verify that the package came from this repository's
Latest Release and follow the scoped first-launch instructions in the README;
do not disable platform security globally.

---

## VRAM Radar 0.6.1 中文说明

这个正式维护版本让更新提醒不再受服务器连接状态影响。

### 下载

- Windows：`VRAMRadar-Setup-0.6.1.exe`。
- macOS：`VRAMRadar-0.6.1-macos.zip`，内含分别构建和验证的 Apple Silicon
  arm64 与 Intel x86_64 应用。

### 修复内容

- 桌面桥接准备好后立即检查 GitHub 更新，不再等待首轮 SSH/GPU 刷新。
- GitHub 检查失败不再静默忽略：界面会显示简短原因和“重试”按钮，但不影响
  服务器监控。
- 失败后五分钟自动重试；成功后每六小时复查。长时间运行的应用重新获得焦点、
  且上次检查已经过期时，也会再次检查。
- 更新通知仍然只提供下载入口，不会在后台静默下载或安装可执行文件。

### 平台安全边界

Windows 安装器尚未使用 Authenticode 签名；macOS 应用也尚未使用 Developer ID
签名或公证，因为项目没有配置发行凭据。请确认安装包来自本仓库 Latest Release，
再按 README 的最短首次启动步骤操作；不要全局关闭系统安全保护。
