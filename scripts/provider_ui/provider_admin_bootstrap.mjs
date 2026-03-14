import fs from 'node:fs/promises';
import path from 'node:path';
import readline from 'node:readline/promises';
import { pathToFileURL } from 'node:url';
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

async function ensureDirectory(targetPath) {
  await fs.mkdir(targetPath, { recursive: true });
}

async function loadPlaywrightChromium() {
  const runnerDir = process.env.QUALITY_ZERO_PROVIDER_UI_RUNNER_DIR;
  if (!runnerDir) {
    throw new Error('QUALITY_ZERO_PROVIDER_UI_RUNNER_DIR is required so the bootstrap can load Playwright from the external runner directory.');
  }

  const runnerRequire = createRequire(path.join(runnerDir, 'package.json'));
  const playwrightEntry = runnerRequire.resolve('playwright');
  const playwrightModule = await import(pathToFileURL(playwrightEntry).href);
  return playwrightModule.chromium;
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

async function launchPersistentContext(args, { headlessDefault, includeManualPrompt }) {
  const target = resolveProviderTarget(args.provider, { repo: args.repo, owner: args.owner });
  await ensureDirectory(path.dirname(args.profileDir));
  await ensureDirectory(args.profileDir);

  const headless = args.headless ?? headlessDefault;
  const chromium = await loadPlaywrightChromium();
  let context;
  try {
    context = await chromium.launchPersistentContext(args.profileDir, {
      channel: process.platform === 'win32' ? 'msedge' : undefined,
      headless,
      slowMo: args.slowMoMs,
      viewport: { width: 1440, height: 960 }
    });
  } catch (error) {
    if (process.platform !== 'win32') {
      throw error;
    }

    context = await chromium.launchPersistentContext(args.profileDir, {
      headless,
      slowMo: args.slowMoMs,
      viewport: { width: 1440, height: 960 }
    });
  }

  const page = context.pages()[0] ?? await context.newPage();
  page.setDefaultTimeout(args.timeoutMs);
  await page.goto(target.targetUrl, { waitUntil: 'domcontentloaded', timeout: args.timeoutMs });

  if (includeManualPrompt) {
    await promptForManualLogin(target, args.profileDir);
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

main().catch((error) => {
  console.error(error instanceof Error ? error.stack ?? error.message : error);
  process.exitCode = 1;
});
