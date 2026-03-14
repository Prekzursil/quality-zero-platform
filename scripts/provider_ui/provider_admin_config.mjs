import os from 'node:os';
import path from 'node:path';

export const PROVIDERS = Object.freeze({
  codecov: Object.freeze({
    key: 'codecov',
    label: 'Codecov',
    homeUrl: 'https://app.codecov.io/gh',
    supportsRepoTarget: true,
    repoUrl: (slug) => `https://app.codecov.io/gh/${slug}`,
    loginHint: 'Sign in with GitHub and confirm the repository binding plus default branch.'
  }),
  qlty: Object.freeze({
    key: 'qlty',
    label: 'Qlty',
    homeUrl: 'https://app.qlty.sh/projects',
    supportsRepoTarget: false,
    loginHint: 'Open the Projects dashboard and verify the GitHub binding, default branch, and quality-gate or coverage statuses.'
  }),
  chromatic: Object.freeze({
    key: 'chromatic',
    label: 'Chromatic',
    homeUrl: 'https://www.chromatic.com/apps',
    supportsRepoTarget: false,
    loginHint: 'Confirm the GitHub app binding and project token for the target repository.'
  }),
  applitools: Object.freeze({
    key: 'applitools',
    label: 'Applitools',
    homeUrl: 'https://eyes.applitools.com/app/manager',
    supportsRepoTarget: false,
    loginHint: 'Confirm the project exists, the API key works, and the expected batch or baseline settings are available.'
  }),
  sonarcloud: Object.freeze({
    key: 'sonarcloud',
    label: 'SonarCloud',
    homeUrl: 'https://sonarcloud.io/projects',
    supportsRepoTarget: false,
    loginHint: 'Verify the project binding, organization, and required GitHub checks.'
  }),
  codacy: Object.freeze({
    key: 'codacy',
    label: 'Codacy',
    homeUrl: 'https://app.codacy.com/organizations/gh/Prekzursil/repositories-config',
    supportsRepoTarget: false,
    loginHint: 'Verify the repository appears under the GitHub organization and the expected status checks are enabled.'
  }),
  deepscan: Object.freeze({
    key: 'deepscan',
    label: 'DeepScan',
    homeUrl: 'https://deepscan.io/projects',
    supportsRepoTarget: false,
    loginHint: 'Verify the repository project exists and that the GitHub check integration is healthy.'
  }),
  sentry: Object.freeze({
    key: 'sentry',
    label: 'Sentry',
    homeUrl: 'https://sentry.io/organizations/',
    supportsRepoTarget: false,
    loginHint: 'Verify the organization access, target project, and GitHub integration settings.'
  })
});

export const PROVIDER_KEYS = Object.freeze(Object.keys(PROVIDERS));

export function getDefaultStateRoot(env = process.env) {
  const explicit = env.QUALITY_ZERO_PROVIDER_UI_HOME;
  if (explicit) {
    return path.resolve(explicit);
  }

  const localAppData = env.LOCALAPPDATA;
  if (localAppData) {
    return path.join(localAppData, 'quality-zero-platform', 'provider-ui');
  }

  return path.join(os.homedir(), '.quality-zero-platform', 'provider-ui');
}

export function getDefaultProfileDir(env = process.env) {
  return path.join(getDefaultStateRoot(env), 'chromium-profile');
}

export function getDefaultRunnerDir(env = process.env) {
  return path.join(getDefaultStateRoot(env), 'pw-runner');
}

export function getRunnerDirForStateRoot(stateRoot) {
  return path.join(stateRoot, 'pw-runner');
}

export function normalizeProvider(provider) {
  if (!provider) {
    throw new Error(`Provider is required. Supported values: ${PROVIDER_KEYS.join(', ')}`);
  }

  const normalized = provider.trim().toLowerCase();
  if (!PROVIDERS[normalized]) {
    throw new Error(`Unknown provider '${provider}'. Supported values: ${PROVIDER_KEYS.join(', ')}`);
  }

  return normalized;
}

export function normalizeRepoSlug(repo, owner = 'Prekzursil') {
  if (!repo) {
    return null;
  }

  const trimmed = repo.trim();
  if (!trimmed) {
    return null;
  }

  if (trimmed.includes('/')) {
    return trimmed;
  }

  return `${owner}/${trimmed}`;
}

export function resolveProviderTarget(provider, { repo = null, owner = 'Prekzursil' } = {}) {
  const providerKey = normalizeProvider(provider);
  const definition = PROVIDERS[providerKey];
  const repoSlug = normalizeRepoSlug(repo, owner);
  const targetUrl = definition.supportsRepoTarget && repoSlug ? definition.repoUrl(repoSlug) : definition.homeUrl;

  return {
    ...definition,
    repoSlug,
    targetUrl
  };
}

function shiftRequiredValue(tokens, option) {
  const value = tokens.shift();
  if (value === undefined) {
    throw new Error(`Missing value for ${option}`);
  }
  return value;
}


export function parseArgs(argv) {
  const args = {
    command: 'help',
    provider: null,
    repo: null,
    owner: 'Prekzursil',
    stateRoot: getDefaultStateRoot(),
    profileDir: null,
    timeoutMs: 90000,
    headless: null,
    keepOpen: false,
    slowMoMs: 0
  };

  const tokens = [...argv];
  if (tokens.length > 0 && !tokens[0].startsWith('-')) {
    args.command = tokens.shift();
  }

  const optionHandlers = {
    '--provider': () => { args.provider = shiftRequiredValue(tokens, '--provider'); },
    '-p': () => { args.provider = shiftRequiredValue(tokens, '-p'); },
    '--repo': () => { args.repo = shiftRequiredValue(tokens, '--repo'); },
    '-r': () => { args.repo = shiftRequiredValue(tokens, '-r'); },
    '--owner': () => { args.owner = shiftRequiredValue(tokens, '--owner'); },
    '--state-root': () => { args.stateRoot = path.resolve(shiftRequiredValue(tokens, '--state-root')); },
    '--profile-dir': () => { args.profileDir = path.resolve(shiftRequiredValue(tokens, '--profile-dir')); },
    '--timeout-ms': () => { args.timeoutMs = Number(shiftRequiredValue(tokens, '--timeout-ms')); },
    '--headless': () => { args.headless = true; },
    '--headed': () => { args.headless = false; },
    '--keep-open': () => { args.keepOpen = true; },
    '--slow-mo-ms': () => { args.slowMoMs = Number(shiftRequiredValue(tokens, '--slow-mo-ms')); },
    '--help': () => { args.command = 'help'; },
    '-h': () => { args.command = 'help'; }
  };

  while (tokens.length > 0) {
    const token = tokens.shift();
    const handler = optionHandlers[token];
    if (!handler) {
      throw new Error(`Unknown argument: ${token}`);
    }
    handler();
  }

  if (!Number.isFinite(args.timeoutMs) || args.timeoutMs <= 0) {
    throw new Error(`Invalid --timeout-ms value: ${args.timeoutMs}`);
  }

  if (!Number.isFinite(args.slowMoMs) || args.slowMoMs < 0) {
    throw new Error(`Invalid --slow-mo-ms value: ${args.slowMoMs}`);
  }

  if (!args.profileDir) {
    args.profileDir = path.join(args.stateRoot, 'chromium-profile');
  }

  return args;
}

export function renderHelp() {
  return `provider-admin-bootstrap

Commands:
  list                              List supported providers and default paths.
  bootstrap --provider <name>       Launch headed Chromium with the persistent profile and wait for manual login confirmation.
  open --provider <name>            Reuse the persistent profile and open the provider page. Defaults to headless for repeat runs.
  inspect --provider <name>         Reuse the profile, navigate, and print the observed page metadata.

Options:
  --repo, -r <owner/repo|repo>      Optional repository slug for providers that support deep links.
  --owner <owner>                   Default owner when --repo is passed without an owner. Default: Prekzursil.
  --state-root <path>               Override the external state root. Default: ${getDefaultStateRoot()}.
  --profile-dir <path>              Override the persistent Chromium user-data dir.
  --timeout-ms <ms>                 Navigation timeout. Default: 90000.
  --headless / --headed             Force browser mode for open/inspect.
  --keep-open                       Keep the browser open after navigation.
  --slow-mo-ms <ms>                 Optional Playwright slowMo for interactive sessions.

Environment:
  QUALITY_ZERO_PROVIDER_UI_HOME     Override the external state root.
`;
}
