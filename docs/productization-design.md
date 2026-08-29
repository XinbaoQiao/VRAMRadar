# 显存雷达（VRAM Radar）产品化设计

状态：跨平台修订 v2（2026-08-27）
范围：离线交互、视觉品牌、独立桌面 App、多用户/多配置实例
现有基础：Python 本地 HTTP 服务、Slurm 查询、Direct SSH `nvidia-smi` 查询

## 1. 结论

采用 **Windows 与 macOS 共用的 pywebview 桌面壳 + Python 采集核心 +
平台原生 PyInstaller 包**。Windows 使用 WinForms/WebView2 与 onedir，macOS
使用 Cocoa/WebKit 与 `.app` BUNDLE；两端共享 HTML/CSS/JavaScript、查询代码和
Profile 模型，并解除对特定开发工作区、固定路径、固定端口及固定服务器 ID
的依赖。

产品工作名为 **显存雷达 / VRAM Radar**。桌面 App 只在本机运行；每位操作系统用户拥有独立配置、缓存、日志和凭据引用。一个用户可创建多个 Profile，并以 `--profile <id>` 启动互不干扰的实例。

“多租户”在桌面产品中定义为 **本机用户隔离 + Profile 隔离**，不是把 SSH 凭据上传到中央 SaaS。未来若做云端团队版，应另行设计服务端租户、鉴权、审计和密钥托管，不能直接复用桌面信任模型。

## 2. 目标架构

```text
VRAMRadar.exe / VRAM Radar.app
├─ Desktop Shell (pywebview / native window / tray)
├─ UI (HTML + CSS + JS)
├─ Application Service
│  ├─ Poll Scheduler
│  ├─ Per-server State Machine
│  └─ Summary Calculator
├─ Connectors
│  ├─ DirectSshNvidiaConnector
│  └─ SlurmSshConnector
└─ Local Storage
   ├─ App Settings
   ├─ Profiles
   ├─ Snapshot Cache
   ├─ Logs
   └─ OS Credential Store references
```

关键边界：

- UI 不拼 SSH 命令；只消费规范化状态模型。
- Connector 不读开发工作区的全局配置；只接收已经校验的 Profile。
- 非秘密配置写入用户配置目录；密码、私钥口令或令牌进入操作系统凭据库。
- 每台服务器独立采集、独立失败；一台离线不能阻塞其他服务器刷新。
- 缓存值永远标记来源和年龄；离线缓存不计入“当前可用显存”。

建议代码布局：

```text
vram_radar/
  app.py
  domain/models.py
  services/poller.py
  services/summary.py
  connectors/base.py
  connectors/ssh_nvidia.py
  connectors/slurm_ssh.py
  storage/config_store.py
  storage/cache_store.py
  security/secret_store.py
  web/index.html
  web/app.css
  web/app.js
```

## 3. 离线状态模型

### 3.1 每服务器状态

| 状态 | 判定 | UI | 汇总口径 | 自动行为 |
|---|---|---|---|---|
| `connecting` | 首次连接尚未完成 | 骨架占位 + “正在连接” | 不计入 | 受控等待 |
| `online` | 本轮成功且数据有效 | 正常数据卡 | 计入 | 正常刷新 |
| `stale` | 本轮失败但有成功缓存 | 显示旧值、数据年龄和异常原因 | 不计入实时汇总 | 指数退避重试 |
| `offline` | 失败且无任何缓存 | 完整空状态占位 | 不计入 | 指数退避重试 |
| `auth_required` | 密钥、密码或权限失败 | 锁形状态 + “修复凭据” | 不计入 | 停止自动重试 |
| `security_blocked` | Host Key 改变或不匹配 | 高优先级安全警告 | 不计入 | 必须人工确认 |
| `misconfigured` | 命令缺失、配置或响应格式错误 | 配置诊断 | 不计入 | 配置改变前不重试 |

### 3.2 错误分类

错误对象不得只传一段异常字符串，应包含：

```json
{
  "code": "ssh_timeout",
  "message": "服务器在 10 秒内未响应",
  "retryable": true,
  "occurred_at": "2026-08-26T03:00:00Z",
  "retry_at": "2026-08-26T03:00:30Z",
  "diagnostic_id": "evt_01..."
}
```

首批稳定错误码：`dns_failed`、`ssh_timeout`、`ssh_refused`、`host_key_changed`、`auth_failed`、`command_missing`、`permission_denied`、`parse_failed`、`config_invalid`、`unknown`。

### 3.3 缓存与重试

- 每台服务器只保存最近一次成功快照，采用原子写入，包含 schema 版本、时间、Profile ID 和采集器版本。
- 连接失败后保留旧数据，但所有旧值附带“最后成功于……”和 `stale` 标记。
- 顶部实时汇总只计算 `online` 服务器；另显示“1 台服务器的数据已过期”，避免把旧显存误认为仍可使用。
- 前台在线轮询沿用 15 秒；离线后使用约 15、30、60、120、300 秒退避并加入少量 jitter。用户点击“立即重试”可跳过当前等待。
- `auth_required`、`security_blocked`、`misconfigured` 不进行无限自动重试，避免凭据锁定、SSH 噪声和无意义等待。
- 应用重新启动时先秒开缓存，再在后台更新；缓存必须清楚标记，不能短暂伪装成实时数据。

### 3.4 离线 UI

部分离线：

- 顶部橙色全局提示说明“在线服务器继续刷新”。
- 离线服务器仍占原位置，避免布局跳动。
- 卡片显示最后成功时间、精确错误类别、下一次自动重试倒计时。
- 主操作为“立即重试”，次操作为“连接设置”；日志放到三级入口。

首次离线：

- 使用雷达空扫占位图，不绘制虚假的 `0 GiB`。
- 文案区分“没有 GPU”与“尚未取得数据”。
- 提供“打开连接设置”和“复制诊断摘要”，不要求用户阅读 Python 堆栈。

## 4. 视觉与品牌

### 4.1 品牌方向

- 名称：显存雷达（中文）/ VRAM Radar（英文）。
- 核心意象：GPU 芯片 + 雷达扫描 + 四段显存单元。
- 主色：电光青 `#13BFE0`；健康状态：翠绿 `#26D985`。
- 警告和故障只用于状态：琥珀 `#EFA943`、故障红 `#F06B75`。
- 深色和浅色主题共享同一信息层级；状态必须同时使用图标/文字，不能只靠颜色。

### 4.2 图标交付规则

当前概念稿可作为方向确认，不直接作为最终小尺寸图标。正式图标应再做一次确定性矢量重绘，减少雷达刻度和显存条细节，然后导出：

- Windows：`app.ico`，包含 16/20/24/32/48/64/128/256 px。
- macOS：`app.icns` / AppIcon 集合。
- Linux：SVG 源文件及 32/64/128/256/512 px PNG。
- 托盘图标另做 16/20/24 px 单色版本，不能直接缩小彩色主图标。

### 4.3 界面语言

- 应用壳优先呈现“服务器状态 → 可用资源 → 异常修复”，不堆叠无决策价值的装饰卡片。
- Direct SSH 与 Slurm 使用相同卡片骨架，但明确显示“实时显存”或“调度容量”口径。
- 表格在桌面宽度保留；窄屏改为纵向信息块或允许表格自身横向滚动，不允许整个窗口水平溢出。
- 数据更新时间贴近服务器标题；全局状态只表达真正可比较的在线值。
- 动画限制为轻微刷新反馈和离线雷达空扫，并遵循系统“减少动态效果”设置。

## 5. 配置解耦与实例隔离

### 5.1 文件位置

使用 `platformdirs` 获取平台原生用户目录。Windows 示例：

```text
%LOCALAPPDATA%/VRAMRadar/
  config/app.toml
  profiles/<profile-id>.toml
  cache/<profile-id>/<server-id>.json
  logs/app.log
  runtime/<profile-id>.lock
```

安装目录只读，不在 EXE 旁写配置。卸载程序默认保留用户 Profile，并提供单独的“清除本地数据”选项。

### 5.2 Profile schema

```toml
schema_version = 1
id = "lab-gpus"
display_name = "实验室 GPU"
refresh_seconds = 15

[[servers]]
id = "zju-4090"
display_name = "4090 at ZJU"
backend = "direct_ssh"
host = "gpu.example.edu"
port = 22
username = "alice"
auth_ref = "vram-radar/lab-gpus/zju-4090"

[[servers]]
id = "cuhk-slurm"
display_name = "A100 Cluster"
backend = "slurm_ssh"
host = "login.example.edu"
port = 22
username = "alice"
auth_ref = "vram-radar/lab-gpus/cuhk-slurm"
```

Host Key 信任由系统 OpenSSH 的 `known_hosts` 管理；应用不会显示一个未实际执行的独立“指纹已配置”状态。

约束：

- 配置禁止保存明文密码、私钥内容、令牌或私钥口令。
- `auth_ref` 只指向 Windows Credential Locker、macOS Keychain 或 Linux Secret Service 中的秘密。
- 支持系统 `ssh-agent` 和用户选择现有私钥路径；私钥路径不是秘密，但导出 Profile 时默认移除绝对路径。
- 首次连接显示 Host Key 指纹并要求确认；指纹变化进入 `security_blocked`，绝不自动接受。
- Profile 导出只包含脱敏非秘密配置，可用作团队模板；导入后用户自行绑定凭据。

### 5.3 首次配置向导

1. 创建 Profile，选择 Direct SSH 或 Slurm。
2. 填写显示名、主机、端口、用户名和认证方式。
3. 测试网络与 SSH，确认 Host Key。
4. 自动检查 `nvidia-smi` 或 `sinfo/squeue/scontrol`，展示能力检测结果。
5. 预览第一份快照并保存。

每一步只显示与当前后端有关的字段。高级用户可导入 TOML 模板或使用 `VRAMRadar.exe --profile <id>`。

### 5.4 多实例

- 默认同一 Profile 单实例；再次打开时激活已有窗口。
- 不同 Profile 可并行运行，各自持有 `<profile-id>.lock`、缓存、日志和托盘状态。
- 不再使用固定 `8765` 端口。pywebview 内嵌模式通过进程内桥接访问 Python；若开发模式仍启用 HTTP，则动态分配 loopback 端口并使用随机会话令牌。

## 6. 打包决策

### 推荐：pywebview + PyInstaller onedir

原因：

- 能最大程度复用现有 Python 采集器和 Web UI。
- pywebview 使用系统原生 WebView，提供独立窗口、菜单、事件和 JavaScript/Python 桥接，不再借助 Edge `--app` 或 PowerShell 启动器。
- PyInstaller `onedir` 避免 `onefile` 每次启动解包和临时目录问题，便于签名、增量升级和故障诊断。
- Windows 发布物再由安装器封装，用户最终仍只接触一个安装程序和一个开始菜单入口。

不作为首选：

| 方案 | 优点 | 当前代价 | 结论 |
|---|---|---|---|
| pywebview + PyInstaller | 最大复用、迁移短、系统 WebView | 仍需管理 Python 冻结依赖 | 首选 |
| Tauri + Python sidecar | 壳轻、更新与权限模型更现代 | Rust/前端构建链 + sidecar 协议，当前迁移面过大 | 产品成熟后再评估 |
| Electron | 生态成熟、调试方便 | 运行体积和内存开销与轻量监控工具定位不符 | 不采用 |

Windows 交付建议：

- `PyInstaller --onedir --windowed` 生成可签名目录包。
- 安装器负责开始菜单、桌面快捷方式、卸载、WebView2 依赖检查和升级。
- EXE、安装器和更新清单做代码签名；发布 CI 生成 SHA-256、SBOM、依赖锁和构建日志。
- 在干净 Windows 用户、无 Python、无开发工作区、无预设 SSH alias 的虚拟机上做验收。

macOS 交付建议：

- 在 macOS 原生 Python 环境运行同一 PyInstaller spec，选择 Cocoa/WebKit、
  macOS Keychain 和 ICNS 图标并生成 `VRAM Radar.app`。
- 以空 Profile 执行 Info.plist、资源一致性、`--show-paths` 和真实 Cocoa
  窗口 smoke；不能用 Windows 静态检查替代。
- v0.4.0 按已披露的无 Apple 凭据边界发布：两个原生架构仍须分别完成完整
  测试、Cocoa smoke、资源一致性、密码助手和 CPU 架构核验，README 与
  Release Notes 必须说明未 Developer ID 签名/未公证及首次打开方式。
- 未来一旦声明 Apple 分发签名，则恢复 Developer ID hardened-runtime
  签名、notarization、stapling 和 Gatekeeper 验证，并逐一记录所声明架构。

## 7. 迁移阶段

### Phase 0：状态模型硬化

- 把当前“一台失败导致整个快照回退”改为逐服务器隔离。
- 引入结构化错误、缓存来源、数据年龄和离线汇总规则。
- 增加连接超时、认证失败、命令缺失、解析失败、缓存损坏测试。

完成门槛：任意一台服务器断网时，其余服务器继续刷新；UI 不显示虚假的 0 值。

### Phase 1：配置与桌面壳

- 抽离开发工作区依赖，建立 Connector 接口和公开 Profile schema。
- 接入 `platformdirs`、操作系统凭据库、Host Key 校验和首次配置向导。
- 用 pywebview 取代 Edge/PowerShell 启动链，并加入托盘与单实例锁。

完成门槛：Windows 或 macOS 用户不安装项目 Python、不复制开发工作区，也能
从平台原生应用创建 Profile 并看见 GPU。

### Phase 2：安装包与发布工程

- PyInstaller onedir、安装器、图标多尺寸、版本信息、签名和升级策略。
- 干净虚拟机测试安装、首次启动、升级、卸载、离线启动和日志导出。

完成门槛：发布物不含用户路径/服务器信息/秘密；升级保留配置且可回滚。

### Phase 3：外部可用性

- 脱敏 Profile 模板导入/导出。
- 英文界面、可访问性、诊断包和隐私说明。
- 根据真实用户反馈决定是否继续扩展 Linux 或迁移到 Tauri；macOS 已进入
  当前 PyInstaller/Cocoa 路线。

## 8. 验收清单

- 一台、部分和全部服务器离线时都有明确且不同的 UI。
- 缓存数据标注年龄、来源和可否参与汇总。
- 断网重启 App 可秒开缓存，不阻塞窗口。
- Host Key 改变不会被静默接受。
- Profile 导出扫描不到密码、密钥、令牌和用户绝对路径。
- 两个 Profile 可并行运行且缓存、日志、凭据引用互不串用。
- 安装包在无 Python、无开发工作区的干净 Windows 环境运行。
- `.app` 在目标 Mac 上通过 Cocoa 窗口、Keychain 与架构验收；声明 Apple
  分发签名的版本还必须通过 Developer ID、公证、stapling 和 Gatekeeper。
- 主图标在 16 px 可辨认；托盘图标在深浅任务栏均清晰。
- 所有状态不只依赖颜色，并支持键盘操作和系统减少动态效果设置。

## 9. 推荐的下一步

当前共享核心和 Windows 打包已完成；下一步是在真实 Mac 上执行
`Build-VramRadar-macOS.sh` 与 `tools/validate_macos_bundle.py`，再根据目标分发
范围决定单架构或 universal2，以及 Developer ID/notarization 流程。
