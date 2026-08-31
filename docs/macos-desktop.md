# macOS desktop

VRAM Radar supports a native macOS desktop bundle that runs the same local
Python collector and dashboard as the Windows application. It uses pywebview's
Cocoa/WebKit backend, macOS Keychain through `keyring`, the installed OpenSSH
client, and the existing direct-SSH and Slurm connectors.

## Product boundary

- The macOS app is a real `.app` desktop bundle, not an iOS app or a browser
  shortcut.
- Profiles, SSH aliases, private-key paths, caches, and credentials remain on
  the Mac. The public bundle contains none of them.
- PyInstaller cannot cross-build a macOS bundle from Windows. The final `.app`
  must be built and tested on macOS.

## Build on macOS

Prerequisites are `uv`, a supported Python 3.12 or 3.13 installation, and the
standard macOS developer command-line tools used by PyInstaller and `codesign`.
From the project root:

```bash
bash Build-VramRadar-macOS.sh
```

The frozen lock installs pywebview's platform-selected PyObjC/Cocoa, Quartz,
Security, UniformTypeIdentifiers, and WebKit dependencies. The build generates
both Windows `.ico` and macOS `.icns` assets and writes:

```text
dist-macos/VRAM Radar.app
```

The output architecture follows the Python interpreter used for the build.
Build on Apple Silicon for `arm64` or on an Intel Mac for `x86_64`. An explicit
target can be requested when the active Python and every collected binary
support it:

```bash
bash Build-VramRadar-macOS.sh --skip-sync --target-arch=universal2
```

PyInstaller fails closed if the requested architecture is not present in the
Python runtime or a native dependency.

The stable release workflow publishes two thin native bundles. The
Apple Silicon bundle is built on the GitHub-hosted macOS 14 arm64 image, while
the Intel bundle is built on macOS 15 x86_64. This gives directly tested
coverage for both current Mac processor families. Because a PyInstaller bundle
inherits the deployment boundary of the macOS version that built it, the
current evidence boundary is macOS 14+ for Apple Silicon and macOS 15+ for
Intel. Supporting older Intel macOS versions requires a maintained older Intel
builder and fresh native validation; it is not inferred from an x86_64 file.

## Validate the built bundle

```bash
./.venv/bin/python tools/validate_macos_bundle.py
```

This validation checks the bundle identifier/version/category, reports the
executable architectures through `lipo`, verifies the current code signature,
compares bundled web assets with source, exercises the packaged password
helper, runs `--show-paths`, then opens and automatically closes the packaged
Cocoa window using an empty disposable Profile and `--no-auto-import`. It must
contact no configured server. With
`VRAM_RADAR_REQUIRE_DISTRIBUTION_SIGNING=1`, it additionally requires a
Developer ID Application signature, a valid stapled ticket, and a passing
Gatekeeper assessment.

The Windows host can validate the shared Python, JavaScript, packaging contract,
shell syntax, icon generation, and Windows regression behavior. It cannot claim
that Cocoa, Gatekeeper, signing, or a specific Mac architecture has run.

## Run and configure

Open `dist-macos/VRAM Radar.app`. macOS stores application state in its normal
per-user application directories unless `--home` is supplied for development.
Configure an OpenSSH alias, key path, or OS-Keychain-backed login password in
the local Profile, verify the remote host key outside VRAM Radar, and use the
same read-only dashboard workflow as on Windows.

Each saved server also exposes the same progressive SSH Key setup used on
Windows. On macOS it uses the system `ssh-keygen` and `ssh`, stores an optional
app-specific Ed25519 private key under the current user's application config
directory with mode `0600`, sends only the public key through SSH stdin, and
verifies the selected identity before updating the Profile. Password bootstrap
continues to use macOS Keychain through the existing scoped askpass broker.

Automatic discovery uses the same code and merge rules as Windows. It checks
`~/.ssh/config`, `~/.config/ssh/config`, system/Homebrew OpenSSH locations,
bounded OpenSSH `Include` files, Colima/OrbStack, stable and preview Remote-SSH
editor settings, and portable `servers.toml` catalogs. The local scan neither
connects to a host nor executes OpenSSH configuration commands. See
[server config discovery](server-config-discovery.md) for manual fallback steps.

For the current unsigned public archive, first launch requires only this
bounded exception: extract the ZIP, right-click the app matching the Mac's
processor, select **Open**, then confirm **Open** once. Do not disable
Gatekeeper globally and do not remove quarantine attributes with a shell
command. If device policy removes the Open option, the device administrator
must approve the app.

## Signing and distribution

The stable GitHub workflow builds each architecture on a native runner and
requires the complete test suite, strict bundle code verification, source asset
matching, packaged askpass exchange, a packaged GitHub update-transport probe,
Cocoa smoke, and exact architecture checks. Version 0.8.4 is distributed
without an Apple Developer ID signature or Apple notarization, so Gatekeeper
may require Finder's right-click **Open**
confirmation on first launch. The maintained `tools/sign_notarize_macos.sh`
path remains available for a future credentialed release, and
`VRAM_RADAR_REQUIRE_DISTRIBUTION_SIGNING=1` retains the stronger Developer ID,
stapling, and Gatekeeper validation gate when that path is selected.

The maintained workflow is manually dispatched with the stable tag and exact
commit SHA. It creates one non-preview GitHub Release only after the Windows,
Apple Silicon, and Intel artifacts all pass. The two validated native apps are
then placed together in one public macOS ZIP. The final ZIP is extracted and
the selected app is launched and fully validated again on both native runner
architectures. A missing or failed architecture blocks the entire release;
version 0.8.4 does not claim notarization.
