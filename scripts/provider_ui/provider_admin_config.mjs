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
    homeUrl: 'https://qlty.sh/gh',
    supportsRepoTarget: true,
    repoUrl: (slug) => `https://qlty.sh/gh/${slug.replace('/', '/projects/')}`,
    loginHint: 'Open the Projects dashboard and verify the GitHub binding, default branch, and quality-gate or coverage statuses.'
  }),
  chromatic: Object.freeze({
    key: 'chromatic',
    label: 'Chromatic',
    homeUrl: 'https://www.chromatic.com/start',
    supportsRepoTarget: false,
    loginHint: 'Confirm the GitHub app binding and project token for the target repository.'
  }),
  applitools: Object.freeze({
    key: 'applitools',
    label: 'Applitools',
    homeUrl: 'https://auth.applitools.com/users/login',
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
    homeUrl: 'https://deepscan.io/dashboard',
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

function resolveStateBaseDir(env = process.env) {
  return env.LOCALAPPDATA
    ? path.join(env.LOCALAPPDATA, 'quality-zero-platform')
    : path.join(os.homedir(), '.quality-zero-platform');
}

export function getDefaultStateRoot(env = process.env) {
  const explicit = env.QUALITY_ZERO_PROVIDER_UI_HOME;
  return explicit ? path.resolve(explicit) : path.join(resolveStateBaseDir(env), 'provider-ui');
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

function setResolvedPath(args, key, tokens, option) {
  args[key] = path.resolve(shiftRequiredValue(tokens, option));
}

function setNumericValue(args, key, tokens, option) {
  args[key] = Number(shiftRequiredValue(tokens, option));
}

const ARGUMENT_HANDLERS = Object.freeze({
  '--provider': (args, tokens) => {
    args.provider = shiftRequiredValue(tokens, '--provider');
  },
  '-p': (args, tokens) => ARGUMENT_HANDLERS['--provider'](args, tokens),
  '--repo': (args, tokens) => {
    args.repo = shiftRequiredValue(tokens, '--repo');
  },
  '-r': (args, tokens) => ARGUMENT_HANDLERS['--repo'](args, tokens),
  '--owner': (args, tokens) => {
    args.owner = shiftRequiredValue(tokens, '--owner');
  },
  '--state-root': (args, tokens) => setResolvedPath(args, 'stateRoot', tokens, '--state-root'),
  '--profile-dir': (args, tokens) => setResolvedPath(args, 'profileDir', tokens, '--profile-dir'),
  '--timeout-ms': (args, tokens) => setNumericValue(args, 'timeoutMs', tokens, '--timeout-ms'),
  '--headless': (args) => {
    args.headless = true;
  },
  '--headed': (args) => {
    args.headless = false;
  },
  '--keep-open': (args) => {
    args.keepOpen = true;
  },
  '--slow-mo-ms': (args, tokens) => setNumericValue(args, 'slowMoMs', tokens, '--slow-mo-ms'),
  '--help': (args) => {
    args.command = 'help';
  },
  '-h': (args, tokens) => ARGUMENT_HANDLERS['--help'](args, tokens)
});

function applyArgumentToken(args, tokens, token) {
  const handler = ARGUMENT_HANDLERS[token];
  if (!handler) {
    throw new Error(`Unknown argument: ${token}`);
  }
  handler(args, tokens);
}

function validateNumericArgs(args) {
  if (!Number.isFinite(args.timeoutMs) || args.timeoutMs <= 0) {
    throw new Error(`Invalid --timeout-ms value: ${args.timeoutMs}`);
  }

  if (!Number.isFinite(args.slowMoMs) || args.slowMoMs < 0) {
    throw new Error(`Invalid --slow-mo-ms value: ${args.slowMoMs}`);
  }
}

function finalizeArgs(args) {
  validateNumericArgs(args);
  if (!args.profileDir) {
    args.profileDir = path.join(args.stateRoot, 'chromium-profile');
  }
  return args;
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

  while (tokens.length > 0) {
    applyArgumentToken(args, tokens, tokens.shift());
  }

  return finalizeArgs(args);
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

Examples:
  node scripts/provider_ui/provider_admin_bootstrap.mjs list
  node scripts/provider_ui/provider_admin_bootstrap.mjs bootstrap --provider codecov --repo quality-zero-platform
  node scripts/provider_ui/provider_admin_bootstrap.mjs inspect --provider sentry --repo quality-zero-platform --headless
`;
}
