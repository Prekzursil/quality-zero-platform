# Provider browser bootstrap

`quality-zero-platform` keeps provider-admin browser state **outside** the repository tree and reuses a single dedicated Playwright Chromium profile for strict-zero provider administration.

## What this bootstrap is for

Use it for provider-admin tasks that are sourced from the provider UI rather than repo-tracked automation, such as:

- verifying GitHub repository bindings and default branches
- manually signing in once and reusing the authenticated profile later
- checking provider dashboards before tightening rulesets or required checks
- collecting or confirming project tokens without storing them in the repo

Supported providers:

- Codecov
- Qlty
- Chromatic
- Applitools
- SonarCloud
- Codacy
- DeepScan
- Sentry

## State model

The bootstrap uses an external state root, not repo-tracked files.

Default Windows state root:

```text
%LOCALAPPDATA%\quality-zero-platform\provider-ui
```

Important external paths:

- persistent browser profile: `%LOCALAPPDATA%\quality-zero-platform\provider-ui\chromium-profile`
- external Playwright runner: `%LOCALAPPDATA%\quality-zero-platform\provider-ui\pw-runner`

Override the state root with:

```powershell
$env:QUALITY_ZERO_PROVIDER_UI_HOME = 'D:\provider-ui-state'
```

## First run: manual login handoff

```powershell
pwsh -File scripts/provider_ui/bootstrap-provider-ui.ps1 bootstrap --provider codecov --repo Prekzursil/quality-zero-platform --headed
```

What happens:

1. the PowerShell wrapper installs Playwright into the external runner dir if needed
2. a persistent Chromium profile is launched with the requested provider page
3. you sign in manually in the opened browser window
4. after finishing the provider check, press Enter in the terminal to persist the authenticated profile for later reuse

If the machine does not already have the Playwright Chromium browser installed, run:

```powershell
pwsh -File scripts/provider_ui/bootstrap-provider-ui.ps1 install-browsers
```

## Later runs: authenticated reuse

Headless inspection after the first login handoff:

```powershell
pwsh -File scripts/provider_ui/bootstrap-provider-ui.ps1 inspect --provider qlty --repo Prekzursil/quality-zero-platform --headless
```

Open the stored profile again in a visible browser:

```powershell
pwsh -File scripts/provider_ui/bootstrap-provider-ui.ps1 open --provider chromatic --repo Prekzursil/Reframe --headed
```

## Command reference

List supported providers and default paths:

```powershell
pwsh -File scripts/provider_ui/bootstrap-provider-ui.ps1 list
```

Bootstrap manual login for a provider:

```powershell
pwsh -File scripts/provider_ui/bootstrap-provider-ui.ps1 bootstrap --provider sentry --headed
```

Inspect using the saved session without a visible browser:

```powershell
pwsh -File scripts/provider_ui/bootstrap-provider-ui.ps1 inspect --provider sonarcloud --headless
```

Keep the browser open after navigation:

```powershell
pwsh -File scripts/provider_ui/bootstrap-provider-ui.ps1 open --provider applitools --headed --keep-open
```

## Notes

- The wrapper does **not** store tokens, runtime artifacts, or provider exports in the repo.
- The Node script only opens provider pages and prints observed metadata; provider-specific actions remain explicit operator work.
- For providers with stable repo deep links, the script can use `--repo owner/repo` to target the repo page directly. For the others it opens the provider dashboard and leaves the final check to the operator.
