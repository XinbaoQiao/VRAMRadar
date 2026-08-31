## VRAM Radar 0.8.2

This release removes repeated first-use SSH Host Key prompts without tying the
application to a fixed installation directory.

### Downloads

- Windows: `VRAMRadar-Setup-0.8.2.exe`.
- macOS: `VRAMRadar-0.8.2-macos.zip`, containing separately built Apple Silicon
  arm64 and Intel x86_64 applications.

### Host Key behavior

- Ordinary monitoring now uses OpenSSH `StrictHostKeyChecking=accept-new`.
  Previously unknown keys are saved automatically in the user's configured
  `known_hosts` file.
- A changed Host Key is still rejected. VRAM Radar does not use
  `StrictHostKeyChecking=no` and does not bypass OpenSSH's replacement/MITM
  protection.
- The redundant **Trust and connect** confirmation flow has been removed. If
  OpenSSH still cannot save or verify a key, the error now points to
  `known_hosts` permissions or SSH configuration instead of asking for the same
  confirmation again.

### Installation directories

- Windows Setup continues to show the destination picker and supports any
  writable user-owned directory, including `D:\Download\VRAM Radar`.
- The executable directory does not own the Profile or OpenSSH trust files.
  Updates preserve the selected application directory while user data remains
  in the platform-owned data and SSH locations.

### Platform security boundary

The Windows installer is not Authenticode signed. The macOS apps are not
Developer ID signed or notarized. Verify the Latest Release origin and follow
the scoped first-launch guidance in the README; do not disable platform
security globally.

---

## VRAM Radar 0.8.2 中文说明

此版本去掉 SSH 首次连接时反复出现的 Host Key 确认，同时继续支持用户自由选择
程序安装目录。

### 下载

- Windows：`VRAMRadar-Setup-0.8.2.exe`。
- macOS：`VRAMRadar-0.8.2-macos.zip`，内含分别构建的 Apple Silicon arm64
  与 Intel x86_64 应用。

### Host Key 行为

- 普通监控连接默认使用 OpenSSH `StrictHostKeyChecking=accept-new`，首次出现的
  Host Key 会自动保存到用户配置的 `known_hosts`。
- 已记录的 Host Key 如果发生变化，连接仍会被阻止。显存雷达不会使用
  `StrictHostKeyChecking=no`，也不会绕过 OpenSSH 对服务器替换和中间人攻击的
  防护。
- 已移除重复的“信任并连接”确认流程。如果 OpenSSH 仍无法保存或验证 Host Key，
  界面会提示检查 `known_hosts` 权限或 SSH 配置，不再要求重复确认。

### 安装目录

- Windows 安装器继续显示目标目录选择页，支持任意当前用户可写目录，包括
  `D:\Download\VRAM Radar`。
- 程序目录不负责保存 Profile 或 OpenSSH 信任文件。更新会保留用户选择的程序
  目录，用户数据仍位于系统管理的应用数据和 SSH 目录。

### 平台安全边界

Windows 安装包尚未 Authenticode 签名；macOS 应用尚未 Developer ID 签名或公证。
请确认下载来自 Latest Release，并按 README 中的有限首次启动说明操作；不要全局关闭
平台安全机制。
