## VRAM Radar 0.4.3

修复窗口无法显示，并加强 macOS 最终下载包验证。

### 下载

- Windows：`VRAMRadar-Setup-0.4.3.exe`。直接覆盖安装后，原快捷方式继续有效。
- macOS：`VRAMRadar-0.4.3-macos.zip`。M1/M2/M3/M4 用户打开 `VRAM Radar (Apple Silicon).app`，Intel Mac 用户打开 `VRAM Radar (Intel).app`。

下载页只保留上面两个正式安装包。

> macOS 应用未使用 Apple Developer ID 签名，也未经过 Apple 公证。首次
> 启动若被系统阻止，请在 Finder 中右键应用并选择“打开”。

### 修复

- 修复 Windows 通知区域恢复后，窗口可能停留在屏幕外而表现为“程序已运行但打不开”的问题。再次点击快捷方式或托盘“显示 VRAM Radar”时，若窗口不属于任何显示器，会自动移回主屏工作区中央。
- 托盘打包验证现在不仅检查窗口“可见”标记，还确认窗口真实位于某一台显示器上，并覆盖最小化恢复与关闭后恢复两条路径。
- macOS 发布流程新增最终合包验证：公开 ZIP 生成后，再分别在 Apple Silicon 与 Intel Mac 上解压对应应用，重新执行架构、Cocoa 窗口、密码助手、源码资源和空 Profile 验证。只有两个最终应用都能启动，才创建正式 Release。

### 保留功能

- Windows 最小化或关闭窗口后继续驻留通知区域，右键菜单提供显示与退出。
- Mini Navigator 可左右拖放，并且“任务”分类只显示当前用户有任务的服务器。
- 保留自动 SSH 配置发现、密码登录、按需文件夹树、GPU 推荐和最新正式版提醒。
