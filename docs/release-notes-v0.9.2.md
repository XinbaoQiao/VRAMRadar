## VRAM Radar 0.9.2

This release extends sticky headers beyond the Code workspace module and tightens scroll interaction on long server cards.

### What changed

- Every server card header stays sticky under the title bar and collapses to a compact strip while stuck, so account/meta details do not crowd the viewport.
- Open secondary modules (GPU processes, tasks, Code workspace, and the rest) stick one at a time under the server header; only the module currently crossing the stick line stays sticky.
- Compact-header height is remeasured in the same frame as the stuck state, and a seam cover prevents content from bleeding through the gap under sticky module titles.
- Simple Code workspace folder open/close patches the local tree instead of rewriting the whole module, which feels snappier on large directories.
- Expanding a module only auto-scrolls when it is obscured or far down the page, instead of jumping on every open.

Visual skin and layout remain the same as v0.9.1; this release is interaction and sticky-behavior only.

### Validation

- Full `tests.test_web_ui` contract suite on Windows (68 tests).
- Manual sticky-preview confirmation for compact server headers, single-module sticky, and seam coverage.

### Downloads and trust boundary

- Windows: `VRAMRadar-Setup-0.9.2.exe`.
- macOS: `VRAMRadar-0.9.2-macos.zip`, containing separate native Apple Silicon and Intel applications.

The Windows installer remains unsigned, so SmartScreen may ask for confirmation.
The macOS applications remain unsigned and unnotarized; use Finder's **Open** action on first launch. Do not disable SmartScreen or Gatekeeper globally.

---

## VRAM Radar 0.9.2 中文说明

本次版本把吸顶交互从「代码工作目录」扩展到整张服务器卡片，并收紧长列表上的滚动体验。

### 主要变化

- 每台服务器标题栏在顶栏下吸顶；吸顶时收成紧凑条，账号/主目录等次要信息暂时隐藏。
- 展开的二级模块（GPU 进程、任务、代码工作目录等）同一时间只有一个标题吸顶，贴在服务器标题下方。
- 紧凑标题高度与模块吸顶偏移同一帧同步，并用遮挡层盖住标题缝隙，避免内容从缝里透上来。
- 代码工作目录里简单的文件夹开合改为局部刷新，大目录时更跟手。
- 展开模块只在被挡住或位置偏下时才自动滚入视野，避免每次展开都硬跳。

界面皮肤与 v0.9.1 相同，本版只改交互与吸顶行为。

### 下载与信任边界

- Windows：`VRAMRadar-Setup-0.9.2.exe`。
- macOS：`VRAMRadar-0.9.2-macos.zip`，内含 Apple Silicon 与 Intel 两个原生应用。

Windows 安装包仍未签名，SmartScreen 可能要求确认。
macOS 应用仍未签名、未公证；首次请从 Finder 右击 **打开**。请勿全局关闭 SmartScreen 或 Gatekeeper。
