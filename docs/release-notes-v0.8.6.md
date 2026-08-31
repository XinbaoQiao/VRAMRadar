## VRAM Radar 0.8.6

This release makes task completion notifications reliable and consolidates
task, GPU availability, and update messages in one notification center.

Task completion is now written to durable notification state before the native
popup is attempted. A failed popup remains pending and is retried with bounded
backoff during the same app session, as well as after the notification surface
is restored on a later launch. Direct GPU process watches no longer report a
false completion when process metadata is temporarily unavailable, and
PID-only watches distinguish reused process generations.

The notification bell is always visible. Task completion, favorite GPU
availability, saved resource matches, and version updates all appear in the
same center with unread state. Version download and install actions now live in
the update notification instead of a separate banner.

Watched tasks have moved from Settings to the sidebar in place of Recent. The
list starts collapsed, supports individual removal and one-click bulk removal,
and allows another user's task to be watched only through an explicit
per-task action. Watch and removal changes repaint immediately without waiting
for the next server refresh.

The English interface now covers generic GPU memory units and every Slurm node
state, and long status labels wrap instead of overflowing.

### Downloads

- Windows: `VRAMRadar-Setup-0.8.6.exe`.
- macOS: `VRAMRadar-0.8.6-macos.zip`.

The Windows installer is not Authenticode signed. The macOS apps are not
Developer ID signed or notarized; first launch may require Finder's
right-click **Open** action.

---

## VRAM Radar 0.8.6 中文说明

此版本重点修复任务完成提醒的可靠性，并将任务完成、GPU 可用和版本更新消息统一到
同一个通知中心。

任务完成事件现在会先写入本地持久化通知状态，再尝试弹出系统通知。系统弹窗失败后
仍会保持待发送状态，在当前运行期间按退避策略重试；软件重启并恢复通知能力后也会
补发。SSH 直连进程信息暂时不可用时不再误报任务完成；缺少启动时间的 PID 任务也能
识别 PID 被复用后的新旧进程代次。

通知铃铛现在常驻。任务完成、收藏 GPU 可用、资源条件满足和版本更新都会进入统一
通知中心，并保留未读状态。版本下载和安装操作也已从独立顶部横幅移入版本通知。

“关注任务”已从设置页移动到侧边栏并替换“最近”入口。关注列表默认折叠，支持逐项
移除和一键清空；其他用户的任务只有在用户主动逐项选择后才会提醒。关注和移除操作
会立即更新界面，不再等待服务器刷新。

英文界面同时补全了通用 GPU 显存单位和全部 Slurm 节点状态，长状态文案可以换行，
不会再溢出或被裁切。

### 下载

- Windows：`VRAMRadar-Setup-0.8.6.exe`。
- macOS：`VRAMRadar-0.8.6-macos.zip`。

Windows 安装包尚未 Authenticode 签名；macOS 应用尚未 Developer ID 签名或公证，
首次启动可能需要在 Finder 中右击应用并选择“打开”。
