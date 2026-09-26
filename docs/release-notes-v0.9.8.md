## VRAM Radar 0.9.8

This release brings together the pending SSH discovery and interface improvements with a reliability audit of configuration persistence and duplicate detection.

- Group duplicate SSH aliases during local discovery and offer a route choice or keep-all option. Live GPU UUID comparisons can identify untouched duplicate imports.
- Save duplicate choices from multi-file imports and retain each route's own SSH config file when switching or keeping all routes. Cancelled import choices no longer leak into the next settings session.
- Preserve saved-password routes, distinct private keys/certificates, and case-sensitive usernames. Unknown authentication configuration stays separate. Route changes use the same credential-binding checks as connection edits.
- Serialize background deduplication with settings writes without blocking refresh behind an editor commit. Consume all in-flight results before removing a duplicate, and never deduplicate from cached, failed, or disabled observations.
- Recover safely from non-UTF-8 window-state and snapshot-cache files; report a damaged Profile as a configuration error without overwriting it.
- Improve Chinese connection explanations and bilingual interface, notification, and update messages. Retain the earlier Slurm allocation fixes.

Publication requires the complete source tests, native synthetic UI checks, Windows package checks, native Apple Silicon and Intel macOS builds, and validation of the final combined macOS archive on both architectures.

Downloads: `VRAMRadar-Setup-0.9.8.exe` for Windows and `VRAMRadar-0.9.8-macos.zip` for macOS.

The Windows installer is unsigned and may trigger SmartScreen. The macOS apps are unsigned and unnotarized; use Finder's **Open** action on first launch. Do not disable SmartScreen or Gatekeeper globally.

---

## VRAM Radar 0.9.8 更新说明

本次合并此前尚未发布的 SSH 配置发现、重复别名选择和界面改进，并修复配置保存与后台去重中的潜在故障。

- 本地发现时整理重复 SSH 别名，可选择一条连接或全部保留；通过实时 GPU UUID 识别尚未编辑的重复导入项。
- 修复多文件导入后别名选择无法保存的问题。切换或全部保留时使用各自的 SSH 配置文件，取消导入后不会把旧选择带入下次设置。
- 保护已保存密码、不同私钥或证书的连接，正确区分大小写不同的用户名；无法确认认证配置时保留各条连接。切换连接沿用设置页的凭据绑定检查。
- 修复后台去重与保存设置冲突导致的配置覆盖或等待问题；所有采样返回后再处理重复项，离线、缓存和暂停状态不作为去重依据。
- 损坏的窗口状态或显存缓存不再阻碍读取；损坏的 Profile 会报告配置错误并保留原文件。
- 完善中文连接说明、中英文界面、通知与更新提示，保留此前的 Slurm 分配统计修复。

发布需通过完整源码测试、原生模拟界面检查、Windows 打包验证、Apple Silicon 与 Intel 原生构建，以及最终合并 macOS 下载包在两种架构上的验证。

下载：Windows 为 `VRAMRadar-Setup-0.9.8.exe`，macOS 为 `VRAMRadar-0.9.8-macos.zip`。

Windows 安装包未签名，可能出现 SmartScreen 提示。macOS 应用未签名、未公证，首次启动请使用 Finder 的“打开”操作。不要全局关闭 SmartScreen 或 Gatekeeper。
