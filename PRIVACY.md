# Privacy

VRAM Radar is a local-first desktop application. It does not include product
analytics, advertising SDKs, crash-reporting uploads, or a VRAM Radar account
service.

## Data kept on your computer

VRAM Radar stores its Profiles, server catalog, preferences, caches, and logs
under the application data directory on the computer where it runs. Passwords
are stored through Windows Credential Manager or macOS Keychain rather than in
the Profile. SSH private keys remain in the local paths selected by the user.

## Network connections

The application connects only to:

- SSH servers that the user imports or configures;
- GitHub's public release service to check for VRAM Radar updates; and
- a GitHub Release asset after the user accepts an available download or update.

When the optional **Settings → Extensions → Quota monitoring** feature is enabled,
Radar also asks the locally installed Codex app-server to query its account usage
service. Codex handles its own sign-in and network requests. Radar does not open
Codex credential files, send prompts, or copy tokens, account email, or raw RPC
output into its Profile, logs, diagnostics, or interface. The enable switch and
optional executable path are saved locally; quota values are kept in memory.
Turning the feature off cancels its pending query. See [Codex usage](docs/subscription-usage.md).

The usage strip's **Models** menu can also show other AI desktop apps found on
this computer (DeepSeek Harness, Grok, Kimi, Claude, GLM, Qwen, Tencent Yuanbao),
up to four at a time. Detection reads only installation facts (uninstall registry
entries, registered packages, process names) and small non-secret status files or
log lines those apps write themselves.

**Read quota automatically** (Grok, Kimi) is off until the user allows it in a
consent dialog; the choice is saved per app and can be revoked from the strip's
right-click menu. Only with that consent, VRAM Radar decrypts the app's own stored
sign-in in memory and sends the same read-only quota request the app makes for
its own account page (Grok: one `GetSandUsageStatus` request; Kimi: its
subscription and usage queries), at most every 5 minutes. The login data is never
refreshed, saved, logged, shown or uploaded elsewhere, and results stay in memory.
Without consent no such request is made and encrypted credential stores are not
opened. While **Grok** is selected, VRAM Radar may also read the usage figure Grok
shows in its own account menu through Windows UI Automation, only while the Grok
window is in the foreground and only inside open menus; it never clicks, types or
changes anything in Grok.

A further network read exists only while **DeepSeek** is selected in the Models
menu: VRAM Radar repeats the same read-only wallet query DeepSeek Harness makes
for its own account page (`GET https://platform.deepseek.com/api/v0/users/get_user_summary`,
or the public `GET https://api.deepseek.com/user/balance` for a plain API key),
using the sign-in DeepSeek Harness stored in `~/.dsh/.credentials.yaml`. The
token is read into memory for that single request, sent only to DeepSeek's own
origin (redirects refused), and never saved, logged or shown. It runs at most
every 5 minutes, with backoff after errors. Results stay in memory; only the
list of selected apps is saved. See [AI app providers](docs/ai-app-providers.md).

VRAM Radar does not send server addresses, SSH configuration, credentials,
remote file listings, GPU status, job data, or application logs to a VRAM Radar
service. GitHub and each user-configured server process connection metadata
under their own policies.

## Diagnostics

The **Copy diagnostics** action produces a locally redacted report for the user
to review and share voluntarily. It is copied to the clipboard and is not
uploaded automatically. Users should still review it before posting it publicly.

## Removal and questions

Users can remove Profiles and credentials through the application and can
remove remaining local application data after uninstalling. Privacy questions
and reports can be opened in the project's [GitHub Issues](../../issues).
