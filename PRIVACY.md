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
