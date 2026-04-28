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
export async function loadPlaywrightChromium(deps = {}) {
  const runnerDirEnv = deps.runnerDir ?? process.env.QUALITY_ZERO_PROVIDER_UI_RUNNER_DIR;
  if (!runnerDirEnv) {
    throw new Error(
      'QUALITY_ZERO_PROVIDER_UI_RUNNER_DIR is required so the bootstrap can ' +
      'load Playwright from the external runner directory.'
    );
  }
  const _createRequire = deps.createRequire ?? createRequire;
  const _importModule = deps.importModule ?? ((href) => import(href));
  const runnerRequire = _createRequire(path.join(runnerDirEnv, 'package.json'));
  const playwrightEntry = runnerRequire.resolve('playwright');
  const playwrightModule = await _importModule(pathToFileURL(playwrightEntry).href);
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
export async function promptForManualLogin(target, profileDir, deps = {}) {
  const readlineModule = deps.readline ?? readline;
  const inputStream = deps.input ?? input;
  const outputStream = deps.output ?? output;
  return _promptForManualLoginInner(target, profileDir, readlineModule, inputStream, outputStream);
}

async function _promptForManualLoginInner(target, profileDir, readlineModule, inputStream, outputStream) {
  const rl = readlineModule.createInterface({ input: inputStream, output: outputStream });
  try {
    outputStream.write(`\nManual login handoff for ${target.label}\n`);
    outputStream.write(`Target URL: ${target.targetUrl}\n`);
    outputStream.write(`Persistent profile: ${profileDir}\n`);
    outputStream.write(`${target.loginHint}\n`);
    await rl.question(
      'Complete sign-in or provider checks in the opened browser, then ' +
      'press Enter to continue... '
    );
  } finally {
    rl.close();
  }
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
export async function launchPersistentContext(args, { headlessDefault, includeManualPrompt }, deps = {}) {
  const loadChromium = deps.loadPlaywrightChromium ?? loadPlaywrightChromium;
  const launchCtx = deps.launchContextWithFallback ?? launchContextWithFallback;
  const promptLogin = deps.promptForManualLogin ?? promptForManualLogin;
  const target = resolveProviderTarget(args.provider, {
    repo: args.repo,
    owner: args.owner
  });
  const profileDir = ensureManagedStatePath(args.profileDir, args.stateRoot);

  const headless = args.headless ?? headlessDefault;
  const chromium = await loadChromium();
  const context = await launchCtx(chromium, profileDir, {
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
    await promptLogin(target, profileDir);
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
export async function bootstrap(args, deps = {}) {
  const launchCtx = deps.launchPersistentContext ?? launchPersistentContext;
  const printRes = deps.printResult ?? printResult;
  const waitExit = deps.waitForKeepOpenExit ?? waitForKeepOpenExit;
  const result = await launchCtx(args, {
    headlessDefault: false,
    includeManualPrompt: true
  });
  try {
    printRes('bootstrap_complete', result);
    if (args.keepOpen) {
      await waitExit(result.context);
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
export async function openOrInspect(args, { inspectOnly }, deps = {}) {
  const launchCtx = deps.launchPersistentContext ?? launchPersistentContext;
  const printRes = deps.printResult ?? printResult;
  const waitExit = deps.waitForKeepOpenExit ?? waitForKeepOpenExit;
  const result = await launchCtx(args, {
    headlessDefault: true,
    includeManualPrompt: false
  });
  try {
    printRes(inspectOnly ? 'inspect_complete' : 'open_complete', result);
    if (args.keepOpen) {
      await waitExit(result.context);
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
export async function main(argv = process.argv.slice(2), deps = {}) {
  const parse = deps.parseArgs ?? parseArgs;
  const run = deps.runCommand ?? runCommand;
  const args = parse(argv);
  await run(args);
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
export async function runCliIfEntrypoint(importMetaUrl, deps = {}) {
  const isEntrypoint = deps.isCliEntrypoint ?? isCliEntrypoint;
  const runMain = deps.main ?? main;
  const reportErr = deps.reportCliError ?? reportCliError;
  if (!isEntrypoint(importMetaUrl)) {
    return false;
  }
  try {
    await runMain();
  } catch (error) {
    reportErr(error);
  }
  return true;
}

await runCliIfEntrypoint(import.meta.url);
