# Codex usage monitoring

In **Settings → Extensions**, turn on **Codex usage monitoring**. The switch
saves immediately, automatically detects local Codex and restores monitoring on
the next Radar launch. No path, API key or separate Apply step is needed. It does
not discover or connect to SSH servers. The feature is off by default. It can
also be used before adding a server.

Install Codex and sign in with a ChatGPT account first. Radar reuses that local
Codex installation; an API-key login does not provide subscription quota.
Windows and macOS use the same app-server protocol and interface. Detection
covers the Windows desktop versioned bin directory, PATH, npm native payloads
(including custom prefixes) and the official VS Code Codex extension. macOS
also checks the system/user Applications folders, common CLI locations and
the matching native VS Code extension. Discovery runs again for each read, so
desktop upgrades do not leave a saved path pointing at the old version.
If needed, expand **Details and settings → Advanced: choose an executable**
and save an absolute CLI executable path (not a
Windows `.cmd` shim or the macOS `.app` directory). Radar does not install Codex
or initiate sign-in. After installation or sign-in, it retries automatically
every 15 seconds until ready, then returns to the normal five-minute refresh.
**Restore automatic detection** clears a previous manual path in one click.

Windows uses CodexUsage's two-line widget, recolored with Radar's palette and a
tighter number column. It uses a compact DPI-scaled size capped by the taskbar
height and defaults to a locked position immediately left of the notification
area with a one-pixel external gap. Longer text expands as needed. Right-click **Move freely** to enable
free dragging, or **Lock to taskbar** to restore the fixed position. The context
menu and widget background follow the Windows taskbar's light/dark theme and
optional system accent color, updating without restarting Radar. The surface
uses a solid theme color rather than reproducing Explorer's wallpaper blur.
The context menu uses spacious single-line actions with small icons. Its size and
type update when the taskbar DPI changes. Opening the menu cancels a pending
single click. Docked widgets hide while the taskbar is hidden/restarting; widgets
yield to a fullscreen app on the same monitor and restore automatically, checked
once per second. A normal maximized window is not treated as fullscreen. Single-click
opens the GPU home page; double-click opens Codex usage details. A single click
waits for the system double-click interval so double-clicking does not open both.
The right internal padding is two logical pixels. **Hide usage strip** turns the display off; re-enable
it in Settings → Extensions. Placement resets to the taskbar on the next launch.
Right-click **Display options** to choose **Text only** (default) or **Disks and
text**. Countdown values consistently use one decimal place and the `h` suffix,
such as **87.5h**; less than 0.1 hour reads **<0.1h**. Both lines use the same bold
font size. Colors change continuously from cool blue/green toward amber/coral
as quota decreases or reset approaches; long reset waits also continue changing.
Unavailable/stale data is gray. Layout choices are saved in the Profile and
restored after restart, without requesting a server refresh. A single placement
action switches between fixed taskbar placement and free movement. Clicking
outside the native menu dismisses it and its submenu.
The optional disks follow the corresponding quota and reset-wait colors. Both adapt to the
system theme. Both disks share an identical pixel diameter and neutral circular
track, with integer-aligned row centers. The widget outline appears only on hover. One actual quota
window is shown at a time (the first returned window); the period picker has been
removed. Hover shows the period and all quota details. Double-click for
quota details/settings, or unlock and drag to reposition. The widget
stays visible with Radar hidden, stays within the available screen area, and
does not take keyboard focus or add a taskbar button. Right-click also provides
refresh, disable and quit actions. macOS uses a compact native menu-bar item with percentages
and countdowns, plus a menu for details and actions. Closing Radar's main window
with **hide to tray** selected keeps the active menu-bar display available.

Settings keeps a compact switch row; **Details and settings** holds the optional
path, refresh action and quota details. There is no quota card on the GPU home
page. Hover over a reset countdown in settings to see the local reset date/time.
Quotas are shared across the signed-in
account, not specific to Radar or a conversation. A five-hour or weekly window
is displayed only when reported. Missing data is shown as unavailable; expired
windows wait for a successful refresh instead of assuming the quota reset.

The native background worker checks every five minutes, including while Radar
is minimized. **Refresh usage** requests an earlier check (with a ten-second
retry floor). Disabling the feature cancels its pending request and clears the
display. Errors clear previously displayed quota so switching or signing out
of accounts cannot leave an old account's quota presented as current.

Radar starts its own short-lived `codex app-server` child and sends only
`initialize`, `initialized`, `account/read` with `refreshToken: false`, and
`account/rateLimits/read`. Codex owns authentication and contacts its usage
service; Radar never opens Codex credential files or asks for tokens. Raw
account identity, stderr and RPC errors are not logged or passed to the web UI.
Only plan, quota windows and fetch time are kept in memory. The Profile stores
the enable switch and optional executable path. The child has a bounded
timeout, hidden Windows startup, and cleanup of its own process tree.

This feature follows the quota-reading approach of
[Amygdala42/CodexUsage](https://github.com/Amygdala42/CodexUsage), adapted to
Radar's Python/WebView architecture and both platforms. Attribution and the
MIT license are in [third-party notices](third-party-notices.md). The upstream
public reset-announcement feed is not included; Radar shows account-specific
usage in its own native strip/menu-bar display.

Local development acceptance uses synthetic RPC children and an empty Profile.
`tools/validate_usage_surface.py` tests the native strip/menu-bar lifecycle without
logging into Codex. Windows build and native UI evidence are recorded in the project status. A new
macOS release still requires native Apple Silicon and Intel build/UI validation;
portable parser/discovery tests alone do not establish that release claim.
