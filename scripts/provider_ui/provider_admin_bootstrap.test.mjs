import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import {
  bootstrap,
  ensureManagedStatePath,
  isCliEntrypoint,
  launchContextWithFallback,
  launchPersistentContext,
  listProviders,
  loadPlaywrightChromium,
  main,
  openOrInspect,
  printResult,
  promptForManualLogin,
  reportCliError,
  resolvePlaywrightChromium,
  runCliIfEntrypoint,
  runCommand,
  waitForKeepOpenExit
} from './provider_admin_bootstrap.mjs';

// =============================================================================
// ensureManagedStatePath
// =============================================================================

test('ensureManagedStatePath returns absolute path under the state root', () => {
  const root = path.resolve('/tmp/state');
  const target = path.join(root, 'profile');
  const got = ensureManagedStatePath(target, root);
  assert.equal(got, path.resolve(target));
});

test('ensureManagedStatePath rejects sibling escape', () => {
  const root = path.resolve('/tmp/state');
  assert.throws(() => ensureManagedStatePath('/tmp/elsewhere', root), /managed state root/);
});

test('ensureManagedStatePath rejects parent traversal', () => {
  const root = path.resolve('/tmp/state');
  assert.throws(
    () => ensureManagedStatePath(path.join(root, '..', 'evil'), root),
    /managed state root/
  );
});

// =============================================================================
// resolvePlaywrightChromium
// =============================================================================

test('resolvePlaywrightChromium picks up the named export first', () => {
  const stub = { chromium: { tag: 'top-level' } };
  assert.equal(resolvePlaywrightChromium(stub), stub.chromium);
});

test('resolvePlaywrightChromium falls back to default export', () => {
  const stub = { default: { chromium: { tag: 'default' } } };
  assert.equal(resolvePlaywrightChromium(stub), stub.default.chromium);
});

test('resolvePlaywrightChromium throws when no chromium export is present', () => {
  assert.throws(() => resolvePlaywrightChromium({}), /does not expose chromium/);
});

test('resolvePlaywrightChromium throws on null module', () => {
  assert.throws(() => resolvePlaywrightChromium(null), /does not expose chromium/);
});

// =============================================================================
// loadPlaywrightChromium
// =============================================================================

test('loadPlaywrightChromium throws when QUALITY_ZERO_PROVIDER_UI_RUNNER_DIR is unset', async () => {
  const original = process.env.QUALITY_ZERO_PROVIDER_UI_RUNNER_DIR;
  delete process.env.QUALITY_ZERO_PROVIDER_UI_RUNNER_DIR;
  try {
    await assert.rejects(loadPlaywrightChromium(), /QUALITY_ZERO_PROVIDER_UI_RUNNER_DIR/);
  } finally {
    if (original !== undefined) {
      process.env.QUALITY_ZERO_PROVIDER_UI_RUNNER_DIR = original;
    }
  }
});

// =============================================================================
// isCliEntrypoint
// =============================================================================

test('isCliEntrypoint matches when argv[1] resolves to the import URL', () => {
  const target = path.resolve('/tmp/some-script.mjs');
  const url = pathToFileURL(target).href;
  assert.equal(isCliEntrypoint(url, ['node', target]), true);
});

test('isCliEntrypoint rejects when argv[1] is a different file', () => {
  const target = path.resolve('/tmp/script.mjs');
  const otherUrl = pathToFileURL(path.resolve('/tmp/other.mjs')).href;
  assert.equal(isCliEntrypoint(otherUrl, ['node', target]), false);
});

test('isCliEntrypoint returns false when argv[1] is missing', () => {
  const url = pathToFileURL(path.resolve('/tmp/script.mjs')).href;
  assert.equal(isCliEntrypoint(url, ['node']), false);
});

// =============================================================================
// promptForManualLogin
// =============================================================================

test('promptForManualLogin writes guidance and waits for Enter', async () => {
  const writes = [];
  const outputStream = { write: (line) => writes.push(line) };
  const inputStream = {};
  let questionAsked = '';
  let closed = false;
  const readlineModule = {
    createInterface: () => ({
      question: async (prompt) => { questionAsked = prompt; return ''; },
      close: () => { closed = true; }
    })
  };

  await promptForManualLogin(
    { label: 'Codecov', targetUrl: 'https://example.com', loginHint: 'Press something.' },
    '/tmp/profile',
    { readline: readlineModule, input: inputStream, output: outputStream }
  );

  assert.equal(closed, true);
  assert.match(writes.join(''), /Manual login handoff for Codecov/);
  assert.match(writes.join(''), /https:\/\/example\.com/);
  assert.match(writes.join(''), /\/tmp\/profile/);
  assert.match(writes.join(''), /Press something/);
  assert.match(questionAsked, /press Enter to continue/);
});

// =============================================================================
// launchContextWithFallback
// =============================================================================

test('launchContextWithFallback uses msedge channel on win32', async () => {
  const calls = [];
  const chromium = {
    launchPersistentContext: async (dir, options) => {
      calls.push({ dir, options });
      return { ctx: 'edge' };
    }
  };
  const ctx = await launchContextWithFallback(chromium, '/tmp/profile', { headless: true }, 'win32');
  assert.deepEqual(ctx, { ctx: 'edge' });
  assert.equal(calls[0].options.channel, 'msedge');
  assert.equal(calls[0].options.headless, true);
});

test('launchContextWithFallback skips channel on non-win32', async () => {
  const calls = [];
  const chromium = {
    launchPersistentContext: async (dir, options) => {
      calls.push({ dir, options });
      return { ctx: 'chromium' };
    }
  };
  await launchContextWithFallback(chromium, '/tmp/profile', { headless: true }, 'linux');
  assert.equal(calls[0].options.channel, undefined);
});

test('launchContextWithFallback retries without channel on win32 launch failure', async () => {
  const calls = [];
  const chromium = {
    launchPersistentContext: async (dir, options) => {
      calls.push({ options });
      if (options.channel === 'msedge') {
        throw new Error('edge missing');
      }
      return { ctx: 'fallback-chromium' };
    }
  };
  const ctx = await launchContextWithFallback(chromium, '/tmp/profile', { headless: true }, 'win32');
  assert.deepEqual(ctx, { ctx: 'fallback-chromium' });
  assert.equal(calls.length, 2);
  assert.equal(calls[0].options.channel, 'msedge');
  assert.equal(calls[1].options.channel, undefined);
});

test('launchContextWithFallback re-throws on non-win32 launch failure', async () => {
  const chromium = {
    launchPersistentContext: async () => { throw new Error('boom'); }
  };
  await assert.rejects(
    launchContextWithFallback(chromium, '/tmp/profile', { headless: true }, 'linux'),
    /boom/
  );
});

// =============================================================================
// launchPersistentContext
// =============================================================================

function _stubArgs() {
  return {
    provider: 'chromatic',
    repo: 'org/repo',
    owner: 'org',
    profileDir: path.resolve('/tmp/state/profile'),
    stateRoot: path.resolve('/tmp/state'),
    headless: undefined,
    slowMoMs: 0,
    timeoutMs: 1000
  };
}

function _stubBrowserContext() {
  const page = {
    setDefaultTimeout: () => {},
    goto: async () => {},
    title: async () => 'Test Title',
    url: () => 'https://final.example.com'
  };
  return {
    pages: () => [page],
    newPage: async () => page,
    on: () => {},
    once: () => {},
    off: () => {},
    close: async () => {}
  };
}

test('launchPersistentContext walks the launch path and skips manual prompt when configured', async () => {
  const calls = [];
  const ctx = _stubBrowserContext();
  const result = await launchPersistentContext(
    _stubArgs(),
    { headlessDefault: true, includeManualPrompt: false },
    {
      loadPlaywrightChromium: async () => ({ tag: 'chromium' }),
      launchContextWithFallback: async (chromium, dir, opts) => {
        calls.push({ dir, headless: opts.headless });
        return ctx;
      },
      promptForManualLogin: async () => { calls.push('prompt'); }
    }
  );
  assert.equal(result.headless, true);
  assert.equal(result.title, 'Test Title');
  assert.equal(result.finalUrl, 'https://final.example.com');
  assert.deepEqual(calls.map((c) => c.dir ?? c), [path.resolve('/tmp/state/profile')]);
});

test('launchPersistentContext invokes manual prompt when requested', async () => {
  const ctx = _stubBrowserContext();
  let prompted = false;
  await launchPersistentContext(
    _stubArgs(),
    { headlessDefault: false, includeManualPrompt: true },
    {
      loadPlaywrightChromium: async () => ({}),
      launchContextWithFallback: async () => ctx,
      promptForManualLogin: async () => { prompted = true; }
    }
  );
  assert.equal(prompted, true);
});

test('launchPersistentContext creates a new page when context.pages() is empty', async () => {
  const page = {
    setDefaultTimeout: () => {},
    goto: async () => {},
    title: async () => 'New Page',
    url: () => 'https://new.example.com'
  };
  const ctx = {
    pages: () => [],
    newPage: async () => page,
    on: () => {}, once: () => {}, off: () => {}, close: async () => {}
  };
  const result = await launchPersistentContext(
    _stubArgs(),
    { headlessDefault: false, includeManualPrompt: false },
    {
      loadPlaywrightChromium: async () => ({}),
      launchContextWithFallback: async () => ctx,
      promptForManualLogin: async () => {}
    }
  );
  assert.equal(result.title, 'New Page');
});

// =============================================================================
// printResult / listProviders
// =============================================================================

test('printResult emits prefixed JSON via injected logger', () => {
  const logged = [];
  printResult(
    'bootstrap_complete',
    {
      target: { key: 'codecov', label: 'Codecov', repoSlug: 'org/repo', targetUrl: 'https://x' },
      finalUrl: 'https://final',
      title: 'Title',
      headless: true
    },
    (msg) => logged.push(msg)
  );
  assert.equal(logged.length, 1);
  assert.match(logged[0], /^bootstrap_complete: \{/);
  assert.match(logged[0], /"provider": "codecov"/);
});

test('listProviders enumerates known providers via injected logger', () => {
  const logged = [];
  listProviders(
    { stateRoot: '/state', profileDir: '/state/profile' },
    (msg) => logged.push(msg)
  );
  assert.equal(logged.length, 1);
  const payload = JSON.parse(logged[0]);
  assert.ok(Array.isArray(payload.providers));
  assert.ok(payload.providers.length >= 1);
  assert.equal(payload.defaultStateRoot, '/state');
});

// =============================================================================
// waitForKeepOpenExit
// =============================================================================

test('waitForKeepOpenExit resolves when context closes', async () => {
  const ctx = new EventEmitter();
  ctx.off = ctx.removeListener.bind(ctx);
  const processObj = new EventEmitter();
  processObj.off = processObj.removeListener.bind(processObj);
  const done = waitForKeepOpenExit(ctx, processObj);
  ctx.emit('close');
  await done;
});

test('waitForKeepOpenExit resolves on SIGINT', async () => {
  const ctx = new EventEmitter();
  ctx.off = ctx.removeListener.bind(ctx);
  const processObj = new EventEmitter();
  processObj.off = processObj.removeListener.bind(processObj);
  const done = waitForKeepOpenExit(ctx, processObj);
  processObj.emit('SIGINT');
  await done;
});

test('waitForKeepOpenExit resolves on SIGTERM', async () => {
  const ctx = new EventEmitter();
  ctx.off = ctx.removeListener.bind(ctx);
  const processObj = new EventEmitter();
  processObj.off = processObj.removeListener.bind(processObj);
  const done = waitForKeepOpenExit(ctx, processObj);
  processObj.emit('SIGTERM');
  await done;
});

// =============================================================================
// bootstrap / openOrInspect
// =============================================================================

function _bootstrapStubResult(close = async () => {}) {
  return {
    target: { key: 'codecov', label: 'Codecov', repoSlug: 'org/repo', targetUrl: 'https://x' },
    finalUrl: 'https://final',
    title: 'Title',
    headless: false,
    context: { close }
  };
}

test('bootstrap closes the context when keepOpen is false', async () => {
  let closed = 0;
  await bootstrap(
    { keepOpen: false },
    {
      launchPersistentContext: async () => _bootstrapStubResult(async () => { closed += 1; }),
      printResult: () => {},
      waitForKeepOpenExit: async () => {}
    }
  );
  assert.equal(closed, 1);
});

test('bootstrap waits for keep-open exit when requested', async () => {
  let waited = false;
  let closed = 0;
  await bootstrap(
    { keepOpen: true },
    {
      launchPersistentContext: async () => _bootstrapStubResult(async () => { closed += 1; }),
      printResult: () => {},
      waitForKeepOpenExit: async () => { waited = true; }
    }
  );
  assert.equal(waited, true);
  assert.equal(closed, 0);
});

test('openOrInspect uses inspect_complete prefix in inspect mode', async () => {
  const prefixes = [];
  await openOrInspect(
    { keepOpen: false },
    { inspectOnly: true },
    {
      launchPersistentContext: async () => _bootstrapStubResult(),
      printResult: (prefix) => prefixes.push(prefix),
      waitForKeepOpenExit: async () => {}
    }
  );
  assert.deepEqual(prefixes, ['inspect_complete']);
});

test('openOrInspect uses open_complete prefix when not inspect-only', async () => {
  const prefixes = [];
  await openOrInspect(
    { keepOpen: false },
    { inspectOnly: false },
    {
      launchPersistentContext: async () => _bootstrapStubResult(),
      printResult: (prefix) => prefixes.push(prefix),
      waitForKeepOpenExit: async () => {}
    }
  );
  assert.deepEqual(prefixes, ['open_complete']);
});

test('openOrInspect waits for keep-open exit when requested', async () => {
  let waited = false;
  await openOrInspect(
    { keepOpen: true },
    { inspectOnly: false },
    {
      launchPersistentContext: async () => _bootstrapStubResult(),
      printResult: () => {},
      waitForKeepOpenExit: async () => { waited = true; }
    }
  );
  assert.equal(waited, true);
});

// =============================================================================
// runCommand exhaustive cases
// =============================================================================

test('runCommand dispatches list to listProviders hook', async () => {
  const calls = [];
  await runCommand(
    { command: 'list' },
    {
      listProviders: (args) => { calls.push(['list', args]); },
      normalizeProvider: () => {},
      bootstrap: () => {},
      openOrInspect: () => {},
      log: () => {},
      renderHelp: () => 'help'
    }
  );
  assert.deepEqual(calls.map((c) => c[0]), ['list']);
});

test('runCommand dispatches open to openOrInspect with inspectOnly=false', async () => {
  const calls = [];
  await runCommand(
    { command: 'open', provider: 'Sonar' },
    {
      listProviders: () => {},
      normalizeProvider: (p) => { calls.push(['normalize', p]); return p.toLowerCase(); },
      bootstrap: () => { calls.push(['bootstrap']); },
      openOrInspect: (_args, opts) => { calls.push(['open', opts.inspectOnly]); },
      log: () => {},
      renderHelp: () => 'help'
    }
  );
  assert.deepEqual(calls, [['normalize', 'Sonar'], ['open', false]]);
});

test('runCommand dispatches inspect to openOrInspect with inspectOnly=true', async () => {
  const calls = [];
  await runCommand(
    { command: 'inspect', provider: 'Codacy' },
    {
      listProviders: () => {},
      normalizeProvider: (p) => p.toLowerCase(),
      bootstrap: () => {},
      openOrInspect: (_args, opts) => { calls.push(['inspect', opts.inspectOnly]); },
      log: () => {},
      renderHelp: () => 'help'
    }
  );
  assert.deepEqual(calls, [['inspect', true]]);
});

async function _runCommandHelpScenario(command) {
  const messages = [];
  await runCommand(
    { command },
    {
      listProviders: () => {},
      normalizeProvider: () => {},
      bootstrap: () => {},
      openOrInspect: () => {},
      log: (m) => messages.push(m),
      renderHelp: () => 'help text'
    }
  );
  return messages;
}

test('runCommand prints help for unrecognised commands', async () => {
  assert.deepEqual(await _runCommandHelpScenario('unknown-command-name'), ['help text']);
});

test('runCommand prints help when command is "help" (covers case fall-through)', async () => {
  assert.deepEqual(await _runCommandHelpScenario('help'), ['help text']);
});

// =============================================================================
// main / reportCliError
// =============================================================================

test('main parses argv and forwards to runCommand', async () => {
  const calls = [];
  await main(
    ['list'],
    {
      parseArgs: (argv) => { calls.push(['parse', argv]); return { command: 'list' }; },
      runCommand: async (args) => { calls.push(['run', args]); }
    }
  );
  assert.deepEqual(calls, [['parse', ['list']], ['run', { command: 'list' }]]);
});

test('reportCliError prints stack for Error and sets exitCode=1', () => {
  const messages = [];
  const fakeProcess = {};
  reportCliError(new Error('boom'), (m) => messages.push(m), fakeProcess);
  assert.equal(fakeProcess.exitCode, 1);
  assert.match(messages[0], /Error: boom/);
});

test('reportCliError prints raw value for non-Error inputs', () => {
  const messages = [];
  const fakeProcess = {};
  reportCliError({ kind: 'opaque' }, (m) => messages.push(m), fakeProcess);
  assert.deepEqual(messages, [{ kind: 'opaque' }]);
});

test('reportCliError falls back to stack=undefined Error string', () => {
  const messages = [];
  const fakeProcess = {};
  const err = new Error('without stack');
  // Force stack to undefined so the ?? fallback to message is exercised.
  Object.defineProperty(err, 'stack', { value: undefined });
  reportCliError(err, (m) => messages.push(m), fakeProcess);
  assert.deepEqual(messages, ['without stack']);
});

// =============================================================================
// runCliIfEntrypoint
// =============================================================================

test('runCliIfEntrypoint short-circuits when not the CLI entrypoint', async () => {
  let mainCalled = false;
  const ran = await runCliIfEntrypoint('file:///not-the-script.mjs', {
    isCliEntrypoint: () => false,
    main: async () => { mainCalled = true; },
    reportCliError: () => {}
  });
  assert.equal(ran, false);
  assert.equal(mainCalled, false);
});

test('runCliIfEntrypoint runs main when called as CLI', async () => {
  let mainCalled = false;
  const ran = await runCliIfEntrypoint('file:///cli.mjs', {
    isCliEntrypoint: () => true,
    main: async () => { mainCalled = true; },
    reportCliError: () => {}
  });
  assert.equal(ran, true);
  assert.equal(mainCalled, true);
});

test('runCliIfEntrypoint forwards errors to reportCliError', async () => {
  const errors = [];
  const ran = await runCliIfEntrypoint('file:///cli.mjs', {
    isCliEntrypoint: () => true,
    main: async () => { throw new Error('boom from main'); },
    reportCliError: (e) => errors.push(e.message)
  });
  assert.equal(ran, true);
  assert.deepEqual(errors, ['boom from main']);
});

// =============================================================================
// loadPlaywrightChromium - happy path with a stubbed runner dir
// =============================================================================

test('loadPlaywrightChromium reads runner dir env and resolves chromium via injected deps', async () => {
  const stubChromium = { tag: 'stub-chromium' };
  const stubPlaywright = { chromium: stubChromium };
  const got = await loadPlaywrightChromium({
    runnerDir: '/tmp/runner',
    createRequire: () => ({
      resolve: (id) => {
        assert.equal(id, 'playwright');
        return '/tmp/runner/node_modules/playwright/index.js';
      }
    }),
    importModule: async (href) => {
      assert.match(href, /\/tmp\/runner\/node_modules\/playwright\/index\.js$/);
      return stubPlaywright;
    }
  });
  assert.equal(got, stubChromium);
});

test('loadPlaywrightChromium throws when runnerDir is empty', async () => {
  await assert.rejects(loadPlaywrightChromium({ runnerDir: '' }), /QUALITY_ZERO_PROVIDER_UI_RUNNER_DIR/);
});

// =============================================================================
// runCommand bootstrap branch (lines 333-335 explicit re-cover)
// =============================================================================

test('runCommand bootstrap branch invokes normalize then bootstrap hooks in order', async () => {
  const order = [];
  await runCommand(
    { command: 'bootstrap', provider: 'codecov' },
    {
      listProviders: () => order.push('list'),
      normalizeProvider: (p) => { order.push(`normalize:${p}`); return p; },
      bootstrap: async (a) => { order.push(`bootstrap:${a.provider}`); },
      openOrInspect: () => order.push('openOrInspect'),
      log: () => {},
      renderHelp: () => 'help'
    }
  );
  assert.deepEqual(order, ['normalize:codecov', 'bootstrap:codecov']);
});
