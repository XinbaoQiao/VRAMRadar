## VRAM Radar 0.9.7

Slurm capacity remains visible when other users' task details are hidden.

- Read allocated GPU counts from node-level `GresUsed`, independently of the task visibility setting. This fixes incorrect free GPU and scheduler VRAM totals on clusters whose `AllocTRES` omits GPU allocation.
- Preserve unknown allocation state when counters or permissions are unavailable, instead of reporting those GPUs as free.
- Keep other users' job details hidden when their display option is disabled.
- Simplify setup guidance and fix English accessibility labels for server navigation and reordering.

Publication requires source tests, native UI checks, Windows packaged checks, and native macOS checks on both Apple Silicon and Intel. The final combined macOS download is also validated on both architectures.

Downloads: `VRAMRadar-Setup-0.9.7.exe` for Windows and `VRAMRadar-0.9.7-macos.zip` for macOS (separate native Apple Silicon and Intel applications).

The Windows installer remains unsigned and may trigger SmartScreen. The macOS applications remain unsigned and unnotarized; use Finder's **Open** action on first launch. Do not disable SmartScreen or Gatekeeper globally.

---

## VRAM Radar 0.9.7 更新说明

关闭其他用户的任务详情后，仍能看到正确的 Slurm GPU 分配和调度显存占用。

- 使用节点级 `GresUsed` 统计已分配 GPU，与任务详情显示开关独立，修复部分集群的 `AllocTRES` 不含 GPU 时误报空闲的问题。
- 分配数据或权限不可用时保留未知状态，不将这些 GPU 当作空闲资源。
- 关闭显示选项时，其他用户的任务详情继续保持隐藏。
- 简化配置引导，修复服务器导航与排序的英文无障碍标签漏译。

发布需通过源码测试、原生界面检查、Windows 打包验证，以及 Apple Silicon 和 Intel 两种架构的 macOS 原生验证；最终合并的 macOS 下载包也在两种架构上验证。

下载：Windows 为 `VRAMRadar-Setup-0.9.7.exe`；macOS 为 `VRAMRadar-0.9.7-macos.zip`，包含 Apple Silicon 和 Intel 两套原生应用。

Windows 安装包未签名，可能出现 SmartScreen 提示。macOS 应用未签名、未公证，首次启动请使用 Finder 的“打开”操作。不要全局关闭 SmartScreen 或 Gatekeeper。
