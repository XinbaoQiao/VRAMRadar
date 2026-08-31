## VRAM Radar 0.8.4

This release makes alerts recoverable and repairs update checks in the packaged
macOS apps.

The bell beside Refresh is now an always-visible notification center. Task
completions, favorite-GPU availability, and saved GPU-condition matches share
one recent-history list with a durable unread state. Task activity baselines
are stored outside the app package: if a task was observed active in a live
snapshot before shutdown and is gone on the next live refresh, VRAM Radar adds
the missed completion to the center and sends the native system notification.
Cached and offline snapshots cannot claim a completion.

Current-account task completion alerts remain enabled by default. Any visible
task from another user can be watched individually, but other users are never
auto-watched. Individual watches have direct Remove actions plus one Remove all
action in Settings. The server editor also keeps paired connection fields on
the same row baseline and returns shared OpenSSH guidance to the full grid edge.

Apple Silicon and Intel builds now verify GitHub HTTPS through the native macOS
Security framework instead of depending on a Python bundle-specific CA path.
Finder launches no longer need an `SSL_CERT_FILE` workaround.

Manual checks now distinguish certificate, DNS, timeout, rate-limit, HTTP, and
untrusted-metadata failures without exposing proxy addresses or certificate
details. The native release gate runs the packaged app's real GitHub update
transport on both architectures and repeats that check after the final combined
ZIP is extracted.

The installation boundary is unchanged: macOS downloads are verified with the
exact GitHub size and SHA-256 digest and then revealed in Finder for manual app
replacement. Windows installer-managed copies retain safe one-click update,
rollback, Profile preservation, and automatic restart.

### Downloads

- Windows: `VRAMRadar-Setup-0.8.4.exe`.
- macOS: `VRAMRadar-0.8.4-macos.zip`.

The Windows installer is not Authenticode signed. The macOS apps are not
Developer ID signed or notarized; first launch may require Finder's
right-click **Open** action.

---

## VRAM Radar 0.8.4 中文说明

此版本让提醒可恢复，并修复打包后 macOS 应用的更新检查。

“立即刷新”旁的铃铛现在常驻，作为统一通知中心。任务完成、收藏 GPU 可用、资源
条件满足都会进入同一份最近记录，并持久保存未读状态。如果任务曾在关机或退出前
被实时快照确认仍在运行，而下次启动后的实时刷新发现它已经结束，软件会补记完成
消息并发送系统通知；缓存或离线快照不能据此判定任务完成。

当前账号的任务完成提醒仍默认启用。其他用户的可见任务可以逐项手动关注，但绝不
默认自动关注；设置中既保留单项“移除”，也新增“一键全部移除”。
服务器编辑器的成对连接字段也已恢复同一行基线，OpenSSH 共用说明与整个字段网格
左边界对齐。

Apple Silicon 与 Intel 版本现在
通过 macOS Security framework 的系统证书信任验证 GitHub HTTPS，不再依赖
Python 打包环境中的 CA 路径；从 Finder 正常启动时无需再设置 `SSL_CERT_FILE`。

手动检查现在能够区分证书、DNS、超时、限流、HTTP 和不可信 Release 元数据，
同时不会暴露代理地址或证书细节。原生发布门槛会在两个架构上直接运行打包应用的
GitHub 更新链路，并在最终合并 ZIP 解压后再次验证。

安装边界保持不变：macOS 更新包会核对 GitHub 记录的准确大小与 SHA-256，随后在
Finder 中显示，由用户手动替换应用；使用安装器管理的 Windows 版本继续支持安全
一键更新、失败回滚、Profile 保留和自动重启。

### 下载

- Windows：`VRAMRadar-Setup-0.8.4.exe`。
- macOS：`VRAMRadar-0.8.4-macos.zip`。

Windows 安装包尚未 Authenticode 签名；macOS 应用尚未 Developer ID 签名或公证，
首次启动可能需要在 Finder 中右击应用并选择“打开”。
