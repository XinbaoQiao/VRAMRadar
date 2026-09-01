# Windows code-signing path

VRAM Radar is applying for the SignPath Foundation sponsored open-source
program. The current public `v0.8.8` installer remains outside that approval and is
unsigned. Do not describe a Windows artifact as signed until the verification
workflow below succeeds for the exact published file.

## Trust boundary

- The source repository and MIT license are public.
- GitHub-hosted runners build the exact full commit supplied to the workflow.
- SignPath receives GitHub's uploaded artifact identity, not a maintainer-local
  file.
- The SignPath private key never enters this repository or GitHub Secrets.
- GitHub stores only the scoped SignPath API token. Configuration identifiers
  are repository variables, not credentials.
- The application bundle is signed before Inno Setup embeds it. The completed
  installer is then signed in a second request.
- Every `.exe`, `.dll`, and `.pyd` in the returned bundle, plus the final
  installer, must have a valid SignPath Foundation Authenticode signature and a
  trusted timestamp.

## Approval-owned values

After SignPath Foundation accepts the application, configure one Actions secret:

- `SIGNPATH_API_TOKEN`

Configure these Actions variables exactly as assigned by SignPath:

- `SIGNPATH_ORGANIZATION_ID`
- `SIGNPATH_PROJECT_SLUG`
- `SIGNPATH_SIGNING_POLICY_SLUG`
- `SIGNPATH_BUNDLE_ARTIFACT_CONFIGURATION_SLUG`
- `SIGNPATH_INSTALLER_ARTIFACT_CONFIGURATION_SLUG`

The two artifact configurations are deliberately separate. The first signs the
bundle's PE files; the second signs the completed Inno Setup installer. The
maintained `.github/workflows/signpath-windows-validation.yml` workflow fails
closed when any value is absent and never publishes a Release. Its only output
is a short-lived signed candidate for validation.

Once that workflow passes, integrate the same two requests into the stable
release workflow, repeat packaged update validation on the signed installer,
and publish under a new semantic version. Replacing an existing tag is not
acceptable signing or SmartScreen-reputation evidence.
