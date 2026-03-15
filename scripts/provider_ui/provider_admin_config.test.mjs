import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {
  PROVIDER_KEYS,
  getDefaultProfileDir,
  getDefaultRunnerDir,
  getDefaultStateRoot,
  normalizeRepoSlug,
  parseArgs,
  resolveProviderTarget
} from './provider_admin_config.mjs';

test('supports the full provider set for strict-zero admin work', () => {
  assert.deepEqual(PROVIDER_KEYS, [
    'codecov',
    'qlty',
    'chromatic',
    'applitools',
    'sonarcloud',
    'codacy',
    'deepscan',
    'sentry'
  ]);
});

test('default state locations stay outside the repository tree', () => {
  const env = { LOCALAPPDATA: String.raw`C:\Users\Prekzursil\AppData\Local` };
  const stateRoot = getDefaultStateRoot(env);
  assert.equal(stateRoot, path.join(env.LOCALAPPDATA, 'quality-zero-platform', 'provider-ui'));
  assert.equal(getDefaultProfileDir(env), path.join(stateRoot, 'chromium-profile'));
  assert.equal(getDefaultRunnerDir(env), path.join(stateRoot, 'pw-runner'));
});

test('repo slug normalization supports ownerless input', () => {
  assert.equal(normalizeRepoSlug('Reframe'), 'Prekzursil/Reframe');
  assert.equal(normalizeRepoSlug('OpenAI/demo', 'IgnoredOwner'), 'OpenAI/demo');
  assert.equal(normalizeRepoSlug('', 'Prekzursil'), null);
});

test('provider targets can derive repo-specific URLs when supported', () => {
  const codecov = resolveProviderTarget('Codecov', { repo: 'quality-zero-platform' });
  assert.equal(codecov.repoSlug, 'Prekzursil/quality-zero-platform');
  assert.equal(codecov.targetUrl, 'https://app.codecov.io/gh/Prekzursil/quality-zero-platform');

  const qlty = resolveProviderTarget('qlty', { repo: 'quality-zero-platform' });
  assert.equal(qlty.targetUrl, 'https://app.qlty.sh/projects');
});

test('argument parsing defaults to external persistent profile paths', () => {
  const args = parseArgs(['bootstrap', '--provider', 'chromatic', '--repo', 'WebCoder']);
  assert.equal(args.command, 'bootstrap');
  assert.equal(args.provider, 'chromatic');
  assert.equal(args.repo, 'WebCoder');
  assert.match(args.profileDir, /quality-zero-platform[\\/]provider-ui[\\/]chromium-profile$/);
  assert.equal(args.headless, null);
  assert.equal(args.timeoutMs, 90000);
});
