## VRAM Radar 0.4.1

Windows、Apple Silicon Mac 和 Intel Mac 的正式稳定更新。

### 下载

- Windows 日常安装：`VRAMRadar-Setup-0.4.1.exe`。安装路径和快捷方式目标保持固定，直接覆盖升级后原快捷方式继续打开最新版。
- Apple Silicon（M1/M2/M3/M4）：`VRAMRadar-0.4.1-macos-arm64.zip`。
- Intel Mac：`VRAMRadar-0.4.1-macos-x86_64.zip`。

发布流程在上传前完成 SHA-256 校验；下载页只保留上面三个用户安装包。

> 本版本的两个 macOS 包未使用 Apple Developer ID 签名，也未经过 Apple
> 公证。首次启动可能出现“无法验证开发者”提示；确认下载来自本仓库后，可在
> Finder 中右键应用并选择“打开”。

### 主要更新

- 服务器 Mini Navigator 默认位于右侧，可拖到左侧或拖回右侧；选择写入本地 Profile，重启和版本更新后继续保留。
- Mini Navigator 的“任务”分类只显示当前登录账号确实存在活动任务或 GPU 进程的服务器，不再混入无本人任务的服务器。
- Windows 最小化或关闭主窗口后进入通知区域；点击图标恢复，右键菜单提供“显示 VRAM Radar”和“退出”。
- Windows 安装器沿用固定 AppId、固定安装目录和固定可执行文件目标，并在升级前优雅退出托盘实例；原开始菜单和桌面快捷方式无需重建。
- 保留自动 SSH 配置发现、密码登录、按需文件夹树、GPU 推荐、最新正式版提醒以及分层展开/收起功能。

### 发布验证

- Windows x64 完成完整单元测试、JavaScript/Python 语法检查、PyInstaller 打包、空 Profile GUI smoke、托盘生命周期、密码助手和 Inno Setup 安装包验证。
- 两个 macOS 包分别在原生 `arm64` 与 `x86_64` GitHub 托管 Mac 上完成完整测试、Cocoa 窗口 smoke、密码助手、源码资源比对与架构核对；本版本不声明 Developer ID 签名或 Apple 公证。
- 所有发布包均不包含服务器地址、Profile、SSH 凭据、缓存、日志或机器专属配置。
