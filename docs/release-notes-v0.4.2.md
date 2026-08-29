## VRAM Radar 0.4.2

Windows 与 macOS 的正式稳定更新。

### 下载

- Windows：`VRAMRadar-Setup-0.4.2.exe`。安装路径和快捷方式目标保持固定，直接覆盖升级后原快捷方式继续打开最新版。
- macOS：`VRAMRadar-0.4.2-macos.zip`。一个压缩包同时包含 Apple Silicon 与 Intel 两个已原生验证的应用；M1/M2/M3/M4 用户打开 `VRAM Radar (Apple Silicon).app`，Intel Mac 用户打开 `VRAM Radar (Intel).app`。

发布流程在上传前完成原生架构验证和 SHA-256 校验；下载页只保留上面两个用户安装包。

> 本版本的 macOS 应用未使用 Apple Developer ID 签名，也未经过 Apple
> 公证。首次启动可能出现“无法验证开发者”提示；确认下载来自本仓库后，可在
> Finder 中右键应用并选择“打开”。

### 主要更新

- 将 Apple Silicon 与 Intel Mac 应用合并为一个 macOS 下载包，减少用户选择成本，同时保留两种架构各自的原生应用和验证证据。
- Windows Release 只提供正式安装版，不再提供便携 ZIP。
- 服务器 Mini Navigator 可在左右两侧拖放并持久保存位置；“任务”分类只显示当前用户确实存在任务的服务器。
- Windows 最小化或关闭主窗口后进入通知区域，右键菜单提供显示与退出。
- 保留自动 SSH 配置发现、密码登录、按需文件夹树、GPU 推荐、最新正式版提醒以及分层展开/收起功能。

### 发布验证

- Windows x64 完成完整单元测试、JavaScript/Python 语法检查、PyInstaller 打包、空 Profile GUI smoke、托盘生命周期、密码助手和 Inno Setup 安装包验证。
- Apple Silicon 与 Intel 应用分别在原生 GitHub 托管 Mac 上完成完整测试、Cocoa 窗口 smoke、密码助手、源码资源比对与架构核对，再由 macOS 合包任务验证两个原生校验和、应用版本及主程序/密码助手架构后生成单一 ZIP。
- 所有发布包均不包含服务器地址、Profile、SSH 凭据、缓存、日志或机器专属配置。
