## VRAM Radar 0.9.0

This release hardens Windows startup and window activation after a field report
where repeated launches could terminate inside `CoreMessaging.dll` before the
dashboard became usable.

### What changed

- A second launch now waits for both the native window and the embedded DOM to
  be ready before restoring the existing window.
- Shortcut bursts during startup are coalesced into one restore instead of
  issuing overlapping native window operations.
- Authenticated frontend readiness probes, restore, hide, and destroy now share
  one lifecycle lock, preventing probes from crossing WebView disposal.
- Exit requests remain interruptible and wait for the same bounded readiness
  boundary instead of destroying a WebView while it is still initializing.
- GitHub update transport starts only after local dashboard initialization, so
  first paint and the initial background server snapshot do not compete with an
  independent DNS/TLS request.
- The packaged Windows lifecycle gate now launches eight concurrent secondary
  instances immediately after the activation endpoint appears and requires one
  surviving, painted, bridge-ready application through minimize, tray restore,
  and clean shutdown.

### Validation

- Complete unit, contract, and behavior suite on Windows.
- JavaScript syntax, Python compilation, and native synthetic WebView layout
  benchmark with an empty Profile and zero remote connections.
- Rebuilt Windows package, scoped askpass validation, packaged activation-storm
  lifecycle test, installer, and in-place update validation.
- Native Apple Silicon and Intel builds, Cocoa launch checks, architecture
  validation, packaged update transport, and final combined-package launch are
  required by the release workflow before publication.

### Downloads and trust boundary

- Windows: `VRAMRadar-Setup-0.9.0.exe`.
- macOS: `VRAMRadar-0.9.0-macos.zip`, containing separate native Apple Silicon
  and Intel applications.

The Windows installer remains unsigned, so SmartScreen may ask for confirmation.
The macOS applications remain unsigned and unnotarized; use Finder's **Open**
action on first launch. Do not disable SmartScreen or Gatekeeper globally.

---

## VRAM Radar 0.9.0 中文说明

本次版本针对一项真实 Windows 现场问题加固启动与窗口激活流程：连续点击程序时，
旧流程可能在仪表盘就绪前于 `CoreMessaging.dll` 中退出。

### 主要变化

- 第二次启动必须同时等待原生窗口与嵌入页面 DOM 就绪，才恢复已有窗口。
- 启动阶段的连续点击会合并为一次恢复，避免重叠的原生窗口操作。
- 前端就绪探测、显示、隐藏和销毁现在共享同一生命周期锁，探测不会跨过
  WebView 销毁边界。
- 退出请求使用相同的有界就绪条件，并可被关闭流程及时中断。
- GitHub 更新检查延后到本地仪表盘初始化之后，避免与首次绘制及第一轮后台
  服务器快照同时竞争 DNS、TLS 和桥接线程。
- Windows 打包验证会在激活端点刚出现时并发启动八个副本，并要求最终只有一个
  可绘制、桥接就绪的进程完成最小化、托盘恢复和干净退出。

### 下载与信任边界

- Windows：`VRAMRadar-Setup-0.9.0.exe`。
- macOS：`VRAMRadar-0.9.0-macos.zip`，内含 Apple Silicon 与 Intel 两个原生应用。

Windows 安装包仍未签名，SmartScreen 可能要求确认；macOS 应用仍未签名、未公证，
首次启动请在 Finder 中右击应用并选择 **打开**。请勿全局关闭 SmartScreen 或
Gatekeeper。
