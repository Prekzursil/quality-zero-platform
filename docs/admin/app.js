/**
 * Handle load dashboard.
 */
async function loadDashboard() {
  const response = await fetch('data/dashboard.json');
  if (!response.ok) {
    throw new Error(`Failed to load dashboard data: ${response.status}`);
  }
  return response.json();
}

/**
 * Handle safe text.
 */
function safeText(value, fallback = 'unknown') {
  return value === null || value === undefined || value === '' ? fallback : String(value);
}

/**
 * Handle create element.
 */
function createElement(tagName, options = {}) {
  const element = document.createElement(tagName);
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
function createBadge(label) {
  const value = safeText(label).toLowerCase();
  const badge = createElement('span', { className: `badge ${value}`, text: safeText(label) });
  return badge;
}

/**
 * Handle append summary card.
 */
function appendSummaryCard(container, title, value) {
  const card = createElement('article', { className: 'summary-card' });
  card.append(createElement('h3', { text: title }));
  card.append(createElement('div', { className: 'value', text: value }));
  container.append(card);
}

/**
 * Handle render summary.
 */
function renderSummary(payload) {
  const repos = payload.repos || [];
  const passing = repos.filter(repo => repo.default_branch_health === 'success').length;
  const failing = repos.filter(repo => repo.default_branch_health === 'partial').length;
  const summary = document.getElementById('summary');
  summary.replaceChildren();
  appendSummaryCard(summary, 'Generated', safeText(payload.generated_at, 'n/a'));
  appendSummaryCard(summary, 'Repos', safeText(payload.repo_count, '0'));
  appendSummaryCard(summary, 'Main green', safeText(passing, '0'));
  appendSummaryCard(summary, 'Main partial', safeText(failing, '0'));
}

/**
 * Handle issue policy text.
 */
function issuePolicyText(repo) {
  const baseline = repo.issue_policy_baseline_ref ? ` (${repo.issue_policy_baseline_ref})` : '';
  return `${safeText(repo.issue_policy_mode)}${baseline}`;
}

/**
 * Handle create row cell.
 */
function createRowCell(text) {
  return createElement('td', { text });
}

/**
 * Handle create badge cell.
 */
function createBadgeCell(text) {
  const cell = document.createElement('td');
  cell.append(createBadge(text));
  return cell;
}

/**
 * Handle render table.
 */
function renderTable(payload) {
  const searchInput = document.getElementById('searchInput');
  const filter = safeText(searchInput.value, '').toLowerCase();
  const repos = (payload.repos || []).filter(repo => safeText(repo.slug, '').toLowerCase().includes(filter));
  const tbody = document.getElementById('repoRows');
  tbody.replaceChildren();

  repos.forEach(repo => {
    const row = document.createElement('tr');
    row.dataset.slug = safeText(repo.slug, '');
    row.append(
      createRowCell(repo.slug),
      createRowCell(repo.profile),
      createRowCell(repo.rollout),
      createRowCell(issuePolicyText(repo)),
      createRowCell((repo.enabled_scanners || []).join(', ')),
      createRowCell(repo.branch_min_percent ?? 'disabled'),
      createRowCell(repo.deps_policy || 'disabled'),
      createBadgeCell(repo.default_branch_health),
      createBadgeCell(repo.open_pr_health),
      createRowCell(repo.ruleset_present ? 'yes' : 'no'),
    );
    row.addEventListener('click', () => openDetail(repo));
    tbody.append(row);
  });
}

/**
 * Handle create detail line.
 */
function createDetailLine(label, value) {
  const paragraph = createElement('p', { className: 'small' });
  const prefix = createElement('span', { text: `${label}: ` });
  const code = createElement('code', { text: value });
  paragraph.append(prefix, code);
  return paragraph;
}

/**
 * Handle create detail status line.
 */
function createDetailStatusLine(repo) {
  const paragraph = createElement('p', { className: 'small' });
  paragraph.append(createElement('span', { text: 'Main branch: ' }));
  paragraph.append(createBadge(repo.default_branch_health));
  paragraph.append(createElement('span', { text: ' · Open PRs: ' }));
  paragraph.append(createBadge(repo.open_pr_health));
  paragraph.append(createElement('span', { text: ' · Ruleset: ' }));
  paragraph.append(createElement('code', { text: repo.ruleset_present ? 'present' : 'missing' }));
  return paragraph;
}

/**
 * Handle open detail.
 */
function openDetail(repo) {
  document.getElementById('detailTitle').textContent = safeText(repo.slug, 'Repo detail');
  const detailContent = document.getElementById('detailContent');
  detailContent.replaceChildren(
    createDetailLine('Issue policy', issuePolicyText(repo)),
    createDetailLine('Enabled scanners', (repo.enabled_scanners || []).join(', ') || 'None'),
    createDetailLine('Branch coverage minimum', repo.branch_min_percent ?? 'disabled'),
    createDetailLine('Dependency policy', repo.deps_policy || 'disabled'),
    createDetailStatusLine(repo),
  );
  document.getElementById('detailPanel').classList.remove('hidden');
}

/**
 * Handle render error.
 */
function renderError(message) {
  const body = document.body;
  const main = createElement('main', { className: 'topbar' });
  const container = document.createElement('div');
  container.append(
    createElement('h1', { text: 'Quality Zero Admin' }),
    createElement('p', { className: 'small', text: message }),
  );
  main.append(container);
  body.replaceChildren(main);
}

try {
  const payload = await loadDashboard();
  renderSummary(payload);
  renderTable(payload);
  document.getElementById('searchInput').addEventListener('input', () => renderTable(payload));
  document.getElementById('closeDetail').addEventListener('click', () => {
    document.getElementById('detailPanel').classList.add('hidden');
  });
} catch (error) {
  renderError(error.message);
}
