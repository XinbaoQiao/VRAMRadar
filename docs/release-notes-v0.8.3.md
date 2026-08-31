## VRAM Radar 0.8.3

This release fixes the Windows post-install launch path. When Setup starts VRAM
Radar automatically, it now uses the original signed-in user and the installed
application directory as its working directory. Existing OpenSSH configuration,
private keys, Profile data, and server aliases remain authoritative; reinstalling
does not require every server to be authenticated again.

The SSH security boundary is unchanged. Unknown Host Keys use OpenSSH
`accept-new`, changed keys are rejected, and server authentication still uses the
user's configured key, agent, or saved password.

### Downloads

- Windows: `VRAMRadar-Setup-0.8.3.exe`.
- macOS: `VRAMRadar-0.8.3-macos.zip`.

The Windows installer is not Authenticode signed. The macOS apps are not
Developer ID signed or notarized.

---

## VRAM Radar 0.8.3 中文说明

此版本修复 Windows 安装完成后的自动启动路径。安装器自动启动显存雷达时，
现在会明确使用原登录用户身份，并将已安装程序目录设为工作目录。已有 OpenSSH
配置、私钥、Profile 和服务器别名继续生效；重新安装不应再要求所有服务器重新认证。

SSH 安全边界没有改变：首次出现的 Host Key 使用 OpenSSH `accept-new` 保存，
发生变化的密钥仍会被拒绝，服务器登录继续使用用户原有的私钥、ssh-agent 或已保存密码。

### 下载

- Windows：`VRAMRadar-Setup-0.8.3.exe`。
- macOS：`VRAMRadar-0.8.3-macos.zip`。

Windows 安装包尚未 Authenticode 签名；macOS 应用尚未 Developer ID 签名或公证。
