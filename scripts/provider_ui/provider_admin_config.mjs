import os from 'node:os';
import path from 'node:path';

export const PROVIDERS = Object.freeze({
  codecov: Object.freeze({
    key: 'codecov',
    label: 'Codecov',
    homeUrl: 'https://app.codecov.io/gh',
    supportsRepoTarget: true,
    loginHint: 'Sign in with GitHub and confirm the repository binding plus default branch.'
  }),
  qlty: Object.freeze({
    key: 'qlty',
    label: 'Qlty',
    homeUrl: 'https://qlty.sh/gh',
    supportsRepoTarget: true,
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

/**
 * Build a provider deep-link for repository-aware providers.
 *
 * @param {string} providerKey
 * @param {string} repoSlug
 * @returns {string | null}
 */
function buildRepoTargetUrl(providerKey, repoSlug) {
  switch (providerKey) {
    case 'codecov':
      return `https://app.codecov.io/gh/${repoSlug}`;
    case 'qlty':
      return `https://qlty.sh/gh/${repoSlug.replace('/', '/projects/')}`;
    default:
      return null;
  }
}

/**
 * Resolve the parent directory for persisted provider UI state.
 *
 * @param {NodeJS.ProcessEnv} [env]
 * @returns {string}
 */
function resolveStateBaseDir(env = process.env) {
  return env.LOCALAPPDATA
    ? path.join(env.LOCALAPPDATA, 'quality-zero-platform')
    : path.join(os.homedir(), '.quality-zero-platform');
}

/**
 * Return the default root used for persisted provider UI state.
 *
 * @param {NodeJS.ProcessEnv} [env]
 * @returns {string}
 */
export function getDefaultStateRoot(env = process.env) {
  const explicit = env.QUALITY_ZERO_PROVIDER_UI_HOME;
  return explicit ? path.resolve(explicit) : path.join(resolveStateBaseDir(env), 'provider-ui');
}

/**
 * Return the default Chromium profile directory for provider UI sessions.
 *
 * @param {NodeJS.ProcessEnv} [env]
 * @returns {string}
 */
export function getDefaultProfileDir(env = process.env) {
  return path.join(getDefaultStateRoot(env), 'chromium-profile');
}

/**
 * Return the default Playwright runner directory for provider UI sessions.
 *
 * @param {NodeJS.ProcessEnv} [env]
 * @returns {string}
 */
export function getDefaultRunnerDir(env = process.env) {
  return path.join(getDefaultStateRoot(env), 'pw-runner');
}

/**
 * Return the Playwright runner directory for an explicit state root.
 *
 * @param {string} stateRoot
 * @returns {string}
 */
export function getRunnerDirForStateRoot(stateRoot) {
  return path.join(stateRoot, 'pw-runner');
}

/**
 * Normalize a provider identifier to the canonical key.
 *
 * @param {string} provider
 * @returns {string}
 */
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

/**
 * Normalize a repository slug, applying the default owner when needed.
 *
 * @param {string | null} repo
 * @param {string} [owner]
 * @returns {string | null}
 */
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

/**
 * Resolve provider metadata and the appropriate landing URL.
 *
 * @param {string} provider
 * @param {{ repo?: string | null, owner?: string }} [options]
 * @returns {{ key: string, label: string, homeUrl: string, supportsRepoTarget: boolean, loginHint: string, repoSlug: string | null, targetUrl: string | null }}
 */
export function resolveProviderTarget(provider, { repo = null, owner = 'Prekzursil' } = {}) {
  const providerKey = normalizeProvider(provider);
  const definition = PROVIDERS[providerKey];
  const repoSlug = normalizeRepoSlug(repo, owner);
  const canResolveRepoTarget = definition.supportsRepoTarget && repoSlug !== null;
  const targetUrl = canResolveRepoTarget
    ? buildRepoTargetUrl(providerKey, repoSlug)
    : definition.homeUrl;

  return {
    ...definition,
    repoSlug,
    targetUrl
  };
}

/**
 * Shift the next argument token or raise when the option is incomplete.
 *
 * @param {string[]} tokens
 * @param {string} option
 * @returns {string}
 */
function shiftRequiredValue(tokens, option) {
  const value = tokens.shift();
  if (value === undefined) {
    throw new Error(`Missing value for ${option}`);
  }
  return value;
}

const STRING_ARGUMENTS = Object.freeze({
  '--provider': ['provider', '--provider'],
  '-p': ['provider', '--provider'],
  '--repo': ['repo', '--repo'],
  '-r': ['repo', '--repo'],
  '--owner': ['owner', '--owner']
});

const PATH_ARGUMENTS = Object.freeze({
  '--state-root': ['stateRoot', '--state-root'],
  '--profile-dir': ['profileDir', '--profile-dir']
});

const NUMERIC_ARGUMENTS = Object.freeze({
  '--timeout-ms': ['timeoutMs', '--timeout-ms'],
  '--slow-mo-ms': ['slowMoMs', '--slow-mo-ms']
});

const TOGGLE_ARGUMENTS = Object.freeze({
  '--headless': ['headless', true],
  '--headed': ['headless', false],
  '--keep-open': ['keepOpen', true]
});

const HELP_ARGUMENTS = Object.freeze(new Set(['--help', '-h']));

/**
 * Apply a string-valued argument handler when the token matches.
 *
 * @param {Record<string, unknown>} args
 * @param {string[]} tokens
 * @param {string} token
 * @returns {boolean}
 */
function applyStringValueArgument(args, tokens, token) {
  const handler = STRING_ARGUMENTS[token];
  if (!handler) {
    return false;
  }
  const [key, option] = handler;
  args[key] = shiftRequiredValue(tokens, option);
  return true;
}

/**
 * Apply a path-valued argument handler when the token matches.
 *
 * @param {Record<string, unknown>} args
 * @param {string[]} tokens
 * @param {string} token
 * @returns {boolean}
 */
function applyPathValueArgument(args, tokens, token) {
  const handler = PATH_ARGUMENTS[token];
  if (!handler) {
    return false;
  }
  const [key, option] = handler;
  args[key] = path.resolve(shiftRequiredValue(tokens, option));
  return true;
}

/**
 * Apply a numeric argument handler when the token matches.
 *
 * @param {Record<string, unknown>} args
 * @param {string[]} tokens
 * @param {string} token
 * @returns {boolean}
 */
function applyNumericValueArgument(args, tokens, token) {
  const handler = NUMERIC_ARGUMENTS[token];
  if (!handler) {
    return false;
  }
  const [key, option] = handler;
  args[key] = Number(shiftRequiredValue(tokens, option));
  return true;
}

/**
 * Apply any value-bearing argument handler that matches the token.
 *
 * @param {Record<string, unknown>} args
 * @param {string[]} tokens
 * @param {string} token
 * @returns {boolean}
 */
function applyValueArgument(args, tokens, token) {
  if (applyStringValueArgument(args, tokens, token)) {
    return true;
  }
  if (applyPathValueArgument(args, tokens, token)) {
    return true;
  }
  return applyNumericValueArgument(args, tokens, token);
}

/**
 * Apply a toggle argument handler when the token matches.
 *
 * @param {Record<string, unknown>} args
 * @param {string} token
 * @returns {boolean}
 */
function applyToggleArgument(args, token) {
  const handler = TOGGLE_ARGUMENTS[token];
  if (!handler) {
    return false;
  }
  const [key, value] = handler;
  args[key] = value;
  return true;
}

/**
 * Switch into help mode when the token requests it.
 *
 * @param {Record<string, unknown>} args
 * @param {string} token
 * @returns {boolean}
 */
function applyHelpArgument(args, token) {
  if (!HELP_ARGUMENTS.has(token)) {
    return false;
  }
  args.command = 'help';
  return true;
}

/**
 * Dispatch a single CLI token to the correct parser handler.
 *
 * @param {Record<string, unknown>} args
 * @param {string[]} tokens
 * @param {string} token
 * @returns {void}
 */
function applyArgumentToken(args, tokens, token) {
  if (applyValueArgument(args, tokens, token)) {
    return;
  }
  if (applyToggleArgument(args, token)) {
    return;
  }
  if (applyHelpArgument(args, token)) {
    return;
  }
  throw new Error(`Unknown argument: ${token}`);
}

/**
 * Validate timeout-related numeric arguments.
 *
 * @param {{ timeoutMs: number, slowMoMs: number }} args
 * @returns {void}
 */
function validateNumericArgs(args) {
  if (!Number.isFinite(args.timeoutMs) || args.timeoutMs <= 0) {
    throw new Error(`Invalid --timeout-ms value: ${args.timeoutMs}`);
  }

  if (!Number.isFinite(args.slowMoMs) || args.slowMoMs < 0) {
    throw new Error(`Invalid --slow-mo-ms value: ${args.slowMoMs}`);
  }
}

/**
 * Apply derived defaults after argument parsing completes.
 *
 * @param {Record<string, unknown>} args
 * @returns {Record<string, unknown>}
 */
function finalizeArgs(args) {
  validateNumericArgs(args);
  if (!args.profileDir) {
    args.profileDir = path.join(args.stateRoot, 'chromium-profile');
  }
  return args;
}

/**
 * Parse CLI arguments for the provider admin bootstrap entrypoint.
 *
 * @param {string[]} argv
 * @returns {{ command: string, provider: string | null, repo: string | null, owner: string, stateRoot: string, profileDir: string, timeoutMs: number, headless: boolean | null, keepOpen: boolean, slowMoMs: number }}
 */
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

/**
 * Render the CLI help text for provider admin bootstrap.
 *
 * @returns {string}
 */
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
