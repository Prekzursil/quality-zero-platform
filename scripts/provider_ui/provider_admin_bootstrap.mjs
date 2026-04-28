import path from 'node:path';
import readline from 'node:readline/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { stdin as input, stdout as output } from 'node:process';
import { createRequire } from 'node:module';
import {
  PROVIDER_KEYS,
  getRunnerDirForStateRoot,
  normalizeProvider,
  parseArgs,
  renderHelp,
  resolveProviderTarget
} from './provider_admin_config.mjs';

/**
 * Mutable test seam for module-level default dependencies. Public API
 * functions that previously used ``deps.foo ?? moduleFoo`` fallbacks now
 * use ``deps = _internals`` defaults, eliminating per-``??`` branch
 * coverage gaps. Tests can swap entries in ``_internals`` to exercise the
 * no-deps default path; production code never mutates it.
 */
const _internals = {};

/**
 * Ensure provider UI state stays under the managed state root.
 * @param {string} targetPath
 * @param {string} stateRoot
 * @returns {string}
 */
export function ensureManagedStatePath(targetPath, stateRoot) {
  const resolvedPath = path.resolve(targetPath);
  const resolvedRoot = path.resolve(stateRoot);
  const relativePath = path.relative(resolvedRoot, resolvedPath);
  if (relativePath.startsWith('..') || path.isAbsolute(relativePath)) {
    throw new Error(
      `Provider UI state paths must stay under the managed state root: ${resolvedPath}`
    );
  }

  return resolvedPath;
}

/**
 * Load Playwright from the managed external runner directory.
 * @returns {Promise<import('playwright').BrowserType>}
 */
export async function loadPlaywrightChromium(deps = _internals) {
  const runnerDirEnv = deps.runnerDir ?? process.env.QUALITY_ZERO_PROVIDER_UI_RUNNER_DIR;
  if (!runnerDirEnv) {
    throw new Error(
      'QUALITY_ZERO_PROVIDER_UI_RUNNER_DIR is required so the bootstrap can ' +
      'load Playwright from the external runner directory.'
    );
  }
  const runnerRequire = deps.createRequire(path.join(runnerDirEnv, 'package.json'));
  const playwrightEntry = runnerRequire.resolve('playwright');
  const playwrightModule = await deps.importModule(pathToFileURL(playwrightEntry).href);
  return resolvePlaywrightChromium(playwrightModule);
}

/**
 * Resolve the Chromium browser type from the imported Playwright module.
 * @param {unknown} playwrightModule
 * @returns {import('playwright').BrowserType}
 */
export function resolvePlaywrightChromium(playwrightModule) {
  const candidate = playwrightModule?.chromium ?? playwrightModule?.default?.chromium ?? null;
  if (!candidate) {
    throw new Error('Playwright module does not expose chromium.');
  }

  return candidate;
}

/**
 * Detect whether the module is running as the CLI entrypoint.
 * @param {string} importMetaUrl
 * @param {string[]} [argv]
 * @returns {boolean}
 */
export function isCliEntrypoint(importMetaUrl, argv = process.argv) {
  const entryPath = argv?.[1];
  return Boolean(entryPath) && (
    pathToFileURL(path.resolve(entryPath)).href ===
    pathToFileURL(path.resolve(fileURLToPath(importMetaUrl))).href
  );
}

/**
 * Prompt the operator to complete a manual provider login.
 * @param {{label: string, targetUrl: string, loginHint: string}} target
 * @param {string} profileDir
 * @returns {Promise<void>}
 */
export function promptForManualLogin(target, profileDir, deps = _internals) {
  return _promptForManualLoginInner(target, profileDir, deps.readline, deps.input, deps.output);
}

function _promptForManualLoginInner(target, profileDir, readlineModule, inputStream, outputStream) {
  const rl = readlineModule.createInterface({ input: inputStream, output: outputStream });
  outputStream.write(`\nManual login handoff for ${target.label}\n`);
  outputStream.write(`Target URL: ${target.targetUrl}\n`);
  outputStream.write(`Persistent profile: ${profileDir}\n`);
  outputStream.write(`${target.loginHint}\n`);
  return rl.question(
    'Complete sign-in or provider checks in the opened browser, then ' +
    'press Enter to continue... '
  ).finally(() => rl.close());
}

/**
 * Launch a persistent browser context, falling back from Edge on Windows.
 * @param {import('playwright').BrowserType} chromium
 * @param {string} profileDir
 * @param {import('playwright').LaunchPersistentContextOptions} options
 * @returns {Promise<import('playwright').BrowserContext>}
 */
export async function launchContextWithFallback(chromium, profileDir, options, platform = process.platform) {
  try {
    return await chromium.launchPersistentContext(profileDir, {
      channel: platform === 'win32' ? 'msedge' : undefined,
      ...options
    });
  } catch (error) {
    if (platform !== 'win32') {
      throw error;
    }

    return chromium.launchPersistentContext(profileDir, options);
  }
}

/**
 * Launch the provider page with the requested runtime options.
 * @param {ReturnType<typeof parseArgs>} args
 * @param {{headlessDefault: boolean, includeManualPrompt: boolean}} options
 * @returns {Promise<{
 *   context: import('playwright').BrowserContext,
 *   page: import('playwright').Page,
 *   target: ReturnType<typeof resolveProviderTarget>,
 *   title: string,
 *   finalUrl: string,
 *   headless: boolean
 * }>}
 */
export async function launchPersistentContext(args, { headlessDefault, includeManualPrompt }, deps = _internals) {
  const target = resolveProviderTarget(args.provider, {
    repo: args.repo,
    owner: args.owner
  });
  const profileDir = ensureManagedStatePath(args.profileDir, args.stateRoot);

  const headless = args.headless ?? headlessDefault;
  const chromium = await deps.loadPlaywrightChromium();
  const context = await deps.launchContextWithFallback(chromium, profileDir, {
    headless,
    slowMo: args.slowMoMs,
    viewport: { width: 1440, height: 960 }
  });

  const page = context.pages()[0] ?? await context.newPage();
  page.setDefaultTimeout(args.timeoutMs);
  await page.goto(target.targetUrl, {
    waitUntil: 'domcontentloaded',
    timeout: args.timeoutMs
  });

  if (includeManualPrompt) {
    await deps.promptForManualLogin(target, profileDir);
  }

  const title = await page.title();
  const finalUrl = page.url();

  return { context, page, target, title, finalUrl, headless };
}

/**
 * Print one machine-readable CLI result payload.
 * @param {string} prefix
 * @param {{
 *   target: {key: string, label: string, repoSlug: string, targetUrl: string},
 *   finalUrl: string,
 *   title: string,
 *   headless: boolean
 * }} result
 * @returns {void}
 */
export function printResult(prefix, result, log = console.log) {
  const metadata = {
    provider: result.target.key,
    label: result.target.label,
    repo: result.target.repoSlug,
    targetUrl: result.target.targetUrl,
    finalUrl: result.finalUrl,
    title: result.title,
    headless: result.headless
  };

  // skipcq: JS-0002 - this Node CLI intentionally emits structured JSON for automation.
  log(`${prefix}: ${JSON.stringify(metadata, null, 2)}`);
}

/**
 * Print the known provider targets and default runner locations.
 * @param {{stateRoot: string, profileDir: string}} args
 * @returns {void}
 */
export function listProviders(args, log = console.log) {
  const rows = PROVIDER_KEYS.map((key) => {
    const target = resolveProviderTarget(key, {});
    return {
      provider: key,
      label: target.label,
      defaultUrl: target.targetUrl,
      supportsRepoTarget: target.supportsRepoTarget
    };
  });

  // skipcq: JS-0002 - this Node CLI intentionally emits structured JSON for automation.
  log(
    JSON.stringify(
      {
        providers: rows,
        defaultStateRoot: args.stateRoot,
        defaultProfileDir: args.profileDir,
        defaultRunnerDir: getRunnerDirForStateRoot(args.stateRoot)
      },
      null,
      2
    )
  );
}

/**
 * Keep the browser session open until the context closes or a signal arrives.
 * @param {import('playwright').BrowserContext} context
 * @returns {Promise<void>}
 */
export function waitForKeepOpenExit(context, processObj = process) {
  return new Promise((resolve) => {
    /**
     * Detach signal handlers and resolve the keep-open wait.
     * @returns {void}
     */
    function finish() {
      processObj.off('SIGINT', finish);
      processObj.off('SIGTERM', finish);
      context.off('close', finish);
      resolve();
    }

    processObj.once('SIGINT', finish);
    processObj.once('SIGTERM', finish);
    context.once('close', finish);
  });
}

/**
 * Bootstrap a provider session and optionally keep it open for inspection.
 * @param {ReturnType<typeof parseArgs>} args
 * @returns {Promise<void>}
 */
export async function bootstrap(args, deps = _internals) {
  const result = await deps.launchPersistentContext(args, {
    headlessDefault: false,
    includeManualPrompt: true
  });
  try {
    deps.printResult('bootstrap_complete', result);
    if (args.keepOpen) {
      await deps.waitForKeepOpenExit(result.context);
    }
  } finally {
    if (!args.keepOpen) {
      await result.context.close();
    }
  }
}

/**
 * Open or inspect a provider page without the manual prompt.
 * @param {ReturnType<typeof parseArgs>} args
 * @param {{inspectOnly: boolean}} options
 * @returns {Promise<void>}
 */
export async function openOrInspect(args, { inspectOnly }, deps = _internals) {
  const result = await deps.launchPersistentContext(args, {
    headlessDefault: true,
    includeManualPrompt: false
  });
  try {
    deps.printResult(inspectOnly ? 'inspect_complete' : 'open_complete', result);
    if (args.keepOpen) {
      await deps.waitForKeepOpenExit(result.context);
    }
  } finally {
    if (!args.keepOpen) {
      await result.context.close();
    }
  }
}

/**
 * Dispatch one provider admin command.
 * @param {ReturnType<typeof parseArgs>} args
 * @param {Record<string, unknown>} [hooks]
 * @returns {Promise<void>}
 */
export async function runCommand(args, hooks = {}) {
  const {
    listProviders: listProvidersHook = listProviders,
    normalizeProvider: normalizeProviderHook = normalizeProvider,
    bootstrap: bootstrapHook = bootstrap,
    openOrInspect: openOrInspectHook = openOrInspect,
    log: logHook = console.log,
    renderHelp: renderHelpHook = renderHelp
  } = hooks;

  switch (args.command) {
    case 'list':
      await listProvidersHook(args);
      return;
    case 'bootstrap':
      normalizeProviderHook(args.provider);
      await bootstrapHook(args);
      return;
    case 'open':
      normalizeProviderHook(args.provider);
      await openOrInspectHook(args, { inspectOnly: false });
      return;
    case 'inspect':
      normalizeProviderHook(args.provider);
      await openOrInspectHook(args, { inspectOnly: true });
      return;
    case 'help':
    default:
      logHook(renderHelpHook());
  }
}

/**
 * Parse CLI arguments and run the requested provider command.
 * @returns {Promise<void>}
 */
export async function main(argv = process.argv.slice(2), deps = _internals) {
  const args = deps.parseArgs(argv);
  await deps.runCommand(args);
}

/**
 * Report a CLI failure without throwing past the top-level handler.
 * @param {unknown} error
 * @returns {void}
 */
export function reportCliError(error, errLog = console.error, processObj = process) {
  // skipcq: JS-0002 - this Node CLI writes failures to stderr for callers and CI logs.
  errLog(error instanceof Error ? error.stack ?? error.message : error);
  processObj.exitCode = 1;
}

/**
 * Entry-point guard extracted so tests can drive both branches without
 * spawning a child process. ``runCliIfEntrypoint`` is no-op when the
 * module is imported by tests; runs ``main()`` and forwards any throw
 * through ``reportCliError`` when invoked as the CLI script.
 * @param {string} importMetaUrl - the calling module's ``import.meta.url``.
 * @param {object} [deps]
 * @returns {Promise<boolean>} ``true`` if the CLI ran, ``false`` otherwise.
 */
export async function runCliIfEntrypoint(importMetaUrl, deps = _internals) {
  if (!deps.isCliEntrypoint(importMetaUrl)) {
    return false;
  }
  try {
    await deps.main();
  } catch (error) {
    deps.reportCliError(error);
  }
  return true;
}

// Populate the test seam after every function is hoisted so the default
// parameter expressions ``deps = _internals`` resolve to the production
// implementations at call time. Tests can mutate entries on this object
// to drive the no-deps branch of each public function.
Object.assign(_internals, {
  createRequire,
  importModule: (href) => import(href),
  readline,
  input,
  output,
  loadPlaywrightChromium,
  launchContextWithFallback,
  promptForManualLogin,
  launchPersistentContext,
  printResult,
  waitForKeepOpenExit,
  parseArgs,
  runCommand,
  isCliEntrypoint,
  main,
  reportCliError
});

export { _internals };

// Bootstrap entrypoint: kicks off main() when invoked as a CLI.
// ``runCliIfEntrypoint``'s internal try/catch routes failures to
// ``reportCliError``, so the resolved boolean (true=ran-as-CLI,
// false=imported-as-module) is intentionally unused here. Wrapping
// the call in an async IIFE makes the top-level statement a regular
// function call (which all ESLint configs accept as a valid
// expression statement) rather than a bare ``await`` expression.
// Inside the IIFE the await is in an async function body — Sonar's
// javascript:S7785 ("prefer top-level await") doesn't fire on
// async-function bodies, only on ``.then()`` chains at the top
// scope. Resolves the inline-disable-comment asymmetry between
// Codacy's PR-scope and main-scope ESLint analyzers.
(async () => {  // NOSONAR javascript:S7785 — IIFE form needed for Codacy ESLint expr (Sonar/Codacy linter conflict, see reference_sonar_codacy_top_level_await_conflict)
  await runCliIfEntrypoint(import.meta.url);
})();
