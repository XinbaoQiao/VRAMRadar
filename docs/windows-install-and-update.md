# Windows install, updates, and shortcuts

## Recommended path

Use `VRAMRadar-Setup-<version>.exe` for an installed copy. Every maintained
installer uses these stable identities:

- application ID: `{1B2F9822-D7AF-47E9-9757-72F98DB2C106}`;
- default directory: `%LOCALAPPDATA%\Programs\VRAM Radar`;
- executable target: `VRAMRadar.exe`;
- shell identity: `VRAMRadar.Desktop`.

When a later setup uses the same application ID, Inno Setup reuses the previous
install directory and task selections. It closes a running copy when possible,
asks a notification-area copy to exit, cleans the replaceable PyInstaller
runtime, writes the new bundle, and recreates
the Start-menu and selected desktop shortcuts against the same executable path.
The shortcut therefore does not contain a release number and does not need to
be created again.

The default is a per-user installation and does not require administrator
permission. A different drive is also supported: choose a directory owned by
your account, such as `D:\Apps\VRAM Radar` or `D:\VRAM Radar`. Windows protects
locations such as `D:\Program Files`; to install there, close Setup and launch
it explicitly with **Run as administrator**. Before replacing any files, Setup
now verifies that it can create, write, and remove a small probe file in the
selected directory. If the check fails, it keeps the existing installation
untouched and explains which path or privilege change is needed.

## Windows security prompt

The current public installer is not Authenticode signed. Windows Defender
SmartScreen can therefore show **Windows protected your PC** for a newly
downloaded release. Confirm that the download came from
this project's GitHub Releases page, then select **More info → Run anyway**. Do not
disable SmartScreen. A managed computer or Windows 11 Smart App Control policy
may block unsigned software without an override; that requires the device
administrator or a future consistently signed distribution.

Signing every release with one verified publisher identity would improve the
publisher signal, but does not guarantee that a new binary will never show a
reputation warning. Microsoft Store distribution is the only maintained path
that removes SmartScreen download warnings by default. No Windows signing
credential is currently configured for this repository, so this release makes
no Authenticode claim.

Minimizing the main window keeps the installed app in the Windows notification
area. Local Settings lets the close button either do the same or exit. Activate
the tray icon to restore the window. Its right-click menu includes the current
online-server/GPU summary, **显示 VRAM Radar**, **立即刷新**, **打开设置**,
**暂停/继续自动监控** and **退出**. These actions reuse the running instance and
do not create a second monitor process.

## What the in-app update notice does

The desktop app checks the public GitHub Release feed after startup. It only
shows that a newer package exists and opens the trusted Release page after the
user clicks the download action. It does not download, execute, or install code
in the background.

To update an installed copy:

1. Open the download action in VRAM Radar.
2. Download the newer `VRAMRadar-Setup-<version>.exe`.
3. Run it normally. Keep the suggested destination unless intentionally moving
   the application.
4. Launch VRAM Radar from the original shortcut.

Profiles, credentials, caches, logs, and locks live outside the install folder,
so replacing the application bundle does not replace local user state.

## Maintainer validation

Build the Windows bundle, then compile the installer:

```powershell
.\Build-VramRadar.ps1 -SkipSync
.\Build-VramRadar-Installer.ps1 -SkipBundle
```

The packaging contract tests pin the stable application ID, directory,
executable target, previous-install reuse, and scoped cleanup rules. A release
must still install an older setup, create its shortcut, install the newer setup
over it, and launch through that original shortcut before publication.
