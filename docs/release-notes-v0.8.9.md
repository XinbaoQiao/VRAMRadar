## VRAM Radar 0.8.9

This release makes Slurm discovery and failure reporting substantially more
portable across institutional clusters whose login environment differs from a
normal interactive shell.

When the initial Slurm commands are unavailable, wrapped, or incomplete, VRAM
Radar now tries standard Slurm locations, site environment scripts,
Environment Modules/Lmod, the account's configured shell, installation
metadata, and bounded Slurm-specific filesystem discovery. Administrators and
users can also configure a constrained module name, absolute Slurm binary
directory, or absolute initialization script for nonstandard sites.

Inventory and queue collection now degrade independently. If `sinfo` hides GPU
GRES data, VRAM Radar can use `scontrol show nodes -o`; if the cluster rejects a
global `squeue` query, it retries the current account and clearly marks the
limited scope. A restricted public gateway, ForceCommand menu, required second
hop, missing command, controller failure, permission denial, or incompatible
Slurm output now produces a bounded actionable reason instead of a generic
remote-command failure. Diagnostics retain only redacted stage, exit, and
capability information.

The fallback collector was exercised through real Bash behavior tests with
temporary Slurm installations and injected failures. These tests cover broken
PATH wrappers, configured binary override, `sinfo` to `scontrol` inventory
fallback, global-to-current-user queue fallback, and discovery through
`whereis`, environment prefixes, Spack, and RPM metadata. They do not claim
that every site-specific interactive gateway has been reproduced.

### Downloads

- Windows: `VRAMRadar-Setup-0.8.9.exe`.
- macOS: `VRAMRadar-0.8.9-macos.zip`.

The Windows installer is not Authenticode signed. The macOS apps are not
Developer ID signed or notarized; first launch may require Finder's
right-click **Open** action.

---

## VRAM Radar 0.8.9 中文说明

此版本增强了 Slurm 环境发现和失败诊断，主要解决学校或机构集群的登录环境与普通交互式
Shell 不一致时，SSH 已连接但 GPU 信息采集无法启动的问题。

当最初的 Slurm 命令不可用、被包装或信息不完整时，VRAM Radar 现在会尝试标准 Slurm
目录、站点环境脚本、Environment Modules/Lmod、账号默认 Shell、安装索引，以及有时限且
只面向 Slurm 安装位置的文件系统搜索。对于非标准平台，也可以安全配置模块名、Slurm
二进制绝对目录或环境初始化脚本。

节点和队列采集现在可以分别降级：`sinfo` 隐藏 GPU GRES 时可改用
`scontrol show nodes -o`；集群拒绝全局 `squeue` 时会退回当前账号范围，并明确提示
可见范围受限。公共平台菜单、ForceCommand 网关、必须二次跳转、命令缺失、控制器异常、
权限拒绝和 Slurm 输出不兼容也会分别给出经过脱敏的可操作原因，不再统一显示模糊的
远程命令失败。

本次回退采集器已通过真实 Bash 行为测试：测试会创建临时 Slurm 安装并注入 PATH 包装
命令损坏、配置目录覆盖、`sinfo` 到 `scontrol` 回退、全局到当前用户队列回退，以及
`whereis`、环境前缀、Spack 和 RPM 索引发现。不过，这不代表已经复现所有机构自定义的
交互式网关。

### 下载

- Windows：`VRAMRadar-Setup-0.8.9.exe`。
- macOS：`VRAMRadar-0.8.9-macos.zip`。

Windows 安装包尚未 Authenticode 签名；macOS 应用尚未 Developer ID 签名或公证，
首次启动可能需要在 Finder 中右击应用并选择“打开”。
