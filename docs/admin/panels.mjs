/**
 * Operator panels for the Quality Zero admin dashboard (Lane 8).
 *
 * Three pure renderers — one per operator question — parameterised over a
 * `doc` implementation so the SAME code runs in the browser (real
 * `document`, mounted by app.js) and in the fixture smoke test
 * (scripts/dashboard_ui/dashboard_render.test.mjs, minimal DOM shim).
 *
 * Data contracts (baked at build time by the publish workflow, static JSON
 * only — nothing privileged is fetched client-side):
 *   gates         — data/dashboard.json  (repos[].gate_status / gates[])
 *   ruleset drift — data/ruleset-drift.json
 *   rollup gaps   — data/rollup-gaps.json
 */

/**
 * Handle safe text.
 */
function safeText(value, fallback = 'unknown') {
  return value === null || value === undefined || value === '' ? fallback : String(value);
}

/**
 * Handle create element.
 */
function createElement(doc, tagName, options = {}) {
  const element = doc.createElement(tagName);
  if (options.className) {
    element.className = options.className;
  }
  if (options.text !== undefined) {
    element.textContent = safeText(options.text, '');
  }
  return element;
}

/**
 * Handle create badge.
 */
function createBadge(doc, label) {
  const value = safeText(label).toLowerCase();
  return createElement(doc, 'span', { className: `badge ${value}`, text: safeText(label) });
}

/**
 * Handle create table.
 */
function createTable(doc, headers, rows) {
  const table = createElement(doc, 'table');
  const headRow = doc.createElement('tr');
  for (const header of headers) {
    const th = doc.createElement('th');
    th.textContent = header;
    headRow.append(th);
  }
  const thead = doc.createElement('thead');
  thead.append(headRow);
  const tbody = doc.createElement('tbody');
  for (const row of rows) {
    tbody.append(row);
  }
  table.append(thead, tbody);
  return table;
}

/**
 * Handle short sha.
 */
function shortSha(sha) {
  const value = safeText(sha, '');
  return value.length > 10 ? value.slice(0, 10) : value || '—';
}

/**
 * Render the gate-status panel (question 1: which repo is failing which gate).
 */
export function renderGatesPanel(doc, payload) {
  const container = createElement(doc, 'div', { className: 'panel-body' });
  const repos = (payload && payload.repos) || [];
  const rows = [];
  for (const repo of repos) {
    const row = doc.createElement('tr');
    row.dataset.slug = safeText(repo.slug, '');
    const repoCell = doc.createElement('td');
    repoCell.append(createElement(doc, 'strong', { text: safeText(repo.slug, '<unknown>') }));
    const gatesCell = doc.createElement('td');
    gatesCell.className = 'gates-list';
    for (const gate of repo.gates || []) {
      const chip = createElement(doc, 'span', { className: 'gate-chip' });
      chip.append(createBadge(doc, gate.status));
      chip.append(createElement(doc, 'code', { text: safeText(gate.context, '?') }));
      gatesCell.append(chip);
    }
    if (!repo.gates || repo.gates.length === 0) {
      gatesCell.append(createElement(doc, 'span', { className: 'muted', text: safeText(repo.gate_note, 'no required contexts measured') }));
    }
    row.append(
      repoCell,
      createElement(doc, 'td', { text: safeText(repo.rollout, '') }),
      createBadgeCell(doc, repo.gate_status),
      createElement(doc, 'td', { className: 'muted', text: `${shortSha(repo.gate_head_sha)} ${safeText(repo.gate_head_date, '')}`.trim() }),
      gatesCell,
    );
    rows.push(row);
  }
  container.append(
    createTable(doc, ['Repo', 'Rollout', 'Gates', 'Head', 'Required context verdicts'], rows),
  );
  return container;
}

/**
 * Handle create badge cell.
 */
function createBadgeCell(doc, label) {
  const cell = doc.createElement('td');
  cell.append(createBadge(doc, label));
  return cell;
}

/**
 * Render the ruleset-drift panel (question 2: where is committed-vs-generated drift).
 */
export function renderDriftPanel(doc, payload) {
  const container = createElement(doc, 'div', { className: 'panel-body' });
  const summary = (payload && payload.summary) || {};
  const header = doc.createElement('p');
  header.append(createBadge(doc, safeText(payload && payload.status, 'unknown')));
  header.append(
    createElement(doc, 'span', {
      className: 'muted',
      text: ` in_sync ${safeText(summary.in_sync, 0)} · drift ${safeText(summary.drift, 0)} · missing ${safeText(summary.missing, 0)}`,
    }),
  );
  container.append(header);
  const entries = (payload && payload.entries) || [];
  const rows = [];
  for (const entry of entries) {
    const row = doc.createElement('tr');
    const linkCell = doc.createElement('td');
    linkCell.append(createElement(doc, 'code', { text: safeText(entry.file, '?') }));
    row.append(
      linkCell,
      createBadgeCell(doc, entry.status),
      createElement(doc, 'td', { text: safeText(entry.side, '') }),
    );
    rows.push(row);
  }
  container.append(createTable(doc, ['Ruleset artifact', 'Status', 'Side'], rows));
  return container;
}

/**
 * Render the rollup-gaps panel (question 3: which PRs lack a rollup comment).
 */
export function renderRollupGapsPanel(doc, payload) {
  const container = createElement(doc, 'div', { className: 'panel-body' });
  const repos = (payload && payload.repos) || [];
  if (repos.length === 0) {
    container.append(createElement(doc, 'p', { className: 'muted', text: 'No enrolled repos.' }));
    return container;
  }
  for (const repo of repos) {
    const block = createElement(doc, 'div', { className: 'rollup-repo' });
    const heading = doc.createElement('p');
    heading.append(createElement(doc, 'strong', { text: safeText(repo.slug, '<unknown>') }));
    if (repo.status === 'stale') {
      heading.append(createBadge(doc, 'stale'));
      heading.append(createElement(doc, 'span', { className: 'muted', text: ` — ${safeText(repo.reason, 'source unavailable')}` }));
      block.append(heading);
      container.append(block);
      continue;
    }
    heading.append(
      createElement(doc, 'span', {
        className: 'muted',
        text: ` — ${safeText(repo.gaps, 0)} of ${safeText(repo.open_count, 0)} open PRs lack a rollup comment${repo.truncated ? ' (list truncated)' : ''}`,
      }),
    );
    block.append(heading);
    const rows = [];
    for (const pr of repo.prs || []) {
      const row = doc.createElement('tr');
      const titleCell = doc.createElement('td');
      if (pr.url) {
        const link = doc.createElement('a');
        link.href = pr.url;
        link.textContent = safeText(pr.title, `#${safeText(pr.number, '?')}`);
        titleCell.append(link);
      } else {
        titleCell.textContent = safeText(pr.title, `#${safeText(pr.number, '?')}`);
      }
      row.append(
        createElement(doc, 'td', { text: `#${safeText(pr.number, '?')}` }),
        titleCell,
        createBadgeCell(doc, pr.rollup_comment === 'no' ? 'missing' : safeText(pr.rollup_comment, 'unknown')),
      );
      rows.push(row);
    }
    block.append(createTable(doc, ['PR', 'Title', 'Rollup comment'], rows));
    container.append(block);
  }
  return container;
}
