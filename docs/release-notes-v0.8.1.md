## VRAM Radar 0.8.1

This release improves connection portability, adds default-on completion alerts
for the current user's work, and keeps update checks silent when no action is
needed.

### Downloads

- Windows: `VRAMRadar-Setup-0.8.1.exe`.
- macOS: `VRAMRadar-0.8.1-macos.zip`, containing separately built Apple Silicon
  arm64 and Intel x86_64 applications.

### Portable SSH and Slurm setup

- New and OpenSSH-imported servers use **Auto-detect** by default. Saving the
  configuration validates the exact collector and can switch between direct
  `nvidia-smi` monitoring and Slurm when the initial choice is incompatible.
- Direct SSH and Slurm remain explicit manual choices when automatic validation
  cannot decide or the user needs to override it.
- OpenSSH discovery still follows bounded local `Include` files without running
  configuration commands. When one existing static `IdentityFile` can be
  confirmed, its expanded path is carried into the server editor; ambiguous or
  dynamic key choices remain under OpenSSH or manual control.
- Existing reviewed connection choices, key overrides, and password safety
  boundaries remain preserved during catalog synchronization.

### Task completion alerts

- Completion alerts for the current account's Slurm jobs and direct GPU
  processes are enabled by default and may be disabled in Settings.
- A bell action on each owned active task or process allows one or more items to
  be watched individually, including when the global completion toggle is off.
- Completed work produces a native notification and an in-app unread badge.
  Offline, stale, paused, and first-observed snapshots do not create false
  completion events.

### Quiet update checks

- The top update notice is now visible only when a newer release is available.
- Automatic no-update and network-failure results stay silent.
- Settings includes **Check for updates**. A manual latest-version result is
  shown briefly and then returns to the quiet explanatory state.

### Platform security boundary

The Windows installer is not Authenticode signed. The macOS apps are not
Developer ID signed or notarized. Verify the Latest Release origin and follow
the scoped first-launch guidance in the README; do not disable platform
security globally.

---

## VRAM Radar 0.8.1 中文说明

此版本提升不同用户环境下的连接兼容性，默认提供当前账号任务完成提醒，并让
“没有需要处理的更新”保持安静。

### 下载

- Windows：`VRAMRadar-Setup-0.8.1.exe`。
- macOS：`VRAMRadar-0.8.1-macos.zip`，内含分别构建的 Apple Silicon arm64
  与 Intel x86_64 应用。

### SSH 与 Slurm 连接兼容性

- 新建和从 OpenSSH 导入的服务器默认使用“自动识别”。保存配置时会实际验证精确
  采集器；初始类型不兼容时，可在 SSH 直连和 Slurm 之间自动切换。
- 自动验证无法判断或用户需要覆盖时，仍可明确手动选择 SSH 直连或 Slurm。
- OpenSSH 发现继续以不执行配置命令的方式安全解析有界 `Include`。如果能唯一确认
  一个已存在的静态 `IdentityFile`，会展开并填入服务器编辑器；动态或有歧义的密钥
  继续交给 OpenSSH 或用户手动选择。
- catalog 同步继续保留用户已经确认的连接类型、私钥覆盖和密码安全边界。

### 任务完成提醒

- 当前账号的 Slurm 作业和直连 GPU 进程结束提醒默认开启，可在设置中关闭。
- 每个自己的活动任务或进程都有关注按钮，可同时单独关注多个项目；即使关闭全局
  提醒，被单独关注的项目结束时仍会提醒。
- 任务结束时发送系统通知，并在应用顶部显示未读标记。离线、旧快照、暂停监控和
  第一次观察到的任务不会误报为完成。

### 安静的更新检查

- 只有检测到新版本时才显示顶部更新提示。
- 自动检查没有更新或网络失败时保持静默。
- 设置中新增“检查更新”；手动检查得到“已是最新版本”后只短暂反馈，随后恢复安静。

### 平台安全边界

Windows 安装包尚未 Authenticode 签名；macOS 应用尚未 Developer ID 签名或公证。
请确认下载来自 Latest Release，并按 README 中的有限首次启动说明操作；不要全局关闭
平台安全机制。
