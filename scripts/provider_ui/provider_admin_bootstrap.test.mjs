import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { resolvePlaywrightChromium } from './provider_admin_bootstrap.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

test('resolvePlaywrightChromium supports ESM wrappers around CommonJS playwright exports', () => {
  const fakeChromium = { launchPersistentContext() {} };
  assert.equal(resolvePlaywrightChromium({ chromium: fakeChromium }), fakeChromium);
  assert.equal(resolvePlaywrightChromium({ default: { chromium: fakeChromium } }), fakeChromium);
});

test('resolvePlaywrightChromium rejects modules without a chromium launcher', () => {
  assert.throws(
    () => resolvePlaywrightChromium({ default: {} }),
    /Playwright module does not expose chromium/
  );
});

test('importing bootstrap helpers does not execute the CLI entrypoint', () => {
  const moduleUrl = pathToFileURL(path.join(__dirname, 'provider_admin_bootstrap.mjs')).href;
  const child = spawnSync(
    process.execPath,
    [
      '--input-type=module',
      '--eval',
      `import ${JSON.stringify(moduleUrl)};`
    ],
    { encoding: 'utf-8' }
  );

  assert.equal(child.status, 0);
  assert.equal(child.stdout.trim(), '');
  assert.equal(child.stderr.trim(), '');
});
