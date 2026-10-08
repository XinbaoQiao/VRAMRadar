## VRAM Radar 1.0.3 更新说明

- **任务信息补充查询**：常规进程查询失败时，从可访问的 `/proc/<PID>` 读取用户和命令行；空命令行回退到进程名。读取前后核对进程身份，避免 PID 复用导致错配。
- **运行时间补充查询**：运行时长返回 0 或缺失时，尝试通过 `ps lstart` 和服务器当前时间计算，统一时区，并拒绝无效或未来的启动时间。
- **更准确的不可用提示**：区分 PID 不可见、进程信息不可读和查询不可用，不再统一标记为权限受限。GPU 显存信息保留。
- **降低断线重试压力**：自动失败重试不短于正常刷新间隔，连续失败逐步退避；正常刷新和手动刷新行为保持不变。
- 包含 1.0.2 的可折叠已移除服务器恢复列表及稳定性修复。

边界：容器内 root 不代表能访问宿主机的所有进程。当前 SSH 环境不可见的 PID 仍需宿主机侧信息来源；本版本不保证恢复所有隔离进程的任务名或时间。重试调整不代表已确定历史服务器故障的原因。

下载：`VRAMRadar-Setup-1.0.3.exe`（Windows x64）和 `VRAMRadar-1.0.3-macos.zip`（Apple Silicon / Intel）。Windows 安装程序未签名；macOS 应用未签名、未经公证。首次启动如遇提示，请使用 Finder 的“打开”操作；不要全局关闭 SmartScreen 或 Gatekeeper。

---

## VRAM Radar 1.0.3 release notes

- Recover readable process owners and commands from `/proc/<PID>` when the usual query fails, with process-name fallback and start-tick checks against PID reuse.
- Supplement zero or missing elapsed times with `ps lstart` and the server wall clock in a consistent timezone; reject invalid or future start times.
- Distinguish invisible PIDs, unreadable metadata and unavailable queries instead of labelling all missing details as permission restrictions. Preserve GPU memory readings.
- Automatic failure retries now wait at least the configured refresh interval and retain progressive backoff. Normal polling and manual refresh behavior are unchanged.
- Includes the collapsible removed-server recovery list and stability fixes from 1.0.2.

Limitations: container root access does not expose every host process. PIDs invisible to the SSH environment still require a host-side data source. This release does not guarantee recovery of every isolated task or establish the cause of previous server outages.

Downloads: `VRAMRadar-Setup-1.0.3.exe` (Windows x64) and `VRAMRadar-1.0.3-macos.zip` (Apple Silicon / Intel). Windows installer unsigned; macOS apps unsigned and unnotarized. Use Finder's Open action if prompted; do not disable SmartScreen or Gatekeeper globally.
