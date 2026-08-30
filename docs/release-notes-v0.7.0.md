## VRAM Radar 0.7.0

This release adds a user-confirmed, fail-closed update path while preserving the
existing stable installation and shortcut identity.

### Downloads

- Windows: `VRAMRadar-Setup-0.7.0.exe`.
- macOS: `VRAMRadar-0.7.0-macos.zip`, containing separately built and validated
  Apple Silicon arm64 and Intel x86_64 applications.

### Safe updates

- Installer-managed Windows copies now offer **Safe one-click update** after an
  update is detected. Nothing is installed without explicit confirmation.
- The app accepts only the exact official VRAMRadar Release asset name and
  URL, enforces a bounded download size, rejects non-GitHub redirects, and
  verifies GitHub's SHA-256 digest before execution.
- An independent single-file updater closes the running application normally,
  preserves the previous installation, runs the verified installer, probes the
  new executable, and automatically restarts it with the same local Profile.
- If installation or the launch probe fails, the updater restores and relaunches
  the previous version. Profiles, credentials, caches, and logs remain outside
  the replaceable installation directory.
- macOS downloads are also SHA-256 verified and revealed in Finder. Replacing
  the `.app` remains manual until Developer ID signing and notarization are
  configured.

### First updater bootstrap

`v0.6.1` did not contain the independent updater. Users of that version must
install `v0.7.0` manually once. Installer-managed updates after `v0.7.0` can use
the one-click path.

### Configuration reliability and large-fleet usability

- Favorite servers can now use one persistent GPU availability alert. The app
  sends a native system notification when a whole GPU becomes idle or when any
  GPU reaches an optional per-device free-VRAM threshold. Hidden-window refresh
  remains active, cached/offline data never triggers an alert, and each
  unavailable-to-available transition notifies only once.
- Copy SSH now produces a paste-ready OpenSSH `Host` block with verified
  address, user, port, optional identity, and non-interactive safety options.
  Conditional or dynamic configurations fall back to the safe executable
  alias command instead of freezing guessed fields.
- The Mini Navigator now uses a narrower responsive layout lane rather than
  covering dashboard content. It reserves exactly its expanded width on either
  desktop edge and becomes an in-flow row above the main content in compact
  windows.
- The complete desktop interface can now switch between Simplified Chinese and
  English in Settings. The choice is stored with the local Profile and also
  updates native Windows notification-area actions; existing Profiles retain
  Simplified Chinese until the user changes the setting.
- Automatic discovery keeps each imported alias attached to its exact OpenSSH
  source, while `User`, `Port`, `IdentityFile`, `Include`, `ProxyJump`, and
  `ProxyCommand` remain owned by OpenSSH at connection time. Removing an
  imported alias records a local ignore decision so startup synchronization
  does not add it back; newly added aliases are still discovered.
- Import, Profile save, SSH authentication, backend collection, and monitoring
  readiness are separate states. A parsed or saved server is not counted as
  live capacity until the real collector succeeds.
- Saved passwords are removed when the reviewed host, account, effective port,
  alias, or OpenSSH source changes unless the user explicitly re-enters the
  password in that save.
- Monitoring and one-click SSH Key setup share the same bounded password
  fallback policy. Public-key installation is non-replacing and append-only:
  it validates the existing `authorized_keys`, rejects duplicate entries, and
  never replaces the file.
- If identity verification or the local Profile save fails after the append,
  the app does not automatically rewrite or delete `authorized_keys`, because
  that could erase a concurrent edit. It retains the appended public key and
  matching generated local key, returns `recovery_required`, and provides retry
  or exact-key manual-removal guidance.
- Settings keeps a bounded in-memory draft model and renders only 20 server
  forms per page. Search, cross-page edits, password isolation, reordering, and
  close-time DOM cleanup are covered by the 120-server synthetic benchmark.
- Redacted diagnostics copy a bounded support report to the clipboard. Server
  details include connection state, local SSH readiness, credential presence,
  recent error codes, and package/platform facts without exposing hostnames,
  aliases, usernames, paths, commands, keys, or passwords.

### Platform security boundary

The Windows installer is not Authenticode signed. The macOS apps are not
Developer ID signed or notarized because distribution credentials are not
configured for this project. Verify that the package came from this repository's
Latest Release and follow the scoped first-launch instructions in the README;
do not disable platform security globally.

---

## VRAM Radar 0.7.0 中文说明

此版本加入由用户确认、默认拒绝不可信输入的一键更新流程，同时保持原有安装路径
和快捷方式不变。

### 下载

- Windows：`VRAMRadar-Setup-0.7.0.exe`。
- macOS：`VRAMRadar-0.7.0-macos.zip`，内含分别构建并验证的 Apple Silicon
  arm64 与 Intel x86_64 应用。

### 安全更新

- Windows 正式安装版检测到新版本后会显示“安全一键更新”；未经用户确认不会安装。
- 只接受官方 VRAMRadar Release 的精确文件名和下载地址，同时限制文件
  大小、拒绝非 GitHub 重定向，并在执行前校验 GitHub 提供的 SHA-256。
- 独立单文件更新器会正常关闭应用、备份旧安装、运行已校验的安装器、探测新程序
  是否能启动，并使用同一份本地 Profile 自动重启。
- 安装或启动探测失败时，自动恢复并重新启动旧版本。Profile、凭据、缓存和日志
  位于安装目录之外，不参与替换。
- macOS 更新包同样会校验 SHA-256 并在 Finder 中定位；在配置 Developer ID 签名
  和公证之前，替换 `.app` 仍需用户手动完成。

### 首次启用更新器

`v0.6.1` 不包含独立更新器，因此需要手动安装 `v0.7.0` 一次。从 `v0.7.0`
之后，Windows 正式安装版即可使用一键更新。

### 配置可靠性与大规模服务器体验

- 收藏服务器现在可以启用持久 GPU 可用提醒：整张 GPU 空闲，或任意单卡达到
  可选的最低空闲显存阈值时发送原生系统通知。窗口收起后仍会检测；缓存或离线数据
  不会触发；每次从不可用变为可用只提醒一次，不会随刷新重复弹出。
- “复制 SSH”现在生成可直接粘贴进 OpenSSH config 的 `Host` 配置块，包含已验证的
  地址、用户、端口、可选私钥和非交互安全选项；条件式或动态配置会回退到安全的
  可执行别名命令，不会固化猜测字段。
- Mini Navigator 现在使用更窄的响应式独立布局，不再覆盖仪表盘内容；在桌面窗口
  左右两侧都会预留与展开宽度一致的空间，紧凑窗口中则作为主内容上方的正常布局行。
- 设置中可在简体中文和英文之间切换完整桌面界面；语言选择随本机 Profile
  持久化，并同步更新 Windows 通知区域菜单。已有 Profile 默认继续使用简体中文，
  只有用户主动切换后才会改变。
- 自动发现会保留每个别名对应的原始 OpenSSH 配置来源；`User`、`Port`、
  `IdentityFile`、`Include`、`ProxyJump` 与 `ProxyCommand` 仍由 OpenSSH 在连接时
  解析。用户删除过的导入别名会被本地记录为忽略项，启动同步不会反复加回；新增别名
  仍会继续发现。
- 配置解析、Profile 保存、SSH 认证、后端资源读取和监控就绪是不同状态。服务器只被
  解析或保存时不会计入实时资源，必须由实际采集器成功验证。
- 已保存密码所对应的主机、账号、有效端口、别名或 OpenSSH 来源发生变化时，除非用户
  在同一次保存中重新输入密码，否则系统会移除旧密码引用。
- 日常监控与一键 SSH Key 配置共用同一套受限密码回退规则。远端公钥安装采用
  非替换式追加：先校验现有 `authorized_keys` 并拒绝重复项，绝不替换整个文件。
- 公钥追加后若私钥验证或本地 Profile 保存失败，应用不会自动改写或删除
  `authorized_keys`，以免误删并发写入的内容。系统会保留已追加公钥及其匹配的本地
  生成密钥，返回 `recovery_required`，并提示用户重试或手动精确移除该公钥。
- 设置页使用有界内存草稿，每页只渲染 20 台服务器表单。搜索、跨页修改、密码与
  Profile 隔离、排序以及关闭后释放 DOM 均由 120 台服务器的合成基准覆盖。
- “复制诊断”会把有界、脱敏的支持报告写入剪贴板，包含连接状态、本地 SSH 就绪度、
  凭据是否存在、近期错误代码和包/平台信息，但不包含主机名、别名、用户名、路径、
  命令、密钥或密码。

### 平台安全边界

Windows 安装器尚未使用 Authenticode 签名；macOS 应用也尚未使用 Developer ID
签名或公证，因为项目没有配置发行凭据。请确认安装包来自本仓库 Latest Release，
再按 README 的最短首次启动步骤操作；不要全局关闭系统安全保护。
