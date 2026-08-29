## VRAM Radar 0.4.0

首个同时面向 Windows、Apple Silicon Mac 和 Intel Mac 的正式桌面版本。

### 下载

- Windows 日常安装：`VRAMRadar-Setup-0.4.0.exe`。以后运行新版安装程序会原位升级，原来的开始菜单和桌面快捷方式继续有效。
- Windows 免安装：`VRAMRadar-0.4.0-windows-x64-portable.zip`。
- Apple Silicon（M1/M2/M3/M4）：`VRAMRadar-0.4.0-macos-arm64.zip`。
- Intel Mac：`VRAMRadar-0.4.0-macos-x86_64.zip`。

发布流程已在上传前完成 SHA-256 校验；下载页只保留上面四个用户安装包。

> 本版本的两个 macOS 包未使用 Apple Developer ID 签名，也未经过 Apple
> 公证。首次启动可能出现“无法验证开发者”提示；确认下载来自本仓库后，可在
> Finder 中右键应用并选择“打开”。

### 主要更新

- 重新设计整体界面、首次启动引导、信息层级、字体和重点数值展示。
- GPU 推荐仅在用户主动查询后显示，并突出服务器、分区、卡型、空闲卡数与单卡可用显存。
- 显示登录账号、主目录与可逐级展开的代码工作目录；支持固定默认目录并跨重启和版本更新保存。
- Windows 与 macOS 使用同一套本地只读 SSH 配置自动发现逻辑：进入发现步骤即自动扫描，覆盖用户/XDG/系统/Homebrew OpenSSH、Include、Remote-SSH 稳定版与预览版、Colima、OrbStack 和便携式服务器目录；扫描不连接服务器，也不执行配置命令。
- 右侧服务器快速目录的“任务”分类只列出当前登录用户有运行、排队或暂停任务的服务器，并在该分类内仅汇总本人的任务；其他分类保持原有内容。
- 没有密钥时可使用服务器登录密码；密码只保存在 Windows 凭据管理器或 macOS Keychain，不写入 Profile 或发布包。
- 启动后检查 GitHub 的最新正式版；只提示并打开下载页，不静默下载或安装。
- 移除 iPhone 伴侣端，聚焦 Windows 与 macOS 桌面监控。

### 发布验证

- Windows x64 完成完整单元测试、语法检查、PyInstaller 打包、空 Profile GUI smoke、密码助手验证和 Inno Setup 安装包构建。
- 两个 macOS 包分别在原生 `arm64` 与 `x86_64` GitHub 托管 Mac 上完成完整测试、Cocoa 窗口 smoke、打包密码助手验证、源码资源比对与架构核对；本版本不声明 Developer ID 签名或 Apple 公证。
- 所有发布包均不包含服务器地址、Profile、SSH 凭据、缓存、日志或机器专属配置。
