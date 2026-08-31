## VRAM Radar 0.8.5

This maintenance release makes task-watch changes immediate and repairs the
English Slurm node table.

Watching or removing a task now updates its button and the Settings watch list
as soon as the local Profile save succeeds. The UI no longer waits for a new
server snapshot; monitoring refreshes remain responsible only for live task
state.

The English interface now translates per-GPU memory units for every GPU model,
not only A100 rows, and covers every Slurm node state. The node-state column has
also been widened and can wrap long English labels instead of clipping them.
Synthetic WebView validation now includes R2080, TITANX, H100, allocated,
partially allocated, and idle rows, checks the full rendered English interface
for Chinese text, verifies state-cell geometry, and proves watch add/remove
repaints without changing the server data revision.

The installation boundary is unchanged: Windows installer-managed copies keep
safe one-click update and rollback. macOS downloads are size- and SHA-256
verified and then revealed in Finder for manual replacement.

### Downloads

- Windows: `VRAMRadar-Setup-0.8.5.exe`.
- macOS: `VRAMRadar-0.8.5-macos.zip`.

The Windows installer is not Authenticode signed. The macOS apps are not
Developer ID signed or notarized; first launch may require Finder's
right-click **Open** action.

---

## VRAM Radar 0.8.5 中文说明

这是一个修复版本：关注任务的状态现在会立即更新，同时修复英文 Slurm 节点表中的
中文残留和长文案裁切。

关注或移除任务后，只要本地 Profile 保存成功，任务按钮和设置中的关注列表就会立即
同步，不再等待下一轮服务器快照。服务器刷新现在只负责确认任务的实时状态。

英文界面中的单卡显存单位现在适用于所有 GPU 型号，不再只覆盖 A100；所有 Slurm
节点状态也都有完整英文映射。节点状态列已重新分配宽度，较长的英文状态可以换行，
不会再被裁切。

合成 WebView 回归现在同时覆盖 R2080、TITANX、H100，以及已分配、部分占用、空闲
等状态；它会扫描完整渲染后的英文界面是否残留中文，检查状态单元格几何尺寸，并
验证关注和移除在服务器数据版本不变时仍能立即重绘。

安装边界保持不变：Windows 安装器版本继续支持安全的一键更新和失败回滚；macOS
下载会核对大小与 SHA-256，随后在 Finder 中显示，由用户手动替换应用。

### 下载

- Windows：`VRAMRadar-Setup-0.8.5.exe`。
- macOS：`VRAMRadar-0.8.5-macos.zip`。

Windows 安装包尚未 Authenticode 签名；macOS 应用尚未 Developer ID 签名或公证，
首次启动可能需要在 Finder 中右击应用并选择“打开”。
