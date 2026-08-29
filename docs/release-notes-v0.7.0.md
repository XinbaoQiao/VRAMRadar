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

### 平台安全边界

Windows 安装器尚未使用 Authenticode 签名；macOS 应用也尚未使用 Developer ID
签名或公证，因为项目没有配置发行凭据。请确认安装包来自本仓库 Latest Release，
再按 README 的最短首次启动步骤操作；不要全局关闭系统安全保护。
