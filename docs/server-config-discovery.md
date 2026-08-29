# Windows / macOS SSH 配置查找教程

## 先看这里：大多数用户只需要三步

1. 打开显存雷达的“本地配置”，进入“发现服务器”。
2. 等待自动扫描完成；“自动发现并导入”按钮可随时重新扫描。
3. 检查每台服务器是“SSH 直连”还是“Slurm”，然后点击“保存并连接”。

如果你平时已经能在终端运行 `ssh gpu-lab` 登录服务器，`gpu-lab` 就是
OpenSSH 的服务器别名。显存雷达会从 SSH 配置中导入这类别名，然后使用
本机安装的 OpenSSH 获取只读状态。

自动检测只读取固定的本机配置位置，不扫描整个硬盘，不读取私钥、密码、
系统凭据或 `known_hosts`，不会执行 OpenSSH 配置中的命令，也不会在检测阶段
连接任何服务器。发现结果先进入可检查的预览；保存前不会持久化或开始监控。

## 自动检测失败时要找什么

SSH 配置通常是一个名为 `config`、没有 `.txt` 扩展名的文本文件。文件中
会出现类似下面的内容：

```sshconfig
Host gpu-lab
    HostName gpu.example.org
    User researcher
```

- `Host gpu-lab`：服务器别名，也是显存雷达导入的名称。
- `HostName`：真实域名或 IP 地址，继续由 OpenSSH 管理。
- `User`：登录账号，继续由 OpenSSH 管理。
- `IdentityFile`：如果存在，指向私钥；显存雷达不会复制或读取私钥内容。

找到配置文件后，把它的完整路径粘贴到“服务器设置文件路径”，点击
“导入并替换列表”。

## Windows：逐步查找

### 方法一：让 OpenSSH 显示它读取的配置文件

1. 在显存雷达的 Windows 教程中点击 **一键打开 PowerShell**。也可以打开
   开始菜单，搜索 **PowerShell**；它是 Windows 自带的命令窗口。
2. 复制并运行：

```powershell
ssh -G -v any-host-name 2>&1 | Select-String "Reading configuration data"
```

`ssh -G` 只计算配置，不发起服务器连接。输出可能类似：

```text
debug1: Reading configuration data <你的用户目录>\.ssh\config
```

把 `Reading configuration data` 后面显示的真实路径粘贴到显存雷达；通常也可以直接填写 `%USERPROFILE%\.ssh\config`。

如果配置中使用了 `Match exec`，OpenSSH 计算配置时可能执行其中的本地判断
命令；只应在你信任自己的 SSH 配置时手动运行本方法。显存雷达的自动扫描
不会运行这条命令。

如果 PowerShell 提示找不到 `ssh`，请打开 Windows“可选功能”，安装
**OpenSSH 客户端**，然后重新打开 PowerShell。

### 方法二：检查常用目录

在 PowerShell 中查看 `.ssh` 目录：

```powershell
Get-ChildItem -Force "$HOME\.ssh"
```

显存雷达会检查：

- `%USERPROFILE%\.ssh\config`
- `%HOME%\.ssh\config`，适用于 Git for Windows 等工具修改了 `HOME` 的情况
- `%XDG_CONFIG_HOME%\ssh\config`，适用于设置了 XDG 路径的情况
- `.ssh\config.d\*` 和 `.ssh\conf.d\*` 中的配置片段
- `%PROGRAMDATA%\ssh\ssh_config` 和常见独立 OpenSSH 安装目录

### 方法三：没有 config 时创建一个

下面的命令只确保目录存在，然后用记事本打开配置文件；不会清空已有文件：

```powershell
New-Item -ItemType Directory -Force "$HOME\.ssh" | Out-Null; notepad "$HOME\.ssh\config"
```

写入配置并保存时，注意记事本不要把文件保存成 `config.txt`。可以在保存
窗口把“保存类型”设为“所有文件”。

## macOS：逐步查找

### 方法一：让 OpenSSH 显示它读取的配置文件

1. 在显存雷达的 macOS 教程中点击 **一键打开终端**。也可以打开
   “应用程序 → 实用工具 → **终端**”。
2. 复制并运行：

```bash
ssh -G -v any-host-name 2>&1 | grep "Reading configuration data"
```

`ssh -G` 只计算配置，不发起服务器连接。输出可能类似：

```text
debug1: Reading configuration data <你的用户目录>/.ssh/config
```

把 `Reading configuration data` 后面显示的真实路径粘贴到显存雷达；通常也可以直接填写 `~/.ssh/config`。

如果配置中使用了 `Match exec`，OpenSSH 计算配置时可能执行其中的本地判断
命令；只应在你信任自己的 SSH 配置时手动运行本方法。显存雷达的自动扫描
不会运行这条命令。

### 方法二：检查常用目录

```bash
ls -la ~/.ssh
```

显存雷达会检查：

- `~/.ssh/config`
- `~/.config/ssh/config`
- `~/.ssh/config.d/*` 和 `~/.ssh/conf.d/*`
- `~/.colima/ssh_config`
- `~/.orbstack/ssh/config`
- `/etc/ssh/ssh_config`、`/usr/local/etc/ssh/ssh_config` 和
  `/opt/homebrew/etc/ssh/ssh_config` 及其常规片段目录

### 方法三：没有 config 时创建一个

```bash
mkdir -p ~/.ssh && touch ~/.ssh/config && chmod 600 ~/.ssh/config && open -e ~/.ssh/config
```

这条命令会创建目录和文件，设置推荐的仅当前用户可读写权限，然后用 macOS
文本编辑器打开配置。已有文件不会被清空。

## VS Code、Cursor、VSCodium 和 Windsurf

Remote-SSH 扩展可能使用自定义配置文件，而不是标准的 `~/.ssh/config`。

1. 打开编辑器“设置”。
2. 搜索 **Remote.SSH: Config File**。
3. 如果这里填写了路径，复制完整路径。
4. 把路径粘贴到显存雷达的“服务器设置文件路径”。
5. 点击“导入并替换列表”。

也可以直接在编辑器的 `settings.json` 中搜索：

```json
"remote.SSH.configFile": "/完整路径/ssh-config"
```

显存雷达能够自动读取 VS Code、Cursor、VSCodium 和 Windsurf 的稳定版与
常见预览版设置；路径中的 `~`、`${userHome}`、`${env:NAME}` 和系统环境变量
都会按当前用户展开。无效 JSON、超大设置文件或不存在的路径会安全跳过。
导入后，每台服务器会在本地 Profile 中保留这个配置文件的路径。实际连接
使用 `ssh -F 完整路径 服务器别名`，因此不要求再把该文件复制到
`~/.ssh/config`。

## OpenSSH Include 配置片段

有些主配置只包含：

```sshconfig
Include config.d/*
```

显存雷达会继续解析被包含文件中的具体 `Host`。为避免错误配置导致无限
读取，它会检测循环引用，并限制单个文件、总大小和文件数量。通配 Host
（例如 `Host *` 或 `Host *.example.org`）不会被当作可导入服务器。
与 OpenSSH 一致，用户配置中所有非绝对 Include 路径都以 `~/.ssh` 为根，
即使 Include 出现在另一个被包含的文件里也是如此。
系统 OpenSSH 配置中的相对 Include 则以系统 SSH 配置目录为根。

## 导入后的检查

### 1. 选择正确的服务器类型

- 普通工作站、直接运行 `nvidia-smi`：选择“SSH 直连 / nvidia-smi”。
- 使用 `squeue`、`sacct`、`scontrol` 的集群：选择“Slurm / SSH”。

OpenSSH 文件本身不能说明服务器属于哪一种，因此新发现的别名默认按 SSH
直连显示，需要用户确认一次。后续同步会保留已经确认的类型。

### 2. 选择登录方式

- 已经配置 ssh-agent 或私钥：密码留空即可。
- 服务器要求账号密码：在服务器编辑区输入登录密码。
- 密码只进入 Windows Credential Manager 或 macOS Keychain；Profile 和发布包
  中不会保存明文密码。

### 3. 首次连接前确认主机密钥

请先在系统终端连接一次，并确认显示的主机指纹属于正确服务器：

```text
ssh gpu-lab
```

显存雷达不会自动接受未知或变化的主机密钥。

## 常见问题

### “没有可导入的具体 Host 别名”

配置里可能只有 `Host *`、`Match` 或全局选项。增加一个具体别名，例如
`Host gpu-lab`，然后重新导入。

### Windows 上只有 PuTTY Session

PuTTY Session 不是 OpenSSH config，系统的 `ssh.exe` 无法直接使用。请把
主机、端口和用户名转换为 OpenSSH `Host` 条目，或在显存雷达里手动添加。

### 配置在 WSL 中

WSL 的 `~/.ssh/config` 属于 Linux 环境，其中的 Linux 私钥路径和代理设置
不一定能被 Windows OpenSSH 使用。请把需要的别名复制到 Windows 的
`%USERPROFILE%\.ssh\config`，并确认 Windows 终端可以运行 `ssh 别名`。

### 找到多个配置文件

自动发现会按稳定优先级合并并去重，展示一份预览。因为多个来源没有唯一
的同步所有者，多来源导入不会绑定“启动时自动同步此文件”；手动选择单一
文件导入时仍可启用自动同步。

### 导入成功但无法连接

先在终端运行 `ssh 服务器别名`。如果终端也失败，应先修复 OpenSSH 的网络、
VPN、用户名、密钥、密码或主机指纹问题；如果终端成功而显存雷达失败，再
根据应用显示的错误代码检查服务器类型和认证方式。

如果导入的是编辑器或其他工具的自定义配置，显存雷达会自动保留并传给
OpenSSH；不需要手动添加 `-F`。仍然失败时，可以用下面的等价命令检查：

```text
ssh -F "配置文件完整路径" 服务器别名
```
