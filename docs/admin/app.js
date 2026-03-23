async function loadDashboard() {
  const response = await fetch('data/dashboard.json');
  if (!response.ok) {
    throw new Error(`Failed to load dashboard data: ${response.status}`);
  }
  return response.json();
}

function badge(label) {
  const value = (label || 'unknown').toLowerCase();
  return `<span class="badge ${value}">${label || 'unknown'}</span>`;
}

function renderSummary(payload) {
  const repos = payload.repos || [];
  const passing = repos.filter(repo => repo.default_branch_health === 'success').length;
  const failing = repos.filter(repo => repo.default_branch_health === 'partial').length;
  document.getElementById('summary').innerHTML = `
    <article class="summary-card"><h3>Generated</h3><div class="value">${payload.generated_at}</div></article>
    <article class="summary-card"><h3>Repos</h3><div class="value">${payload.repo_count}</div></article>
    <article class="summary-card"><h3>Main green</h3><div class="value">${passing}</div></article>
    <article class="summary-card"><h3>Main partial</h3><div class="value">${failing}</div></article>
  `;
}

function renderTable(payload) {
  const filter = document.getElementById('searchInput').value.toLowerCase();
  const repos = (payload.repos || []).filter(repo => repo.slug.toLowerCase().includes(filter));
  const tbody = document.getElementById('repoRows');
  tbody.innerHTML = repos.map(repo => `
    <tr data-slug="${repo.slug}">
      <td>${repo.slug}</td>
      <td>${repo.profile}</td>
      <td>${repo.rollout}</td>
      <td>${repo.issue_policy_mode}${repo.issue_policy_baseline_ref ? ` (${repo.issue_policy_baseline_ref})` : ''}</td>
      <td>${(repo.enabled_scanners || []).join(', ')}</td>
      <td>${repo.branch_min_percent ?? 'disabled'}</td>
      <td>${repo.deps_policy || 'disabled'}</td>
      <td>${badge(repo.default_branch_health)}</td>
      <td>${badge(repo.open_pr_health)}</td>
      <td>${repo.ruleset_present ? 'yes' : 'no'}</td>
    </tr>
  `).join('');

  Array.from(tbody.querySelectorAll('tr')).forEach((row, index) => {
    row.addEventListener('click', () => openDetail(repos[index]));
  });
}

function list(items = []) {
  if (!items.length) return '<p class="small">None</p>';
  return `<ul>${items.map(item => `<li>${item}</li>`).join('')}</ul>`;
}

function openDetail(repo) {
  document.getElementById('detailTitle').textContent = repo.slug;
  document.getElementById('detailContent').innerHTML = `
    <p class="small">Issue policy: <code>${repo.issue_policy_mode}</code>${repo.issue_policy_baseline_ref ? ` · baseline <code>${repo.issue_policy_baseline_ref}</code>` : ''}</p>
    <p class="small">Enabled scanners: ${(repo.enabled_scanners || []).join(', ') || 'None'}</p>
    <p class="small">Branch coverage minimum: <code>${repo.branch_min_percent ?? 'disabled'}</code></p>
    <p class="small">Dependency policy: <code>${repo.deps_policy || 'disabled'}</code></p>
    <p class="small">Main branch: ${badge(repo.default_branch_health)} · Open PRs: ${badge(repo.open_pr_health)} · Ruleset: <code>${repo.ruleset_present ? 'present' : 'missing'}</code></p>
  `;
  document.getElementById('detailPanel').classList.remove('hidden');
}

loadDashboard().then(payload => {
  renderSummary(payload);
  renderTable(payload);
  document.getElementById('searchInput').addEventListener('input', () => renderTable(payload));
  document.getElementById('closeDetail').addEventListener('click', () => {
    document.getElementById('detailPanel').classList.add('hidden');
  });
}).catch(error => {
  document.body.innerHTML = `<main class="topbar"><div><h1>Quality Zero Admin</h1><p class="small">${error.message}</p></div></main>`;
});
