<p align="center">
  <a href="README.md">English</a> · <strong>简体中文</strong>
</p>

<p align="center">
  <img src="src/vram_radar/assets/app-icon.png" width="96" alt="VRAM Radar 图标">
</p>

<h1 align="center">VRAM Radar · 显存雷达</h1>

<p align="center">
  <strong>Vibe Coding 越顺手，也别失去对服务器状态的感知。</strong>
</p>

<p align="center">
  一个本地桌面界面，看清多台 SSH 服务器与 Slurm 集群里的 GPU、任务和连接状态。
</p>

<p align="center">
  <img alt="Windows x64" src="https://img.shields.io/badge/Windows-x64-2563EB?logo=windows11&logoColor=white">
  <img alt="macOS Apple Silicon and Intel" src="https://img.shields.io/badge/macOS-Apple_Silicon_%2B_Intel-111827?logo=apple&logoColor=white">
  <img alt="Direct SSH and Slurm" src="https://img.shields.io/badge/Direct_SSH_%2B_Slurm-334155">
  <img alt="本地优先" src="https://img.shields.io/badge/本地优先-0F766E">
  <img alt="MIT License" src="https://img.shields.io/badge/license-MIT-3B7C6A">
</p>

<p align="center">
  <a href="../../releases/latest"><strong>下载正式版</strong></a>
  · <a href="#3-分钟开始使用">快速开始</a>
  · <a href="#三个最常用的能力">核心功能</a>
  · <a href="docs/server-config-discovery.md">SSH 配置教程</a>
</p>

<p align="center">
  <img src="docs/assets/readme/vibe-coding-cover.webp" width="560" alt="Vibe Coding 越顺手，GPU 状态反而越难感知">
</p>

## 最新更新 · v0.9.6

当前更新为 **v0.9.6 刷新版**，包含 Light Radar 界面、统一应用图标、交互稳定性修复和 SSH 进程计时检查。

- 简化界面层次，统一模块对齐，采用半透明滚动条和尊重系统“减少动态效果”设置的轻量动效。
- 修正进程、工作目录展开时的重复滚动调整，后台刷新时保持当前服务器的位置。
- 将任务结束提醒的说明放到标题下方，并在卡片替换时释放过期的尺寸监听。
- 正常进程计时保持原样；持续返回零的异常数据经过检查后明确显示为不可用，能够验证进程身份时另列已观测运行下限。

**已经安装 v0.9.6？请从 [Latest Release](../../releases/latest) 重新下载并安装刷新后的安装包，版本号没有变化。** 详细内容见 [发布说明](docs/release-notes-v0.9.6.md)。

## 为什么做这个工具

现在很多开发流程已经变成直接用自然语言驱动代码和任务执行。Agent 可以改代码、跑命令、启动长任务，我们不必一直守着终端。

但一个很现实的问题也冒出来了：**代码流程更省心，服务器状态反而更难感知。**

- 任务到底还在不在跑？
- 跑在哪台服务器、哪张 GPU 上？
- 什么时候会有真正可用的显存？

VRAM Radar 想补回的就是这层“状态感”。它把 Direct SSH 与 Slurm 里的容量、当前账号任务和连接状态放到同一个本地桌面界面里，并把任务完成与 GPU 可用消息收进同一个本地通知中心。

## 三个最常用的能力

<table>
  <tr>
    <td width="33%"><img src="docs/assets/readme/multi-server-overview.webp" alt="多服务器 GPU 总览"></td>
    <td width="33%"><img src="docs/assets/readme/task-status.webp" alt="运行与排队任务状态"></td>
    <td width="33%"><img src="docs/assets/readme/local-alert.webp" alt="GPU 资源可用时本地提醒"></td>
  </tr>
  <tr>
    <td><strong>一个界面看多台服务器</strong><br>Direct SSH 工作站和 Slurm 集群不再分散在不同终端。</td>
    <td><strong>任务状态与 GPU 放在一起</strong><br>查看当前账号的运行、排队、节点和资源状态。</td>
    <td><strong>提醒集中，减少反复查看</strong><br>任务完成与 GPU 可用消息统一进入本地通知中心；保留未读记录，其他人的任务只在逐项选择后关注。</td>
  </tr>
</table>

服务器总览优先展示可用显存和连接状态；节点、任务、进程、资源匹配和代码目录在需要时再展开。图中的服务器数据均为合成示例，不包含真实地址、账号、密钥或本地 Profile。

### 日常使用

| 功能 | 可以做什么 |
|---|---|
| 服务器导航 | 搜索、筛选与收藏服务器，在需要时暂停监控。 |
| GPU 可用性 | 在后端支持时查看每张卡的显存、使用率和温度；收藏的服务器或 GPU 整卡空闲、或达到设定空闲显存时接收提醒。 |
| 任务与进程 | 查看 Slurm 运行和排队任务，以及 Direct SSH GPU 进程的归属、GPU 分配和可用计时信息。 |
| 任务结束提醒 | 开启当前账号任务提醒，或逐项关注其他用户的任务；在本地通知中心查看消息和未读记录。 |
| CPU 与内存 | 在 GPU 视图旁查看 CPU 使用率、平均负载、核心数量与主机内存，并阅读内置指标说明。 |
| 工作目录 | 按需浏览账号目录、固定默认目录，减少反复打开终端的操作。 |

### 如何理解计时与连接状态

Direct SSH 进程的运行时长依赖远端系统提供的元数据。容器隔离、权限限制或时钟不一致，都可能导致这些信息缺失或不可靠。

- **正常计时**：正数时长沿用原有显示；刚启动的进程确实可能短暂显示零。
- **计时待确认**：零值无法对应到可验证的进程身份，此时不生成运行时间估计。
- **运行时长不可用**：确认是同一进程，连续至少三次采样、跨越 30 秒仍返回零。另列的 **“已观测运行至少……”** 只表示软件实际观测到的时间下限，不代表任务从启动至今的完整时长。

进程身份变化、进程消失、连接失败、暂停监控或采样间隔过长后，观测会重新计数。缓存或离线数据不能证明任务仍在运行；任务结束提醒依赖成功采样，无法保证捕获两次采样之间启动并结束的短任务。

<p align="center">
  <img src="docs/assets/readme/product-boundary.webp" width="560" alt="VRAM Radar 不替代调度器，只把状态感补回来">
</p>

VRAM Radar 不提交任务、不预约 GPU，也不替代 `nvidia-smi`、`nvtop` 或 Slurm。它解决的是更靠前的判断：**哪里有容量、我的任务在哪里、下一步应该打开哪台服务器。**

## 3 分钟开始使用

1. 从 [Latest Release](../../releases/latest) 下载与你的平台对应的正式包。
2. 启动 VRAM Radar，检查它在本机发现的 SSH 别名。
3. 确认每台服务器使用 Direct SSH 还是 Slurm，保存 Profile 后进入资源总览。
4. 在设置中选择任务结束提醒和收藏 GPU 提醒。最小化后可从通知区域或菜单栏访问应用；关闭窗口时隐藏还是退出，也可以在设置中选择。

自动发现覆盖常见 OpenSSH、VS Code、Cursor、Windsurf、Colima、OrbStack、XDG 与 Harness 目录。发现过程只读取本地配置；一个条目只有在保存后的连接和采集都成功后，才会显示为**监控就绪**并计入实时容量。

## 下载与首次启动边界

当前公开稳定版为 **v0.9.6**。

| 平台 | 下载文件 | 当前边界 |
|---|---|---|
| Windows x64 | `VRAMRadar-Setup-0.9.6.exe` | 按当前用户安装；目前未签名，SmartScreen 可能要求确认。 |
| macOS | `VRAMRadar-0.9.6-macos.zip` | 内含 Apple Silicon 与 Intel 两个原生应用；目前未签名、未公证，首次从 Finder 右击 **打开**。 |

Latest Release 只保留用户实际需要下载的两个文件。Windows 推荐下载安装包，原位
更新会保留开始菜单或桌面快捷方式；公开 Release 不再提供 Windows 便携 ZIP。
macOS 版本未使用 Apple Developer ID 签名、未经公证，首次启动请在 Finder 中右击
**打开**，不要关闭 Gatekeeper。

Apple Silicon 当前验证边界为 macOS 14 或更新版本，Intel x86_64 为 macOS 15 或更新版本。请勿全局关闭 SmartScreen 或 Gatekeeper。详细边界见 [Windows 安装说明](docs/windows-install-and-update.md)、[Windows 签名状态](docs/windows-code-signing.md)、[macOS 兼容性说明](docs/macos-desktop.md)和 [v0.9.6 发布说明](docs/release-notes-v0.9.6.md)。

## 本地优先，不接管你的基础设施

- Profile、缓存、日志、运行锁和服务器目录都留在当前电脑，不会进入公开安装包。
- 密码只保存在 Windows Credential Manager 或 macOS Keychain，不写入 Profile、日志、命令行参数或子进程环境。
- 首次出现的 SSH Host Key 由 OpenSSH 自动保存；已经变化的 Host Key 会继续阻止连接。
- 监控保持只读；任务提交、GPU 预约和站点策略仍由 Slurm 或现有平台负责。

需要完整实现边界时，可查看[隐私说明](PRIVACY.md)与[服务器可靠性审计](docs/server-reliability-audit-2026-08-29.md)。

## 文档

- [SSH 配置自动发现与排查](docs/server-config-discovery.md)
- [Windows 安装、通知区域与更新](docs/windows-install-and-update.md)
- [macOS 构建与兼容性](docs/macos-desktop.md)
- [产品与桌面架构](docs/productization-design.md)
- [界面设计系统](docs/design-system.md)
- [隐私说明](PRIVACY.md)

## 开发

<details>
<summary><strong>本地构建与测试</strong></summary>

Windows：

```powershell
uv sync --extra build --frozen
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check src\vram_radar\web\app.js
node --check src\vram_radar\web\localization.js
.\.venv\Scripts\python.exe -m compileall -q src tests tools
.\.venv\Scripts\python.exe tools\benchmark_webview_ui.py --timeout-seconds 120
.\Build-VramRadar.ps1 -SkipSync
.\.venv\Scripts\python.exe tools\validate_packaged_askpass.py
.\.venv\Scripts\python.exe tools\validate_packaged_tray.py
```

macOS：

```bash
uv sync --extra build --frozen
./.venv/bin/python -m unittest discover -s tests -v
node --check src/vram_radar/web/app.js
node --check src/vram_radar/web/localization.js
./.venv/bin/python -m compileall -q src tests tools
./.venv/bin/python tools/benchmark_webview_ui.py --timeout-seconds 120
bash Build-VramRadar-macOS.sh --skip-sync
./.venv/bin/python tools/validate_macos_bundle.py
```

界面基准使用合成数据，并会短暂显示原生窗口以验证滚动与动画帧。发布验证必须使用空的临时 Profile 和 `--no-auto-import`，避免接触维护者自己的服务器配置。发布前，还应使用临时 `--home`、`--profile`、`--no-auto-import` 和 `--show-paths` 启动一次打包程序；macOS 打包验证会检查其原生启动路径。

</details>

## 反馈与许可证

如果自动发现遗漏了某种 SSH 配置，或应用无法启动，请在 [Issues](../../issues) 中提供系统版本、应用版本和应用生成的**脱敏诊断**。不要上传密码、私钥、真实服务器地址或未经检查的完整日志。

VRAM Radar 采用 [MIT License](LICENSE) 开源。
