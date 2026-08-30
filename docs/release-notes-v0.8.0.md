## VRAM Radar 0.8.0

This release makes resource awareness useful immediately after setup and closes
the configuration-state and Harness-catalog gaps found during the 0.7.0 review.

### Downloads

- Windows: `VRAMRadar-Setup-0.8.0.exe`.
- macOS: `VRAMRadar-0.8.0-macos.zip`, containing separately built and validated
  Apple Silicon arm64 and Intel x86_64 applications.

### Default task awareness

- Favorite GPU availability alerts are enabled by default for new Profiles.
  They remain transition-based and do nothing until a server is favorited.
- Newly added or imported servers enable other-user task and command summaries
  by default. Existing explicit opt-outs remain off.
- Direct SSH shows active GPU processes and locally redacted, length-limited
  command previews. It does not claim scheduler history.
- Slurm exposes the same setting instead of hiding it. It shows scheduler job
  names, states, users, nodes, and timing, including recent results when `sacct`
  is available; it does not read or claim full shell commands.

### Clear configuration state

- A newly saved server now displays **Configuring** with a progress panel while
  the first SSH/backend/GPU snapshot is being established. It is no longer
  rendered through the error panel before validation has finished.
- Genuine offline, authentication, Host Key, and invalid-configuration failures
  keep their existing actionable error states.

### Direct ordering and global interface polish

- Added servers can now be reordered directly by dragging the handle at the
  left of each row. Keyboard users can focus the same handle and press Up or
  Down; search temporarily disables ordering so the result is unambiguous.
- Removed the repetitive move-up and move-down controls and tightened the full
  application layout—not only Settings—with more consistent spacing, type
  hierarchy, button weight, color, and visual emphasis.
- Server, GPU, process, task, setup, and update details remain available; the
  polish reduces decoration and redundant actions rather than hiding useful
  information.

### Server catalog compatibility

- Portable Harness `servers.toml` versions 2 and 3 are accepted. The current
  Harness v3 catalog can therefore synchronize directly instead of falling back
  to an OpenSSH-only recovery path.
- Catalog import remains local, bounded, credential-field rejecting, and does
  not contact a configured server.

### Platform security boundary

The Windows installer is not Authenticode signed. The macOS apps are not
Developer ID signed or notarized. Verify the Latest Release origin and follow
the scoped first-launch guidance in the README; do not disable platform
security globally.

---

## VRAM Radar 0.8.0 中文说明

此版本让新配置完成后立即具备任务感知能力，并修复 0.7.0 复查时发现的配置中
状态与 Harness catalog 兼容性问题。

### 下载

- Windows：`VRAMRadar-Setup-0.8.0.exe`。
- macOS：`VRAMRadar-0.8.0-macos.zip`，内含分别构建并验证的 Apple Silicon
  arm64 与 Intel x86_64 应用。

### 默认任务感知

- 新 Profile 默认开启收藏 GPU 可用提醒；没有收藏服务器时不会产生通知，每次从
  不可用变为可用仍只提醒一次。
- 新添加或新导入的服务器默认开启其他用户任务与命令摘要；旧 Profile 中明确关闭
  的选择继续保持关闭。
- SSH 直连显示活动 GPU 进程以及在本地完成敏感参数遮盖和长度限制的命令摘要，
  不把快照描述成调度历史。
- Slurm 不再隐藏同一设置。它显示作业名、状态、用户、节点和时间；`sacct` 可用时
  还显示近期结果，但不会读取或声称展示完整 shell 命令。

### 清晰的配置中状态

- 新保存的服务器在建立第一份 SSH、后端和 GPU 有效快照期间显示“正在配置中”
  及进度说明，不再在验证尚未结束时使用错误面板。
- 真正的网络、认证、Host Key 和无效配置问题仍保留原有可操作错误状态。

### 直接排序与全局界面微调

- “已添加的服务器”现在可直接拖动每行左侧手柄排序；键盘用户聚焦同一手柄后可按
  上下方向键。搜索期间会暂时禁用排序，避免局部结果中的顺序含义不清。
- 移除了重复的“上移 / 下移”操作，并统一微调整个应用，而不只是设置页：布局密度、
  留白、字体层级、按钮权重、配色和视觉重点现在更一致。
- 服务器、GPU、进程、任务、配置与更新等有效信息仍完整保留；此次调整减少的是装饰
  和冗余操作，不会以隐藏重要内容换取简洁。

### 服务器 catalog 兼容性

- 现在同时接受便携 Harness `servers.toml` version 2 和 3；当前 Harness v3
  catalog 可直接同步，不再降级到仅 OpenSSH 的恢复路径。
- catalog 导入仍只在本地进行，限制大小、拒绝凭据字段，并且不会连接服务器。

### 平台安全边界

Windows 安装器尚未使用 Authenticode 签名；macOS 应用也未使用 Developer ID
签名或公证。请确认安装包来自 Latest Release，并按 README 的最短首次启动说明
操作；不要全局关闭平台安全保护。
