## VRAM Radar 0.9.3

This release packages per-GPU favorites with orientation UX so multi-server fleets stay easy to scan, leave, and return to.

### What changed

- Favorite individual GPUs as well as whole servers; the navigator Favorites filter includes both, with badges that distinguish whole-server favorites from “has favorite GPU”.
- Leaving a server (navigator click / prev-next / active-server change) remembers the last open module and relative scroll; returning restores that place without fighting collapse-anchor or quiet expand.
- Location strip stays on the stuck header and also appears for the active server when its card intersects the upper viewport; the active navigator item shows `· module name` when known.
- One-click **收起模块 / Collapse modules** on each server head closes that card’s open modules using collapse-anchor so the viewport does not jump.
- Keeps the quieter expand / collapse-anchor / stuck-header chrome from v0.9.2; formal skin unchanged (no console left-rail / kpi-strip / overview-gpu-grid).

### Validation

- `tests.test_web_ui` (71 tests) plus favorites-related `tests.test_models_storage`, `tests.test_service`, and `tests.test_shell` on Windows.
- `node --check` on `app.js` / `localization.js`.
- Local packaged `dist/VRAMRadar/VRAMRadar.exe` smoke launch after `Build-VramRadar.ps1 -SkipSync`.

### Downloads and trust boundary

- Windows: `VRAMRadar-Setup-0.9.3.exe`.
- macOS: `VRAMRadar-0.9.3-macos.zip`, containing separate native Apple Silicon and Intel applications.

The Windows installer remains unsigned, so SmartScreen may ask for confirmation.
The macOS applications remain unsigned and unnotarized; use Finder's **Open** action on first launch. Do not disable SmartScreen or Gatekeeper globally.

---

## VRAM Radar 0.9.3 更新说明

本版本把「按 GPU 收藏」与方向/定位体验一并打包，方便在多服务器列表里扫读、离开后再回来。

### 主要变化

- 可收藏整台服务器或单张 GPU；导航「收藏」筛选同时包含两者，并用徽章区分「整机收藏」与「含收藏 GPU」。
- 离开某台服务器（导航点击 / 上一台下一台 / 活动服务器切换）会记住上次打开的模块与相对滚动；返回时尽量还原，且不与收起锚点、安静展开冲突。
- 位置条在吸顶头上保留，并在活动服务器卡片进入上半视口时显示；活动导航项在已知时展示 `· 模块名`。
- 服务器头新增 **收起模块**，一键关闭该卡上已展开模块，并用收起锚点避免视口乱跳。
- 保留 v0.9.2 的安静展开 / 收起锚点 / 吸顶头；正式皮肤不变（不恢复 console 左栏 / kpi-strip / overview-gpu-grid）。

### 下载与信任边界

- Windows：`VRAMRadar-Setup-0.9.3.exe`。
- macOS：`VRAMRadar-0.9.3-macos.zip`，内含 Apple Silicon 与 Intel 两套原生应用。

Windows 安装包仍未签名，SmartScreen 可能要求确认。
macOS 应用仍未签名、未公证；首次请用 Finder 右键 **打开**。不要全局关闭 SmartScreen 或 Gatekeeper。
