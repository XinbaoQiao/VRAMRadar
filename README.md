<p align="center">
  <strong>English</strong> · <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <img src="src/vram_radar/assets/app-icon.png" width="96" alt="VRAM Radar icon">
</p>

<h1 align="center">VRAM Radar</h1>

<p align="center">
  <strong>Know where your GPU capacity is—without leaving the flow.</strong>
</p>

<p align="center">
  A local desktop view of GPU capacity, jobs, and connection state across SSH hosts and Slurm clusters.
</p>

<p align="center">
  <img alt="Windows x64" src="https://img.shields.io/badge/Windows-x64-2563EB?logo=windows11&logoColor=white">
  <img alt="macOS Apple Silicon and Intel" src="https://img.shields.io/badge/macOS-Apple_Silicon_%2B_Intel-111827?logo=apple&logoColor=white">
  <img alt="Direct SSH and Slurm" src="https://img.shields.io/badge/Direct_SSH_%2B_Slurm-334155">
  <img alt="Local first" src="https://img.shields.io/badge/local--first-0F766E">
  <img alt="MIT License" src="https://img.shields.io/badge/license-MIT-3B7C6A">
</p>

<p align="center">
  <a href="../../releases/latest"><strong>Download</strong></a>
  · <a href="#quick-start">Quick start</a>
  · <a href="#what-stays-visible">Features</a>
  · <a href="docs/server-config-discovery.md">SSH setup</a>
</p>

![VRAM Radar overview](docs/assets/vram-radar-overview.png)

## Vibe Coding made the code flow easier—and the server state harder to feel

Agents can edit code, run commands, and start long tasks while you stay focused on the outcome. But once the terminal is no longer in front of you, simple operational questions become harder to answer:

- Is the task still running?
- Which server or GPU is it using?
- When will usable capacity become available?

VRAM Radar puts that missing state back into one local desktop view. It combines Direct SSH and Slurm capacity, shows your active work beside the GPUs, and can notify you when a saved availability condition becomes true.

It is intentionally not a scheduler: VRAM Radar does not submit jobs, reserve GPUs, or replace `nvidia-smi`, `nvtop`, or Slurm. It helps you decide **where capacity exists, where your work is running, and which server to open next**.

## What stays visible

| One view for multiple servers | Your jobs beside the GPUs | Availability without constant refreshing |
|---|---|---|
| Compare Direct SSH workstations and Slurm clusters without opening a terminal for each host. | See running and queued work for the current account, with node and GPU context when available. | Save a GPU requirement and receive a one-shot local notification when it becomes available. |

![VRAM Radar server and task details](docs/assets/vram-radar-server-detail.png)

The interface starts with available VRAM and server state. Nodes, tasks, processes, matching controls, and code directories stay collapsed until you need them. Screenshots use synthetic data and contain no private hosts, keys, or Profiles.

## Quick start

1. Download the package for your platform from the [Latest Release](../../releases/latest).
2. Start VRAM Radar and review the SSH aliases found on your computer.
3. Confirm whether each server uses Direct SSH or Slurm, save the Profile, and open the resource view.

Automatic discovery reads common OpenSSH, VS Code, Cursor, Windsurf, Colima, OrbStack, XDG, and Harness catalog locations. Discovery is local and reviewable; finding an SSH entry does not count it as live capacity. A server becomes **monitoring ready** only after its saved connection and collector succeed.

## Downloads and platform boundary

The current public stable release is **v0.8.1**.

| Platform | Download | Current boundary |
|---|---|---|
| Windows x64 | `VRAMRadar-Setup-0.8.1.exe` | Per-user installer; currently unsigned, so SmartScreen may ask for confirmation. |
| macOS | `VRAMRadar-0.8.1-macos.zip` | Contains native Apple Silicon and Intel apps; currently unsigned and unnotarized, so first launch uses Finder's **Open** action. |

The Latest Release contains exactly the two files users need to download. On
Windows, the installer is the recommended download: it preserves the Start-menu
or desktop shortcut across in-place updates, and the public Release no longer
offers a Windows portable ZIP. This release is not signed with an Apple Developer ID
and is not notarized; on first launch, right-click **Open** in
Finder instead of disabling Gatekeeper.

Apple Silicon is currently validated on macOS 14 or newer; Intel x86_64 on macOS 15 or newer. Do not disable SmartScreen or Gatekeeper globally. See the [Windows installation guide](docs/windows-install-and-update.md), [Windows signing status](docs/windows-code-signing.md), [macOS notes](docs/macos-desktop.md), and [v0.8.1 release notes](docs/release-notes-v0.8.1.md) for the exact boundaries.

## Local-first by design

- Server Profiles, caches, logs, locks, and credentials stay on your computer and never enter the public package.
- Passwords are stored only in Windows Credential Manager or macOS Keychain; they are not written to Profiles, logs, argv, or child-process environments.
- Previously unknown SSH Host Keys are saved through OpenSSH on first use; changed Host Keys remain blocked.
- Monitoring is read-only. Job submission, reservation, and site policy remain with Slurm or your existing platform.

Read the full [privacy policy](PRIVACY.md) and [server reliability audit](docs/server-reliability-audit-2026-08-29.md) when you need the implementation details.

## Documentation

- [SSH configuration discovery](docs/server-config-discovery.md)
- [Windows installation and updates](docs/windows-install-and-update.md)
- [macOS builds and compatibility](docs/macos-desktop.md)
- [Product and architecture notes](docs/productization-design.md)
- [Interface design system](docs/design-system.md)
- [Privacy](PRIVACY.md)

## Development

<details>
<summary><strong>Build and test locally</strong></summary>

Windows:

```powershell
uv sync --extra build --frozen
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check src\vram_radar\web\app.js
.\Build-VramRadar.ps1 -SkipSync
```

macOS:

```bash
uv sync --extra build --frozen
./.venv/bin/python -m unittest discover -s tests -v
node --check src/vram_radar/web/app.js
bash Build-VramRadar-macOS.sh --skip-sync
./.venv/bin/python tools/validate_macos_bundle.py
```

Release validation must use an empty temporary Profile with `--no-auto-import`, so maintainer server configuration is never contacted.

</details>

## Feedback and license

Open an [Issue](../../issues) with the OS version, app version, and the app's **redacted diagnostics**. Never upload passwords, private keys, real server addresses, or an unreviewed full log.

VRAM Radar is available under the [MIT License](LICENSE).
