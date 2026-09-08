## VRAM Radar 0.9.6

This release introduces the Light Radar interface: quieter surfaces, clearer emphasis, and more consistent spacing in both light and dark themes.

### What changed

- Refreshed application identity: one deep-teal and mint radar mark across the dashboard, welcome screens, Windows executable/taskbar/tray, and macOS Dock.
- Simplified cards, controls, and capacity meters with restrained teal accents.
- Aligned server headers, GPU tables, CPU summaries, and expanded modules; corrected narrow-window and sticky-header overlaps.
- Increased space between module accent lines and text, including GPU processes and code working directories.
- Redesigned scrollbars with translucent gray-teal thumbs and clearer hover and drag feedback.
- Added subtle control transitions and hover feedback while respecting reduced-motion preferences.
- Expanded the bilingual CPU reference to explain utilization, load averages, logical processors, host memory, and data freshness in a full-width panel.

### Release validation

Publication requires source tests, JavaScript and Python syntax checks, native layout checks, and packaged application checks. Windows x64 and both native macOS architectures are built from the same source commit; the final combined macOS archive is validated on both architectures before publication.

### Downloads and trust boundary

The v0.9.6 downloads were refreshed with the new application icon. The version number remains unchanged; if you installed the earlier v0.9.6 package, download and reinstall this refreshed package to receive the icon update.

- Windows: `VRAMRadar-Setup-0.9.6.exe`.
- macOS: `VRAMRadar-0.9.6-macos.zip`, containing separate native Apple Silicon and Intel applications.

The Windows installer remains unsigned, so SmartScreen may ask for confirmation. The macOS applications remain unsigned and unnotarized; use Finder's **Open** action on first launch. Do not disable SmartScreen or Gatekeeper globally.

---

## VRAM Radar 0.9.6 更新说明

本版本采用 Light Radar 界面，在浅色与深色主题下统一优化留白、视觉层次与信息强调。

### 主要变化

- 统一应用标识：界面、欢迎页、Windows 程序/任务栏/托盘及 macOS Dock 均采用深青与薄荷绿的新雷达图标。
- 简化卡片、控件和显存容量条，以克制的青绿色突出重点信息。
- 统一服务器标题、GPU 表格、CPU 概览及展开模块的内容宽度，修正窄窗口和吸顶状态下的组件重叠。
- 增加模块强调线与文字之间的间距，改善 GPU 进程、代码工作目录等区域的阅读体验。
- 重新设计滚动条：默认采用半透明灰青色，悬停和拖动时逐渐增强辨识度。
- 增加轻量的控件过渡与悬停反馈，并尊重系统的减少动态效果设置。
- 扩充中英文 CPU 指标说明，以完整宽度的说明面板解释使用率、平均负载、逻辑处理器、主机内存和数据时效。

### 发布验证

发布流程要求通过源码测试、JavaScript 与 Python 语法检查、原生布局检查及打包应用验证。Windows x64、Apple Silicon 和 Intel 应用均从同一源码提交构建；最终 macOS 合集还须在两种架构上分别验证后才会公开发布。

### 下载与信任边界

v0.9.6 下载包已更新为新版应用图标，版本号保持不变。已安装较早 v0.9.6 下载包的用户，请重新下载并安装本次更新包，以获得图标更新。

- Windows：`VRAMRadar-Setup-0.9.6.exe`。
- macOS：`VRAMRadar-0.9.6-macos.zip`，内含 Apple Silicon 与 Intel 两套原生应用。

Windows 安装包仍未签名，SmartScreen 可能要求确认。macOS 应用仍未签名、未公证；首次请用 Finder 右键 **打开**。不要全局关闭 SmartScreen 或 Gatekeeper。
