<p align="center">
  <a href="README.md">English</a> · <strong>简体中文</strong>
</p>

<p align="center">
  <img src="src/vram_radar/assets/app-icon.png" width="104" alt="VRAM Radar 图标">
</p>

<h1 align="center">VRAM Radar · 显存雷达</h1>

<p align="center">
  <strong>把分散在 SSH、Slurm 和多台 GPU 服务器里的资源状态，收进一个本地桌面工作台。</strong>
</p>

<p align="center">
  Windows 与 macOS · Direct SSH 与 Slurm · 本地优先 · 不上传服务器配置
</p>

<p align="center">
  <img alt="Windows x64" src="https://img.shields.io/badge/Windows-x64-2563EB?logo=windows11&logoColor=white">
  <img alt="macOS Apple Silicon and Intel" src="https://img.shields.io/badge/macOS-Apple_Silicon_%2B_Intel-111827?logo=apple&logoColor=white">
  <img alt="Local first" src="https://img.shields.io/badge/data-local--first-0F766E">
  <img alt="MIT License" src="https://img.shields.io/badge/license-MIT-3B7C6A">
</p>

<p align="center">
  <a href="../../releases/latest"><strong>下载正式版</strong></a>
  · <a href="#3-分钟开始使用">快速开始</a>
  · <a href="#它能做什么">功能</a>
  · <a href="docs/server-config-discovery.md">SSH 配置教程</a>
</p>

![VRAM Radar 产品总览](docs/assets/vram-radar-overview.png)

## 不再为“哪张卡有空”反复切换终端

还在逐台登录服务器，重复运行 `nvidia-smi`、`nvtop`、`squeue`，再手动比较哪张 GPU 可以使用？

VRAM Radar 是面向个人工作站、实验室服务器和 Slurm 集群的本地桌面监控工具。它从你电脑上已经存在的 SSH 配置开始，统一呈现可用显存、卡型、分区、节点、当前账号任务和连接状态；需要细节时再逐层展开，不需要时保持简洁。

它不会提交作业、抢占 GPU 或替代集群调度器。它解决的是更靠前、也更高频的问题：**快速看清资源在哪里、我的任务在哪里、下一步应该连接哪台服务器。**

| 常见使用痛点 | VRAM Radar 的处理方式 |
|---|---|
| 多台服务器来回 SSH，信息散落在不同终端 | 一个桌面界面汇总 Direct SSH 与 Slurm 资源 |
| SSH 配置分散在系统、编辑器和工具目录 | 自动发现常见 OpenSSH、VS Code、Cursor、Windsurf、Colima、OrbStack 配置 |
| 卡型、分区、显存和任务需要人工对照 | 在同一层级展示关键事实，并按条件推荐可用资源 |
| 服务器或 GPU 数量很大，页面容易卡顿 | 主列表、右侧导航和集群节点均采用有界分页与按需加载 |
| 只想知道“我的任务在哪”，却被全部作业淹没 | 右侧“任务”分类只保留当前登录账号的活动任务或进程 |

## Preview

真实界面使用合成服务器数据生成；截图不包含私人服务器地址、密钥或本地 Profile。

### 一眼比较多台服务器

顶部先给出总可用显存和在线资源，服务器标题栏把账号、主目录、状态以及收藏 / 复制 SSH / 打开终端 / 暂停放在同一信息带中。右侧 Mini Navigator 用于快速查看和跳转。

### 需要时，再展开节点、任务与目录

![VRAM Radar 服务器与节点细节](docs/assets/vram-radar-server-detail.png)

A100/Slurm 节点表使用固定列轨道，长节点名、分区名或状态不会让不同服务器的列错位。任务详情与代码工作目录默认收起，展开后仍可一键恢复。

## 它能做什么

### 多服务器 GPU 总览

- 同时查看 Direct SSH `nvidia-smi` 与 Slurm 调度快照。
- 汇总在线服务器、GPU 数量、总显存和可用显存。
- 区分在线、离线、缓存、认证失败、Host Key 变化和配置错误。
- Direct SSH 显示单卡显存、利用率、温度和 GPU 进程；Slurm 显示节点、分区、卡型、调度显存和作业状态。

### 找一台真正合适的 GPU

- 按 GPU 数量、单卡最低空闲显存、卡型和分区筛选。
- 多卡请求可要求位于同一节点。
- 推荐结果在用户主动查询后才出现，并突出服务器、节点 / 设备、卡型、分区和空闲数量。
- 常用条件可以保存为视图；满足条件时可触发一次本地通知。

### 面向多服务器与大集群

- 右侧 Mini Navigator 默认收起，悬停或键盘聚焦时展开。
- 收起时只为目录窄轨道和少量间距预留空间，不再让隐藏面板挤压主界面；紧凑窗口会把它移到主内容上方并参与正常布局。
- 支持搜索、收藏、最近访问、可用资源、我的任务和异常状态筛选。
- 导航栏可拖到左侧或右侧，选择会跨重启和版本更新保留。
- 主界面每页最多渲染 50 台服务器，导航窗口最多渲染 80 项。
- 服务器设置支持搜索，每页只渲染 20 张编辑表单；关闭设置后会释放这些表单节点。
- 超过 64 个节点或 256 张 GPU 的 Slurm 集群先显示卡型 / 分区汇总；节点明细每页按需读取 75 行。

### 从已有 SSH 配置开始

- 首次启动优先自动发现，不要求用户从空表单开始填写。
- Windows 与 macOS 共用同一套有界发现、合并和去重逻辑。
- 自动发现只读取本地配置，不会因为“发现了 Host”就主动连接服务器。
- 用户主动移除已导入的 SSH 别名后，系统会持久记录并在后续启动与同步时跳过它；SSH Config 中新增的其他 `Host` 仍会被发现，手动重新添加同名别名则会清除忽略记录。
- OpenSSH 自动导入只保留服务器 ID、类型、别名和源配置文件引用，不复制用户名、`IdentityFile`、私钥或凭据；任何本地 Profile、服务器地址和用户数据都不会进入发布包。
- 没有发现配置时，内置教程会按系统给出查找路径、命令和复制按钮。
- 每台已保存服务器都提供折叠式 SSH Key 引导：可复用现有密钥或生成独立 Ed25519 密钥；远端只以非替换方式追加公钥，不覆盖 `authorized_keys`，随后强制验证所选私钥。若追加后的验证或本地 Profile 保存失败，应用会保留已追加公钥及其匹配的本地生成密钥，返回“需要恢复”，并指导用户重试或手动精确移除该公钥。

### 账号、任务与代码目录

- 每台服务器同时显示当前登录账号和主目录位置。
- 右侧导航的“任务”只展示当前账号正在运行或排队的工作，不混入其他用户任务。
- 任务详情仍保留“我的 / 其他用户 / 近期结果”的分级视图，方便进一步检查。
- 文件夹树默认定位推断出的代码工作目录；可把任意已浏览目录固定为新的默认路径并持久化。
- 目录浏览有深度与数量上限，不读取文件内容，也不跟随符号链接。

### 桌面级便利功能

- 在设置中切换简体中文 / English；完整界面会立即切换，本地偏好在重启和覆盖更新后仍会保留。
- 为收藏服务器持久设置资源提醒：整张 GPU 空闲，或任意单卡达到可选的最低空闲显存阈值时发送系统通知。窗口收起后仍会继续判断；同一次可用状态只提醒一次，不会每轮刷新重复弹出。
- 收藏服务器、复制 SSH 配置、打开系统终端、单独暂停服务器监控。“复制 SSH”生成可直接粘贴进 OpenSSH config 的 `Host` 配置块，包含静态确认的 `HostName`、`User`、`Port`、可选 `IdentityFile` 和非交互安全选项；遇到条件式或动态配置时，会回退复制安全的可执行别名命令并明确提示，而不会猜测字段。
- Windows 最小化或关闭后可留在通知区域；右键菜单提供状态、显示、刷新、设置、暂停和退出。
- 自动检查 GitHub 最新稳定版，只提醒，不静默下载安装。
- Windows 安装版保持固定安装目录和快捷方式目标；覆盖安装新版本后原快捷方式继续可用。
- 复制脱敏诊断信息并直接打开本地日志目录，便于反馈连接问题。

## 它与 `nvidia-smi` / `nvtop` 的关系

VRAM Radar 不是对终端工具的重新包装，也不要求你放弃它们。`nvidia-smi` 与 `nvtop` 仍然非常适合深入观察当前主机；VRAM Radar 更适合在连接前完成跨服务器判断。

| 场景 | `nvidia-smi` / `nvtop` | VRAM Radar |
|---|---|---|
| 当前主机的即时 GPU 诊断 | 很适合 | 提供概览与关键指标 |
| 多台 SSH 服务器横向比较 | 通常需要逐台执行 | 一个界面统一比较 |
| Slurm 节点、分区和当前账号任务 | 需要额外命令组合 | 与 GPU 容量同屏呈现 |
| 首次配置 | 由用户自行组织 SSH 命令 | 自动发现并提供图形化复核 |
| 千卡规模浏览 | 依赖用户自行脚本化 | 汇总优先、分页、窗口化和按需读取 |
| 作业提交与 GPU 预约 | 不负责 | 不负责；继续使用 Slurm 或现有平台 |

## 3 分钟开始使用

1. 从 [Latest Release](../../releases/latest) 下载与你的平台对应的正式包。
2. 启动 VRAM Radar。空 Profile 会进入三步引导，而不是展示一张空仪表盘。
3. 选择“自动发现”，检查找到的 SSH 别名，并确认每台服务器使用 Direct SSH 还是 Slurm。
4. 如需密码登录，在本地设置中输入服务器账号密码；随后也可以通过每台服务器的 SSH Key 引导配置并验证免密登录。
5. 保存后进入资源总览。服务器的高级信息、任务和目录会在你展开时再读取或显示。

服务器状态按实际证据分级：**已发现**只表示本地条目解析成功，**已保存**表示 Profile 事务已经提交，**正在验证**表示系统正在运行保存后的 SSH 链路，只有认证、后端命令和返回解析全部成功后才会显示**监控就绪**。尚未通过最后一步的服务器绝不会计入实时容量；旧快照只会作为明确标记的历史信息保留。

### Windows

下载 `VRAMRadar-Setup-0.7.0.exe`。这是 Windows 推荐下载。默认按当前用户安装，不需要管理员权限；安装器会创建稳定的开始菜单 / 桌面快捷方式，后续运行新版安装器会原位升级并保留用户 Profile。可以安装到 `D:\Apps\VRAM Radar` 等当前用户可写目录；只有选择 `Program Files` 等受保护目录时才需要管理员权限。公开 Release 不再提供 Windows 便携 ZIP。

当前 `v0.7.0` Windows 包没有 Authenticode 发布者证书。VRAM Radar 正在申请 [SignPath Foundation](https://signpath.org/) 开源项目免费签名；只有公开安装包通过项目维护的[签名验证门](docs/windows-code-signing.md)后，我们才会把后续版本标为已签名。在此之前，如果 SmartScreen 弹出提示，请先确认文件来自本仓库的 Latest Release，再点击 **更多信息 → 仍要运行**。单位管理的电脑可能不允许绕过未签名软件，此时应联系管理员，不要关闭系统安全保护。

### macOS

下载 `VRAMRadar-0.7.0-macos.zip`：

- `VRAM Radar (Apple Silicon).app`：M1 / M2 / M3 / M4，当前验证边界为 macOS 14 或更新版本。
- `VRAM Radar (Intel).app`：Intel x86_64，当前验证边界为 macOS 15 或更新版本。

当前项目没有配置 Apple 分发凭据，因此公开 Mac 包没有 Developer ID 签名和公证。解压后，在 Finder 中右击对应 `.app`，选择 **打开**，再确认一次 **打开**；不要全局关闭 Gatekeeper。

> 当前公开稳定版为 `v0.7.0`。更新检查不再等待服务器刷新；网络失败会显示重试入口，应用持续运行时也会定期复查。Windows 正式安装版支持经用户确认、校验 SHA-256、失败自动回滚并重启的一键更新。GitHub Release 只保留 Windows 安装器与合并后的 macOS 下载包。

<details>
<summary><strong>发布包边界</strong></summary>

`VRAMRadar-Setup-0.7.0.exe` 是 Windows 推荐下载。原位升级会保留固定安装路径，因此开始菜单和桌面快捷方式仍然有效。公开 Release 不再提供 Windows 便携 ZIP，只保留用户实际需要的两个文件：Windows 安装器和合并后的 macOS 包。

Windows 一键更新只接受当前仓库正式 Release 中名称和地址完全匹配的安装包，执行前会校验 GitHub 提供的 SHA-256，并在替换前保留旧安装用于失败回滚。同一可见版号下的修复包会用精确源码提交区分，因此已安装的 `v0.7.0` 也能收到较新的 `v0.7.0` 修复提示。手动覆盖安装时，安装器会等待已认证的旧实例真正退出，再替换文件并重建开始菜单和桌面快捷方式；如果旧进程没有退出，安装会在改动应用文件前停止，避免只更新一半。`v0.6.1` 尚未包含独立更新器，因此从该版本升级到 `v0.7.0` 需要手动安装一次；此后的版本才能使用应用内一键替换。macOS 会下载并校验压缩包，但在具备 Developer ID 签名和公证之前，仍需用户手动替换 `.app`。

当前版本未使用 Apple Developer ID 签名，也未经过 Apple 公证。首次启动时，请在 Finder 中右击应用并选择 **打开**，并仅在信任本仓库时确认系统提示。

</details>

## 自动配置覆盖范围

<details>
<summary><strong>展开查看 Windows / macOS 自动发现来源</strong></summary>

VRAM Radar 会在本地按确定性优先级扫描并合并以下来源：

- v2 `servers.toml`，包括便携 Harness 的 `harness/config/servers.toml`。
- 用户、XDG、系统、Homebrew 和已安装 OpenSSH 客户端附近的 `config`。
- 有界 OpenSSH `Include` 片段，包含循环保护和 Windows 反斜杠路径处理。
- VS Code、VS Code Insiders、Cursor、VSCodium 与 Windsurf 的 `remote.SSH.configFile`。
- Colima、OrbStack 及常见独立工具配置。
- `~`、`${userHome}`、`${env:NAME}` 与正常系统环境变量形式的编辑器路径。

导入仅接受具体 `Host` 别名，跳过通配符规则和 `known_hosts`。每个别名会保留它的原始 OpenSSH 配置文件，并在连接时通过 `ssh -F` 使用，因此配置不必重复写入默认 `~/.ssh/config`。`HostName`、`User`、`Port`、`IdentityFile`、`ProxyJump` 和 `ProxyCommand` 继续由该本地文件管理，并由 OpenSSH 在连接时解析，不会被重复复制进应用 Profile。

移除已导入服务器时，应用会按不区分大小写的 SSH 别名写入本地忽略记录。自动同步不会重新导入该别名，但仍会继续发现配置中新增加的其他别名；用户手动添加同名别名会被视为主动恢复，并清除对应的忽略记录。

OpenSSH 本身无法告诉应用某个别名是 Direct SSH 还是 Slurm，所以新别名默认作为 Direct SSH 预览；用户确认过的后端选择会在后续同步中保留。

如果仍未找到配置，请使用 [Windows / macOS SSH 配置查找教程](docs/server-config-discovery.md)。

</details>

## 登录与本地数据安全

<details>
<summary><strong>展开查看密码、密钥与 Profile 边界</strong></summary>

- 默认使用系统安装的 OpenSSH 客户端、Alias、ssh-agent 或用户选择的私钥。
- 服务器账号密码只保存在 Windows Credential Manager 或 macOS Keychain。
- Profile 仅保存非秘密引用；密码不会进入 Profile、Git、日志、命令行参数或子进程环境。
- 打包的一次性 askpass 助手通过经过认证的本地回环通道取用密码。
- 完成 SSH Key 引导前，保存密码时该服务器使用密码认证；密钥验证成功后优先使用所选私钥，系统凭据库中的密码只在密钥认证被拒绝时本地回退。
- SSH Key 引导不会覆盖任何现有本地密钥。公钥通过 SSH 标准输入传送；远端会检查 `.ssh` / `authorized_keys` 的文件类型、所有者、权限和重复项，并以非替换方式追加，而不是替换 `authorized_keys`。
- 公钥一旦追加，后续私钥验证或本地 Profile 保存失败时不会自动改写或删除 `authorized_keys`，因为这可能误删并发写入的内容。应用会保留已追加公钥及其匹配的本地生成密钥，返回 `recovery_required`，并提示用户重试或手动精确移除该公钥。
- 应用生成的独立 Ed25519 私钥保存在当前用户的应用配置目录并收紧权限。为了后台监控，它不设置密钥口令；需要口令保护时应选择现有密钥并通过 ssh-agent 加载。
- 首次连接前，请在应用外验证服务器 Host Key。Host Key 变化会被视为安全错误，不会自动重试。
- Profile、服务器目录、缓存、日志、锁和凭据都保留在当前用户电脑，不进入公开安装包。

</details>

## 下载、更新与平台边界

| 项目 | Windows | macOS |
|---|---|---|
| 正式下载 | x64 安装器 | 一个 ZIP，内含 Apple Silicon 与 Intel 两个原生 `.app` |
| 本地凭据 | Windows Credential Manager | macOS Keychain |
| 桌面运行时 | WebView2 / 原生窗口 | Cocoa / WebKit |
| 快捷方式更新 | 固定安装路径，原位升级 | 用户自行替换 `.app` |
| 当前签名状态 | 未签名；默认按用户安装，不触发 UAC | 未签名、未公证；首次需 Finder 确认一次 |
| 更新方式 | 启动后检查并提醒；仅在用户确认、校验和回滚准备完成后安装 | 启动后检查并提醒；校验压缩包后由用户手动替换 `.app` |

完整说明：

- [Windows 安装、通知区域与快捷方式更新](docs/windows-install-and-update.md)
- [macOS 构建、架构与验证边界](docs/macos-desktop.md)
- [v0.7.0 发布说明](docs/release-notes-v0.7.0.md)

## 常见问题

<details>
<summary><strong>没有公钥或私钥，能使用密码登录吗？</strong></summary>

可以。进入本地设置，为对应服务器填写登录密码即可。密码由系统凭据存储保管，不会写进项目或公开包。

</details>

<details>
<summary><strong>为什么自动发现后还要确认 Direct SSH / Slurm？</strong></summary>

OpenSSH 配置只描述如何连接，不描述远端是否使用 Slurm。VRAM Radar 因此先生成安全的本地预览，再让用户确认采集方式。

</details>

<details>
<summary><strong>服务器很多、甚至有上千张 GPU，会不会卡住？</strong></summary>

大规模路径不会一次性渲染或读取全部内容。服务器列表、导航和节点明细分别使用分页 / 窗口化；远端输出限制为单次 8 MiB，最多并行八个采集器。

</details>

<details>
<summary><strong>应用会替我占用、预约或提交 GPU 作业吗？</strong></summary>

不会。VRAM Radar 当前是本地监控与选择辅助工具；作业提交、预约和站点策略继续由 Slurm 或现有平台负责。

</details>

## 文档

| 文档 | 内容 |
|---|---|
| [SSH 配置查找教程](docs/server-config-discovery.md) | 自动发现失败时的 Windows / macOS 分步排查 |
| [界面设计系统](docs/design-system.md) | 信息层级、字体、卡片、SVG 与交互原则 |
| [产品化设计](docs/productization-design.md) | 功能边界与跨平台桌面架构 |
| [Windows 安装与更新](docs/windows-install-and-update.md) | 安装器、快捷方式和通知区域生命周期 |
| [Windows 代码签名](docs/windows-code-signing.md) | SignPath Foundation 申请、信任边界与签名验证门 |
| [macOS 桌面](docs/macos-desktop.md) | Cocoa 包、Apple Silicon / Intel 和验证方式 |
| [隐私说明](PRIVACY.md) | 本地数据、网络连接、诊断与删除方式 |

## 开发与验证

<details>
<summary><strong>展开查看维护者命令</strong></summary>

Windows：

```powershell
uv sync --extra build --frozen
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check src\vram_radar\web\app.js
.\Build-VramRadar.ps1 -SkipSync
.\.venv\Scripts\python.exe tools\validate_packaged_askpass.py
.\.venv\Scripts\python.exe tools\validate_packaged_tray.py
.\Build-VramRadar-Installer.ps1 -SkipBundle
```

macOS：

```bash
uv sync --extra build --frozen
./.venv/bin/python -m unittest discover -s tests -v
node --check src/vram_radar/web/app.js
bash Build-VramRadar-macOS.sh --skip-sync
./.venv/bin/python tools/validate_macos_bundle.py
```

macOS 构建默认使用当前 Python 架构。只有在 Python 和所有原生依赖都包含目标架构时，才使用 `--target-arch=arm64`、`--target-arch=x86_64` 或 `--target-arch=universal2`。

运行源码应用：

```powershell
.\.venv\Scripts\python.exe -m vram_radar --home <local-profile-root> --profile <profile-id>
```

发布验证必须使用空的临时 Profile 和 `--no-auto-import`，避免接触维护者自己的服务器。

</details>

## 反馈与参与

如果自动发现遗漏了某种 SSH 配置方式，或某个 Windows / macOS 环境无法启动，请在 [Issues](../../issues) 中提供系统版本、应用版本和应用生成的**脱敏诊断**。不要上传密码、私钥、真实服务器地址或未经检查的完整日志。

## 许可证

VRAM Radar 采用 [MIT License](LICENSE) 开源。

VRAM Radar 的目标不是增加另一套复杂管理平台，而是让每天都要查看 GPU 的人少切几个终端、更快找到下一台可用机器。
