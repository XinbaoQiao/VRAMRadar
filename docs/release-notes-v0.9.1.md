## VRAM Radar 0.9.1

This release fixes the Code workspace module so a long directory listing can be collapsed without scrolling back to the top.

### What changed

- The Code workspace header stays sticky while the module is open, so the collapse control remains reachable after scrolling deep into a large tree.
- Server cards no longer clip sticky headers with `overflow: hidden` and `contain: paint`; rounded corners are preserved by clipping the side rail instead.
- Expanding the module scrolls back to its top, and summary clicks are handled explicitly so Chromium/WebView2 sticky hit-testing cannot leave the module stuck open.

### Validation

- Focused and full `tests.test_web_ui` contract suite on Windows.
- JavaScript syntax and whitespace checks for the touched web assets.
- Manual confirmation that a long directory list can be collapsed from the sticky header after scrolling to the bottom.

### Downloads and trust boundary

- Windows: `VRAMRadar-Setup-0.9.1.exe`.
- macOS: `VRAMRadar-0.9.1-macos.zip`, containing separate native Apple Silicon and Intel applications.

The Windows installer remains unsigned, so SmartScreen may ask for confirmation.
The macOS applications remain unsigned and unnotarized; use Finder's **Open** action on first launch. Do not disable SmartScreen or Gatekeeper globally.

---

## VRAM Radar 0.9.1 中文说明

本次版本修复「代码工作目录」在文件较多时的收起体验：滚到底部后不必再回到顶部即可收起。

### 主要变化

- 展开后的「代码工作目录」标题栏保持 sticky，滚动浏览长目录时仍可随时点击收起。
- 放开服务器卡片上会锁死 sticky 的 `overflow: hidden` / `contain: paint`，圆角改由侧栏自行裁切。
- 展开时自动回到模块顶部；标题点击改为显式切换，避免 WebView2 中 sticky 标题点不动、看起来像收不起来。

### 下载与信任边界

- Windows：`VRAMRadar-Setup-0.9.1.exe`。
- macOS：`VRAMRadar-0.9.1-macos.zip`，内含 Apple Silicon 与 Intel 两个原生应用。

Windows 安装包仍未签名，SmartScreen 可能要求确认；macOS 应用仍未签名、未公证，首次启动请在 Finder 中右击应用并选择 **打开**。请勿全局关闭 SmartScreen 或 Gatekeeper。