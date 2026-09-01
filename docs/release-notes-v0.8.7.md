## VRAM Radar 0.8.7

This patch fixes the movable sidebar and makes command summaries easier to use.

Moving the Mini Navigator to the left no longer lets browser grid placement
push it below the full dashboard. Both sides now share the same fixed desktop
row, so switching sides remains immediate even on long server pages.

The watched-task filter is now labeled **Watch** (`关注` in Chinese). When the
sidebar collapses, watch mode keeps a dedicated bell and watched-count badge in
the compact rail instead of clipping a wide task heading. The marker is
geometry-validated on both the left and right sides, including narrow-window
layout.

Available process command summaries now start expanded. New servers and legacy
profiles without an explicit preference show locally redacted, length-limited
summaries for other users by default; an existing explicit opt-out remains
unchanged.

### Downloads

- Windows: `VRAMRadar-Setup-0.8.7.exe`.
- macOS: `VRAMRadar-0.8.7-macos.zip`.

The Windows installer is not Authenticode signed. The macOS apps are not
Developer ID signed or notarized; first launch may require Finder's
right-click **Open** action.

---

## VRAM Radar 0.8.7 中文说明

此补丁修复可移动侧边栏，并让命令摘要默认更容易查看。

Mini Navigator 移到左侧时，不会再被浏览器网格自动排版放到整个仪表盘下方。左右两侧
现在固定在同一个桌面网格行，即使服务器页面很长，切换位置也能立即完成。

“关注任务”入口缩短为“关注”。侧边栏收起后，关注模式会保留专用铃铛和关注数量
徽标，不再截断较宽的任务标题。左右两侧和窄窗口布局均通过原生几何验证。

可用的进程命令摘要现在默认展开。新服务器以及未明确保存该选项的旧 Profile，会默认
显示经过本地遮盖和限长的其他用户命令摘要；用户已经明确关闭的选择不会被覆盖。

### 下载

- Windows：`VRAMRadar-Setup-0.8.7.exe`。
- macOS：`VRAMRadar-0.8.7-macos.zip`。

Windows 安装包尚未 Authenticode 签名；macOS 应用尚未 Developer ID 签名或公证，
首次启动可能需要在 Finder 中右击应用并选择“打开”。
