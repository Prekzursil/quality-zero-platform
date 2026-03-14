import fs from 'node:fs/promises';
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

async function ensureManagedDirectory(targetPath, stateRoot) {
  const managedPath = ensureManagedStatePath(targetPath, stateRoot);
  // eslint-disable-next-line security/detect-non-literal-fs-filename -- managed paths are validated to remain under the external provider-ui state root.
  await fs.mkdir(managedPath, { recursive: true });
  return managedPath;
}

export function ensureManagedStatePath(targetPath, stateRoot) {
  const resolvedPath = path.resolve(targetPath);
  const resolvedRoot = path.resolve(stateRoot);
  const relativePath = path.relative(resolvedRoot, resolvedPath);
  if (relativePath.startsWith('..') || path.isAbsolute(relativePath)) {
    throw new Error(`Provider UI state paths must stay under the managed state root: ${resolvedPath}`);
  }

  return resolvedPath;
}

async function loadPlaywrightChromium() {
  const runnerDir = process.env.QUALITY_ZERO_PROVIDER_UI_RUNNER_DIR;
  if (!runnerDir) {
    throw new Error('QUALITY_ZERO_PROVIDER_UI_RUNNER_DIR is required so the bootstrap can load Playwright from the external runner directory.');
  }

  const runnerRequire = createRequire(path.join(runnerDir, 'package.json'));
  const playwrightEntry = runnerRequire.resolve('playwright');
  const playwrightModule = await import(pathToFileURL(playwrightEntry).href);
  return resolvePlaywrightChromium(playwrightModule);
}

export function resolvePlaywrightChromium(playwrightModule) {
  const candidate = playwrightModule?.chromium ?? playwrightModule?.default?.chromium ?? null;
  if (!candidate) {
    throw new Error('Playwright module does not expose chromium.');
  }

  return candidate;
}

export function isCliEntrypoint(importMetaUrl, argv = process.argv) {
  const entryPath = argv?.[1];
  if (!entryPath) {
    return false;
  }

  const entryUrl = pathToFileURL(path.resolve(entryPath)).href;
  const moduleUrl = pathToFileURL(path.resolve(fileURLToPath(importMetaUrl))).href;
  return entryUrl === moduleUrl;
}

async function promptForManualLogin(target, profileDir) {
  const rl = readline.createInterface({ input, output });
  try {
    output.write(`\nManual login handoff for ${target.label}\n`);
    output.write(`Target URL: ${target.targetUrl}\n`);
    output.write(`Persistent profile: ${profileDir}\n`);
    output.write(`${target.loginHint}\n`);
    await rl.question('Complete sign-in or provider checks in the opened browser, then press Enter to continue... ');
  } finally {
    rl.close();
  }
}

async function launchContextWithFallback(chromium, profileDir, options) {
  try {
    return await chromium.launchPersistentContext(profileDir, {
      channel: process.platform === 'win32' ? 'msedge' : undefined,
      ...options
    });
  } catch (error) {
    if (process.platform !== 'win32') {
      throw error;
    }

    return chromium.launchPersistentContext(profileDir, options);
  }
}

async function launchPersistentContext(args, { headlessDefault, includeManualPrompt }) {
  const target = resolveProviderTarget(args.provider, { repo: args.repo, owner: args.owner });
  const profileDir = await ensureManagedDirectory(args.profileDir, args.stateRoot);
  await ensureManagedDirectory(path.dirname(profileDir), args.stateRoot);

  const headless = args.headless ?? headlessDefault;
  const chromium = await loadPlaywrightChromium();
  const context = await launchContextWithFallback(chromium, profileDir, {
    headless,
    slowMo: args.slowMoMs,
    viewport: { width: 1440, height: 960 }
  });

  const page = context.pages()[0] ?? await context.newPage();
  page.setDefaultTimeout(args.timeoutMs);
  await page.goto(target.targetUrl, { waitUntil: 'domcontentloaded', timeout: args.timeoutMs });

  if (includeManualPrompt) {
    await promptForManualLogin(target, profileDir);
  }

  const title = await page.title();
  const finalUrl = page.url();

  return { context, page, target, title, finalUrl, headless };
}

function printResult(prefix, result) {
  const metadata = {
    provider: result.target.key,
    label: result.target.label,
    repo: result.target.repoSlug,
    targetUrl: result.target.targetUrl,
    finalUrl: result.finalUrl,
    title: result.title,
    headless: result.headless
  };

  console.log(`${prefix}: ${JSON.stringify(metadata, null, 2)}`);
}

async function listProviders(args) {
  const rows = PROVIDER_KEYS.map((key) => {
    const target = resolveProviderTarget(key, {});
    return {
      provider: key,
      label: target.label,
      defaultUrl: target.targetUrl,
      supportsRepoTarget: target.supportsRepoTarget
    };
  });

  console.log(JSON.stringify({
    providers: rows,
    defaultStateRoot: args.stateRoot,
    defaultProfileDir: args.profileDir,
    defaultRunnerDir: getRunnerDirForStateRoot(args.stateRoot)
  }, null, 2));
}

async function bootstrap(args) {
  const result = await launchPersistentContext(args, {
    headlessDefault: false,
    includeManualPrompt: true
  });
  try {
    printResult('bootstrap_complete', result);
    if (args.keepOpen) {
      await new Promise(() => {});
    }
  } finally {
    if (!args.keepOpen) {
      await result.context.close();
    }
  }
}

async function openOrInspect(args, { inspectOnly }) {
  const result = await launchPersistentContext(args, {
    headlessDefault: true,
    includeManualPrompt: false
  });
  try {
    printResult(inspectOnly ? 'inspect_complete' : 'open_complete', result);
    if (args.keepOpen) {
      await new Promise(() => {});
    }
  } finally {
    if (!args.keepOpen) {
      await result.context.close();
    }
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  switch (args.command) {
    case 'list':
      await listProviders(args);
      return;
    case 'bootstrap':
      normalizeProvider(args.provider);
      await bootstrap(args);
      return;
    case 'open':
      normalizeProvider(args.provider);
      await openOrInspect(args, { inspectOnly: false });
      return;
    case 'inspect':
      normalizeProvider(args.provider);
      await openOrInspect(args, { inspectOnly: true });
      return;
    case 'help':
    default:
      console.log(renderHelp());
  }
}

function reportCliError(error) {
  console.error(error instanceof Error ? error.stack ?? error.message : error);
  process.exitCode = 1;
}

async function runCli() {
  await main();
}

if (isCliEntrypoint(import.meta.url)) {
  runCli().catch(reportCliError);
}
