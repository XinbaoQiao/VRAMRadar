## VRAM Radar 0.9.5

This release packages post-0.9.4 navigation, host CPU, and status-color polish on the formal skin (layout A/B/C landing, theme-warm/cool/ink, and abandoned console/compact experiments are not included).

### What changed

- Sidebar navigator jump survives live card replace races and centers the target server in the viewport.
- Expanding modules and task groups scrolls to the first real content item (GPU-first sticky expand).
- UI zoom via Ctrl+/- (50–150%), pin servers to the top of the fleet, pause sort-back for individual servers, and command summary collapsed by default.
- Host CPU row shows usage %, host memory, and load in a compact 1-minute-style readout with one-line help bullets.
- Battery/GPU bar fills use same-hue status micro-gradients (no positional green→red spectrum); warning/critical thresholds move earlier to 70% / 85%.
- Running tasks use sky tone vs completed green; L40 GPU row border clipping fixed.
- Live refresh/reconcile no longer thrash scroll (suppress programmatic toggle, preserve scrollY).
- Light CSS contrast polish for readable secondary text.
- Profile API: `pinned_server_ids` + `set_pinned_server` with localStorage fallback.

### Validation

- `tests.test_web_ui` (zoom/pin/pause/command collapse, navigator jump race, sidebar center) plus connectors/models/shell coverage for CPU/mem and pins on Windows.
- `node --check` on `app.js` / `localization.js` (CI gate).

### Downloads and trust boundary

- Windows: `VRAMRadar-Setup-0.9.5.exe`.
- macOS: `VRAMRadar-0.9.5-macos.zip`, containing separate native Apple Silicon and Intel applications.

The Windows installer remains unsigned, so SmartScreen may ask for confirmation.
The macOS applications remain unsigned and unnotarized; use Finder's **Open** action on first launch. Do not disable SmartScreen or Gatekeeper globally.

---

## VRAM Radar 0.9.5 更新说明

本版本打包 0.9.4 之后的导航、主机 CPU 与状态配色打磨（正式皮肤；不包含 layout A/B/C 落地页、theme-warm/cool/ink，以及已放弃的 console/紧凑版式实验）。

### 主要变化

- 侧栏跳转在卡片被实时替换时仍能命中，并把目标服务器滚到视口中央。
- 展开模块 / 任务组时滚到首个真实内容项（GPU 优先吸顶展开）。
- Ctrl+/- 缩放（50–150%）、置顶服务器、暂停单机回排、命令摘要默认折叠。
- 主机 CPU 行展示使用率%、主机内存与负载（偏 1 分钟风格）及一行帮助说明。
- 电量/GPU 条同色相状态微渐变（非位置绿→红光谱）；预警/危急阈值提前到 70% / 85%。
- 运行中任务用天蓝、已完成用绿色；修复 L40 GPU 行边框裁切。
- 刷新/协调不再把滚动条打乱（抑制程序性 toggle、保留 scrollY）。
- 浅色主题对比度微调，次要文字更易读。
- Profile API：`pinned_server_ids` 与 `set_pinned_server`（含 localStorage 回退）。

### 下载与信任边界

- Windows：`VRAMRadar-Setup-0.9.5.exe`。
- macOS：`VRAMRadar-0.9.5-macos.zip`，内含 Apple Silicon 与 Intel 两套原生应用。

Windows 安装包仍未签名，SmartScreen 可能要求确认。macOS 应用仍未签名、未公证；首次请用 Finder 右键 **打开**。不要全局关闭 SmartScreen 或 Gatekeeper。
