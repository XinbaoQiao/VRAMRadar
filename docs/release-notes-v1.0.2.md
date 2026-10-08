## VRAM Radar 1.0.2 更新说明

- **重新添加已移除的服务器**：点击“自动发现并导入”或手动导入配置时，会列出曾主动移除的服务器。勾选要恢复的服务器，点击“添加所选服务器”，再保存配置即可生效。未勾选的服务器继续保持移除，后台自动同步也不会将它们重新加入。
- **可折叠的恢复列表**：默认收起，只显示提示和数量；展开后列表高度受限，服务器较多时内部滚动。收起再展开保留勾选状态，取消设置会放弃本次恢复。
- **SSH 恢复支持**：保留已配置的主机访问恢复集成，仅在明确的公钥认证拒绝后触发已批准的外部恢复程序；普通连接和未配置的服务器不受影响。
- **macOS 退出修复**：关闭窗口前等待正在返回的界面调用，避免退出时进程因原生回调未结束而卡住。
- 包含 1.0.1 的额度监控、任务栏交互和稳定性修复。

下载：Windows x64 为 `VRAMRadar-Setup-1.0.2.exe`，macOS 为包含 Apple Silicon 与 Intel 原生应用的 `VRAMRadar-1.0.2-macos.zip`。旧版用户可通过应用内更新获取此版本。

Windows 安装程序未签名；macOS 应用未签名、未经公证。首次启动如遇提示，请使用 Finder 的“打开”操作；不要全局关闭 SmartScreen 或 Gatekeeper。

---

## VRAM Radar 1.0.2 release notes

- **Restore removed servers**: automatic discovery and manual configuration import now list previously removed servers separately. Select the servers to restore, click **Add selected servers**, then save settings. Unselected servers remain removed, including during background synchronization.
- **Collapsible recovery list**: collapsed by default with a server count. The expanded list scrolls within a bounded height. Collapsing preserves selections; cancelling settings discards the draft.
- **SSH recovery support**: retains the configured host-access recovery integration. Only an explicit public-key authentication rejection invokes an approved external recovery helper; ordinary connections and unconfigured servers are unaffected.
- **macOS shutdown fix**: drain active native interface calls before destroying the window so pending replies cannot leave the process stuck during exit.
- Includes the quota monitoring, taskbar interaction and stability fixes from 1.0.1.

Downloads: `VRAMRadar-Setup-1.0.2.exe` for Windows x64 and `VRAMRadar-1.0.2-macos.zip` containing native Apple Silicon and Intel apps. Earlier versions can update through the app.

The Windows installer is unsigned; macOS apps are unsigned and unnotarized. Use Finder's **Open** action for first launch if prompted. Do not disable SmartScreen or Gatekeeper globally.
