## VRAM Radar 1.0.3 更新说明

- **同版本归属修复**：识别容器内独立的 PID 视图，避免把同号但不同身份的进程误标为 GPU 进程所有者。存在可读取且经过核验的宿主机 procfs 时，补充所属用户，并同时显示 GPU PID 与当前环境 PID。
- **映射缺失时的独立进程视图**：保留原 GPU 显存和“归属不可见”状态，明确提示容器 PID 映射未提供；另列当前账号打开 NVIDIA 计算设备的进程，不推断其显存归属，也不把设备访问当作正在计算的证明。
- **任务信息补充查询**：常规进程查询失败时，从可访问的 `/proc/<PID>` 读取用户和命令行；空命令行回退到进程名。读取前后核对进程身份，避免 PID 复用导致错配。
- **运行时间补充查询**：运行时长返回 0 或缺失时，尝试通过 `ps lstart` 和服务器当前时间计算，统一时区，并拒绝无效或未来的启动时间。
- **更准确的不可用提示**：区分 PID 不可见、进程信息不可读和查询不可用，不再统一标记为权限受限。GPU 显存信息保留。
- **降低断线重试压力**：自动失败重试不短于正常刷新间隔，连续失败逐步退避；正常刷新和手动刷新行为保持不变。
- 包含 1.0.2 的可折叠已移除服务器恢复列表及稳定性修复。

边界：容器内 root 不代表能访问宿主机的所有进程。完全隔离且没有可读取宿主机信息或映射接口的环境，GPU 进程归属仍不可确认。本版本不保证恢复所有隔离进程的归属、任务名或时间；不会修改宿主机权限或挂载。重试调整不代表已确定历史服务器故障的原因。

下载：`VRAMRadar-Setup-1.0.3.exe`（Windows x64）和 `VRAMRadar-1.0.3-macos.zip`（Apple Silicon / Intel）。Windows 安装程序未签名；macOS 应用未签名、未经公证。首次启动如遇提示，请使用 Finder 的“打开”操作；不要全局关闭 SmartScreen 或 Gatekeeper。

---

## VRAM Radar 1.0.3 release notes

- Same-version ownership repair: detect isolated container PID views and reject equal-number PIDs with different identities. Recover owners from a verified readable host procfs when available, while retaining both the GPU PID and the PID visible in the current environment.
- Without verified mapping, preserve GPU memory and unknown ownership with an explicit container mapping explanation. Separately list current-account processes with open NVIDIA compute-device descriptors, without assigning GPU memory or treating device access as proof of active computation.
- Recover readable process owners and commands from `/proc/<PID>` when the usual query fails, with process-name fallback and start-tick checks against PID reuse.
- Supplement zero or missing elapsed times with `ps lstart` and the server wall clock in a consistent timezone; reject invalid or future start times.
- Distinguish invisible PIDs, unreadable metadata and unavailable queries instead of labelling all missing details as permission restrictions. Preserve GPU memory readings.
- Automatic failure retries now wait at least the configured refresh interval and retain progressive backoff. Normal polling and manual refresh behavior are unchanged.
- Includes the collapsible removed-server recovery list and stability fixes from 1.0.2.

Limitations: container root access does not expose every host process. Fully isolated environments without readable host information or a mapping interface still have unknown GPU process ownership. This release does not guarantee recovery of every isolated owner, task or elapsed time, modify host permissions or mounts, or establish the cause of previous server outages.

Downloads: `VRAMRadar-Setup-1.0.3.exe` (Windows x64) and `VRAMRadar-1.0.3-macos.zip` (Apple Silicon / Intel). Windows installer unsigned; macOS apps unsigned and unnotarized. Use Finder's Open action if prompted; do not disable SmartScreen or Gatekeeper globally.
