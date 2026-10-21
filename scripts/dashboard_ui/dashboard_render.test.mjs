// Lane-8 dashboard render smoke: executes the REAL docs/admin/panels.mjs
// renderers against the committed fixture JSONs under a minimal DOM shim and
// asserts all three operator panels render their fixture content. Run by
// scripts/verify (required "Control Plane Verify" check): green here means a
// fixture-only site build renders every panel without any live GitHub source.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, '..', '..');
const panelsUrl = pathToFileURL(path.join(repoRoot, 'docs', 'admin', 'panels.mjs'));
const fixturesDir = path.join(repoRoot, 'scripts', 'quality', 'fixtures', 'dashboard');

const { renderDriftPanel, renderGatesPanel, renderRollupGapsPanel } = await import(panelsUrl.href);

function loadFixture(name) {
  return JSON.parse(fs.readFileSync(path.join(fixturesDir, name), 'utf8'));
}

// Minimal DOM shim: exactly the API panels.mjs touches (createElement,
// className, textContent, href, dataset, append). No browser required.
function shimElement(tagName) {
  return {
    tagName,
    className: '',
    textContent: '',
    href: '',
    dataset: {},
    children: [],
    append(...nodes) {
      for (const node of nodes) {
        if (node && typeof node === 'object') {
          this.children.push(node);
        }
      }
    },
  };
}

const doc = { createElement: shimElement };

function flatten(node, into) {
  if (!node || typeof node !== 'object') {
    return into;
  }
  if (node.textContent) {
    into.push(String(node.textContent));
  }
  for (const child of node.children || []) {
    flatten(child, into);
  }
  return into;
}

function textOf(node) {
  return flatten(node, []).join(' ');
}

function findByText(node, needle) {
  if (!node || typeof node !== 'object') {
    return null;
  }
  if (String(node.textContent) === needle) {
    return node;
  }
  for (const child of node.children || []) {
    const hit = findByText(child, needle);
    if (hit) {
      return hit;
    }
  }
  return null;
}

function badgesIn(node, into = []) {
  if (!node || typeof node !== 'object') {
    return into;
  }
  if (typeof node.className === 'string' && node.className.startsWith('badge ')) {
    into.push(node);
  }
  for (const child of node.children || []) {
    badgesIn(child, into);
  }
  return into;
}

const dashboardFixture = loadFixture('dashboard.fixture.json');
const driftFixture = loadFixture('ruleset-drift.fixture.json');
const gapsFixture = loadFixture('rollup-gaps.fixture.json');

test('gate panel renders every repo row, verdict badges, and staleness from the fixture', () => {
  const panel = renderGatesPanel(doc, dashboardFixture);
  const text = textOf(panel);
  // Question 1 answerable: repo → failing gate is visible.
  assert.match(text, /Prekzursil\/example-full/);
  assert.match(text, /Coverage 100 Gate/);
  assert.match(text, /Dependency Alerts/);
  assert.match(text, /Prekzursil\/example-lean-observe/);
  assert.match(text, /test \(ubuntu-latest, 3\.12\)/);
  // Rollout column carries the observe lean member.
  assert.match(text, /observe/);
  // Stale/404 member shows the reason, never a fabricated verdict.
  assert.match(text, /<private>/);
  assert.match(text, /default-branch head unavailable for main/);
  // Head freshness: short sha (10 chars) + commit date rendered.
  assert.match(text, /9f86d08188/);
  assert.match(text, /2026-10-21T08:30:00Z/);
  // Verdict vocabulary present as badges: fail, pass, pending, pr_only, stale.
  const classes = badgesIn(panel).map(badge => badge.className);
  for (const expected of ['badge fail', 'badge pass', 'badge pending', 'badge pr_only', 'badge stale']) {
    assert.ok(classes.includes(expected), `expected badge ${expected} in ${JSON.stringify(classes)}`);
  }
});

test('drift panel renders summary counts and every drift/missing entry from the fixture', () => {
  const panel = renderDriftPanel(doc, driftFixture);
  const text = textOf(panel);
  // Question 2 answerable: which artifacts drifted and which side is missing.
  assert.match(text, /in_sync 1 · drift 1 · missing 2/);
  assert.match(text, /example-repo\.json/);
  assert.match(text, /renamed-repo\.json/);
  assert.match(text, /committed_only/);
  assert.match(text, /newly-enrolled-repo\.json/);
  assert.match(text, /generated_only/);
  assert.match(text, /steady-repo\.json/);
  const classes = badgesIn(panel).map(badge => badge.className);
  for (const expected of ['badge drift', 'badge missing', 'badge in_sync']) {
    assert.ok(classes.includes(expected), `expected badge ${expected} in ${JSON.stringify(classes)}`);
  }
});

test('rollup-gaps panel renders PR rows, gap counts, and stale reasons from the fixture', () => {
  const panel = renderRollupGapsPanel(doc, gapsFixture);
  const text = textOf(panel);
  // Question 3 answerable: which PR lacks the rollup comment.
  assert.match(text, /Prekzursil\/example-full/);
  assert.match(text, /1 of 2 open PRs lack a rollup comment/);
  assert.match(text, /#42/);
  assert.match(text, /Add scanner overlay/);
  assert.match(text, /Fix ratchet baseline drift/);
  // Stale private member degrades to reason, no PR rows invented.
  assert.match(text, /private or unreadable with the build token/);
  // Empty open-PR repo still renders its heading with 0 gaps.
  assert.match(text, /Prekzursil\/example-lean-observe/);
  assert.match(text, /0 of 0 open PRs lack a rollup comment/);
  // PR link href survives the shim (title link → PR url).
  const link = findByText(panel, 'Add scanner overlay');
  assert.ok(link, 'PR title element exists');
  assert.equal(link.href, 'https://github.com/Prekzursil/example-full/pull/42');
  // Gap verdict badge: the lacking PR renders as "missing".
  const classes = badgesIn(panel).map(badge => badge.className);
  assert.ok(classes.includes('badge missing'), `expected badge missing in ${JSON.stringify(classes)}`);
  assert.ok(classes.includes('badge stale'), `expected badge stale in ${JSON.stringify(classes)}`);
});

test('the published page wires all three panels (sections, imports, fetch paths)', () => {
  const indexHtml = fs.readFileSync(path.join(repoRoot, 'docs', 'admin', 'index.html'), 'utf8');
  for (const id of ['gatesPanel', 'driftPanel', 'rollupPanel']) {
    assert.match(indexHtml, new RegExp(`id="${id}"`), `index.html must mount #${id}`);
  }
  const appJs = fs.readFileSync(path.join(repoRoot, 'docs', 'admin', 'app.js'), 'utf8');
  assert.match(appJs, /from '\.\/panels\.mjs'/, 'app.js must import the panel renderers');
  assert.match(appJs, /data\/ruleset-drift\.json/, 'app.js must fetch the drift dataset');
  assert.match(appJs, /data\/rollup-gaps\.json/, 'app.js must fetch the rollup-gaps dataset');
  const builder = fs.readFileSync(
    path.join(repoRoot, 'scripts', 'quality', 'build_admin_dashboard.py'),
    'utf8',
  );
  assert.match(builder, /panels\.mjs/, 'the site build must copy panels.mjs next to app.js');
});

test('panels tolerate missing datasets without throwing (graceful empty render)', () => {
  const gatesEmpty = renderGatesPanel(doc, {});
  assert.match(textOf(gatesEmpty), /Required context verdicts/);
  const driftEmpty = renderDriftPanel(doc, null);
  assert.match(textOf(driftEmpty), /in_sync 0 · drift 0 · missing 0/);
  const gapsEmpty = renderRollupGapsPanel(doc, {});
  assert.match(textOf(gapsEmpty), /No enrolled repos\./);
});
