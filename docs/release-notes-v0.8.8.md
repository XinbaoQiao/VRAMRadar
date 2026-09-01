## VRAM Radar 0.8.8

This release completes the English interface, adds a durable notification-clear
action, and exposes optional CPU context for direct SSH servers.

English mode now translates stale-data warnings, connection timeout details,
cached-snapshot explanations, notification text, and the smaller labels inside
direct GPU-process tables. Notification history can be cleared from the
notification center without resetting task-watch or favorite-GPU baselines, and
the open list updates immediately after a successful clear.

Direct SSH servers can now show logical CPU cores, 1/5/15-minute load averages,
and per-process CPU usage alongside GPU memory. Missing or permission-limited CPU
metadata is shown as unavailable instead of being misreported as 0%.

### Downloads

- Windows: `VRAMRadar-Setup-0.8.8.exe`.
- macOS: `VRAMRadar-0.8.8-macos.zip`.

The Windows installer is not Authenticode signed. The macOS apps are not
Developer ID signed or notarized; first launch may require Finder's
right-click **Open** action.

---

## VRAM Radar 0.8.8 中文说明

此版本补全英文界面，增加可持久清空的通知历史，并为 Direct SSH 服务器显示可选的
CPU 信息。

英文模式现在会翻译数据过期、连接超时、旧快照说明、通知内容，以及 GPU 进程表中的
细节标签。通知中心可以直接清空历史记录，且不会重置任务关注或收藏 GPU 的判断基线；
清空成功后，当前打开的列表会立即更新。

Direct SSH 服务器现在可以显示 CPU 逻辑核心数、1/5/15 分钟系统负载和各 GPU 进程的
CPU 使用率。缺失或因权限受限而不可读取的 CPU 数据会显示为“不可用”，不会误报为
`0%`。

### 下载

- Windows：`VRAMRadar-Setup-0.8.8.exe`。
- macOS：`VRAMRadar-0.8.8-macos.zip`。

Windows 安装包尚未 Authenticode 签名；macOS 应用尚未 Developer ID 签名或公证，
首次启动可能需要在 Finder 中右击应用并选择“打开”。
