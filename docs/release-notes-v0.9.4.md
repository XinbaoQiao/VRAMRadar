## VRAM Radar 0.9.4

This release packages post-0.9.3 favorites UX polish and scroll performance work on the formal skin (the later compact layout experiment was reverted and is not included).

### What changed

- Clearer favorite-available alerts: titles/bodies distinguish whole-server free GPUs vs specifically favorited GPUs (`收藏的服务器有空闲 GPU` / `收藏的 GPU 已空闲`).
- Favorites findability: navigator badges use short `整台` / `仅 GPU` labels; empty favorites state is a single `还没有收藏` message; optional favorites summary strip and per-server “只看收藏 GPU”.
- Portable builds explain why in-app update opens GitHub (`当前不是正式安装版…`) and confirm before opening the release page; Setup installs keep one-click update.
- Favorite-available notifications can jump to the matching server/GPU when clicked (persisted `server_id` / indices when available).
- Sticky chrome scroll cost reduced (rAF coalesce, viewport-scoped card measure, incremental sticky-active, conditional location strip updates).

### Validation

- `tests.test_web_ui` plus favorites/update-related `tests.test_service` / `tests.test_shell` on Windows.
- Local packaged `dist/VRAMRadar/VRAMRadar.exe` smoke after `Build-VramRadar.ps1 -SkipSync`.

### Downloads and trust boundary

- Windows: `VRAMRadar-Setup-0.9.4.exe`.
- macOS: `VRAMRadar-0.9.4-macos.zip`, containing separate native Apple Silicon and Intel applications.

The Windows installer remains unsigned, so SmartScreen may ask for confirmation.
The macOS applications remain unsigned and unnotarized; use Finder's **Open** action on first launch. Do not disable SmartScreen or Gatekeeper globally.

---

## VRAM Radar 0.9.4 更新说明

本版本打包 0.9.3 之后的收藏体验打磨与滚动性能优化（后来的紧凑版式实验已回撤，不包含在本版）。

### 主要变化

- 收藏可用提醒文案更清楚：区分整台有空闲 GPU 与指定收藏卡空闲（`收藏的服务器有空闲 GPU` / `收藏的 GPU 已空闲`）。
- 收藏更好找：导航徽章改为简短的「整台 / 仅 GPU」；无收藏时只提示「还没有收藏」；可选收藏摘要条与服务器内「只看收藏 GPU」。
- 便携包更新会说明无法应用内更新并确认后再打开 GitHub；正式 Setup 安装版仍可一键更新。
- 收藏空闲通知支持点击跳到对应服务器/GPU（有持久化字段时）。
- 吸顶滚动更省：每帧合并计算、视口内测量、增量 sticky-active、位置条仅在变化时刷新。

### 下载与信任边界

- Windows：`VRAMRadar-Setup-0.9.4.exe`。
- macOS：`VRAMRadar-0.9.4-macos.zip`，内含 Apple Silicon 与 Intel 两套原生应用。

Windows 安装包仍未签名，SmartScreen 可能要求确认。
macOS 应用仍未签名、未公证；首次请用 Finder 右键 **打开**。不要全局关闭 SmartScreen 或 Gatekeeper。