import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {
  PROVIDER_KEYS,
  buildRepoTargetUrl,
  getDefaultProfileDir,
  getDefaultRunnerDir,
  getDefaultStateRoot,
  normalizeProvider,
  normalizeRepoSlug,
  parseArgs,
  renderHelp,
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
  assert.equal(qlty.targetUrl, 'https://qlty.sh/gh/Prekzursil/projects/quality-zero-platform');

  const chromatic = resolveProviderTarget('chromatic', { repo: 'momentstudio' });
  assert.equal(chromatic.targetUrl, 'https://www.chromatic.com/start');
});

test('provider home URLs stay aligned with live provider entrypoints', () => {
  assert.equal(resolveProviderTarget('deepscan').targetUrl, 'https://deepscan.io/dashboard');
  assert.equal(resolveProviderTarget('applitools').targetUrl, 'https://auth.applitools.com/users/login');
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


test('argument parsing accepts short aliases without changing command semantics', () => {
  const args = parseArgs(['inspect', '-p', 'codacy', '-r', 'quality-zero-platform', '-h']);
  assert.equal(args.command, 'help');
  assert.equal(args.provider, 'codacy');
  assert.equal(args.repo, 'quality-zero-platform');
  assert.equal(args.headless, null);
});

test('getDefaultStateRoot honours QUALITY_ZERO_PROVIDER_UI_HOME override', () => {
  const env = { QUALITY_ZERO_PROVIDER_UI_HOME: '/custom/home' };
  const root = getDefaultStateRoot(env);
  assert.equal(root, path.resolve('/custom/home'));
  assert.equal(getDefaultProfileDir(env), path.join(path.resolve('/custom/home'), 'chromium-profile'));
  assert.equal(getDefaultRunnerDir(env), path.join(path.resolve('/custom/home'), 'pw-runner'));
});

test('getDefaultStateRoot falls back to homedir when LOCALAPPDATA is missing', () => {
  const root = getDefaultStateRoot({});
  // Should land under either the home directory or a non-empty path
  assert.ok(root.length > 0);
  assert.ok(root.endsWith('provider-ui'));
});

test('normalizeProvider rejects empty input', () => {
  assert.throws(() => normalizeProvider(''), /Provider is required/);
});

test('normalizeProvider rejects unknown providers', () => {
  assert.throws(() => normalizeProvider('not-a-provider'), /Unknown provider/);
});

test('normalizeProvider trims and lowercases recognised providers', () => {
  assert.equal(normalizeProvider('  Codecov  '), 'codecov');
  assert.equal(normalizeProvider('Sentry'), 'sentry');
});

test('resolveProviderTarget defaults to provider home when no repo is supplied', () => {
  const target = resolveProviderTarget('codecov');
  assert.equal(target.repoSlug, null);
  // Codecov home page when no repo is supplied
  assert.match(target.targetUrl, /codecov\.io/);
});

test('resolveProviderTarget rejects an unknown provider key', () => {
  assert.throws(() => resolveProviderTarget('totally-unknown'), /Unknown provider/);
});

test('parseArgs throws on an unknown long flag', () => {
  assert.throws(() => parseArgs(['list', '--bogus-flag']), /Unknown argument/);
});

test('parseArgs --headless toggle flips the headless option', () => {
  const headless = parseArgs(['open', '-p', 'codecov', '--headless']);
  assert.equal(headless.headless, true);
  const headed = parseArgs(['open', '-p', 'codecov', '--headed']);
  assert.equal(headed.headless, false);
});

test('parseArgs --keep-open turns on keepOpen', () => {
  const args = parseArgs(['inspect', '-p', 'codecov', '--keep-open']);
  assert.equal(args.keepOpen, true);
});

test('parseArgs --slow-mo-ms applies as numeric', () => {
  const args = parseArgs(['open', '-p', 'codecov', '--slow-mo-ms', '50']);
  assert.equal(args.slowMoMs, 50);
});

test('parseArgs --state-root + --profile-dir resolve to absolute paths', () => {
  const args = parseArgs([
    'open',
    '-p',
    'codecov',
    '--state-root',
    '/tmp/state',
    '--profile-dir',
    '/tmp/profile'
  ]);
  assert.equal(args.stateRoot, path.resolve('/tmp/state'));
  assert.equal(args.profileDir, path.resolve('/tmp/profile'));
});

test('parseArgs rejects non-positive --timeout-ms', () => {
  assert.throws(() => parseArgs(['inspect', '-p', 'codecov', '--timeout-ms', '0']), /Invalid --timeout-ms/);
});

test('parseArgs rejects negative --slow-mo-ms', () => {
  assert.throws(() => parseArgs(['inspect', '-p', 'codecov', '--slow-mo-ms', '-5']), /Invalid --slow-mo-ms/);
});

test('parseArgs requires a value for --provider', () => {
  assert.throws(() => parseArgs(['bootstrap', '--provider']), /Missing value for/);
});

test('parseArgs --owner overrides default owner', () => {
  const args = parseArgs(['inspect', '-p', 'codecov', '--owner', 'someone-else']);
  assert.equal(args.owner, 'someone-else');
});

test('renderHelp returns a help string with Commands / Options sections', () => {
  const help = renderHelp();
  assert.match(help, /Commands:/);
  assert.match(help, /Options:/);
  assert.match(help, /Examples:/);
  assert.match(help, /list\s+List supported providers/);
});

test('normalizeRepoSlug handles slug-only with a fallback owner', () => {
  assert.equal(normalizeRepoSlug('repo-only', 'fallback-owner'), 'fallback-owner/repo-only');
  assert.equal(normalizeRepoSlug(null, 'fallback'), null);
  assert.equal(normalizeRepoSlug(undefined, 'fallback'), null);
});

test('normalizeRepoSlug returns null when input trims to empty', () => {
  assert.equal(normalizeRepoSlug('   ', 'fallback'), null);
});

test('resolveProviderTarget with repo on a provider that does not support repo URLs falls back to home', () => {
  // chromatic does not derive a per-repo URL — the buildRepoTargetUrl
  // default branch (return null) routes back to the provider home page.
  const target = resolveProviderTarget('chromatic', { repo: 'momentstudio' });
  assert.equal(target.targetUrl, 'https://www.chromatic.com/start');
  assert.equal(target.repoSlug, 'Prekzursil/momentstudio');
});

test('buildRepoTargetUrl returns codecov / qlty URLs and null otherwise', () => {
  assert.equal(
    buildRepoTargetUrl('codecov', 'Prekzursil/quality-zero-platform'),
    'https://app.codecov.io/gh/Prekzursil/quality-zero-platform'
  );
  assert.equal(
    buildRepoTargetUrl('qlty', 'Prekzursil/quality-zero-platform'),
    'https://qlty.sh/gh/Prekzursil/projects/quality-zero-platform'
  );
  // Unknown provider key — covers the default: return null branch
  assert.equal(buildRepoTargetUrl('unknown-future-provider', 'org/repo'), null);
});
