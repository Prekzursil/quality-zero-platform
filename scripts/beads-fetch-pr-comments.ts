#!/usr/bin/env npx tsx
/**
 * BEADS PR Comment Fetcher
 *
 * Fetches PR review comments from GitHub for analysis by Claude Code.
 * This script does NOT make AI API calls - it just fetches data.
 *
 * Usage:
 *   npx tsx scripts/beads-fetch-pr-comments.ts
 *   npx tsx scripts/beads-fetch-pr-comments.ts --days 14
 *   npx tsx scripts/beads-fetch-pr-comments.ts --output ./comments.json
 */

import { parseArgs } from "util";
import { writeFileSync, mkdirSync, existsSync } from "fs";
import { dirname } from "path";

// =============================================================================
// Log sanitisation
// =============================================================================
// SonarCloud rule tssecurity:S5145 (log injection) flags any log call whose
// interpolated value can be tainted by network/IO data — even when the value
// is statically a number, the rule's taint analysis follows it conservatively.
// Stripping the C0 control range (\x00-\x1f, which already covers CR/LF/TAB
// and friends) prevents an attacker from smuggling CRLF + a fake log line
// through e.g. a PR title. Objects go through JSON.stringify so we don't
// emit the useless "[object Object]" placeholder.
function sanitizeForLog(value: unknown): string {
  const stringified =
    typeof value === "object" && value !== null
      ? JSON.stringify(value)
      : String(value);
  return stringified.replaceAll(/[\x00-\x1f]/g, " ");
}

// =============================================================================
// CLI Arguments
// =============================================================================

const { values: args } = parseArgs({
  options: {
    days: { type: "string", default: "7" },
    output: { type: "string", default: ".beads/temp/pr-comments.json" },
    help: { type: "boolean", short: "h", default: false },
  },
});

if (args.help) {
  console.log(`
BEADS PR Comment Fetcher - Fetch PR comments for analysis

Usage:
  npx tsx scripts/beads-fetch-pr-comments.ts [options]

Options:
  --days <n>      Number of days to look back (default: 7)
  --output <path> Output file path (default: .beads/temp/pr-comments.json)
  -h, --help      Show this help message

Environment Variables:
  GITHUB_TOKEN    GitHub API token (or use 'gh auth token')
  GITHUB_OWNER    Repository owner (default: from git remote)
  GITHUB_REPO     Repository name (default: from git remote)
`);
  process.exit(0);
}

// =============================================================================
// Types
// =============================================================================

interface PrComment {
  prNumber: number;
  prTitle: string;
  reviewer: string;
  reviewerType: "coderabbit" | "bugbot" | "greptile" | "copilot" | "human" | "unknown";
  body: string;
  filePath?: string;
  url: string;
  createdAt: string;
}

interface OutputData {
  fetchedAt: string;
  period: {
    since: string;
    until: string;
    days: number;
  };
  repository: {
    owner: string;
    repo: string;
  };
  summary: {
    prsFound: number;
    commentsFound: number;
    byReviewerType: Record<string, number>;
  };
  comments: PrComment[];
}

// =============================================================================
// Configuration
// =============================================================================

const KNOWN_REVIEWERS = {
  coderabbit: ["coderabbitai[bot]", "coderabbit[bot]"],
  bugbot: ["cursor-bugbot[bot]", "bugbot[bot]"],
  greptile: ["greptile[bot]", "greptile-bot"],
  copilot: ["github-actions[bot]", "copilot[bot]"],
} as const;

const DAYS = parseInt(args.days || "7", 10);
const OUTPUT_PATH = args.output || ".beads/temp/pr-comments.json";

// =============================================================================
// GitHub API
// =============================================================================

async function getGitHubToken(): Promise<string> {
  if (process.env.GITHUB_TOKEN) {
    return process.env.GITHUB_TOKEN;
  }

  // Try to get from gh CLI
  const { exec } = await import("child_process");
  const { promisify } = await import("util");
  const execAsync = promisify(exec);

  try {
    const { stdout } = await execAsync("gh auth token");
    return stdout.trim();
  } catch {
    throw new Error("No GITHUB_TOKEN found. Set GITHUB_TOKEN or run 'gh auth login'");
  }
}

async function getRepoInfo(): Promise<{ owner: string; repo: string }> {
  if (process.env.GITHUB_OWNER && process.env.GITHUB_REPO) {
    return {
      owner: process.env.GITHUB_OWNER,
      repo: process.env.GITHUB_REPO,
    };
  }

  // Try to get from git remote
  const { exec } = await import("child_process");
  const { promisify } = await import("util");
  const execAsync = promisify(exec);

  try {
    const { stdout } = await execAsync("git remote get-url origin");
    const match = stdout.match(/github\.com[:/]([^/]+)\/([^/.]+)/);
    if (match) {
      return { owner: match[1], repo: match[2] };
    }
  } catch {
    // Fall through to error
  }

  throw new Error("Could not determine repository. Set GITHUB_OWNER and GITHUB_REPO");
}

async function githubFetch<T>(endpoint: string, token: string): Promise<T> {
  const response = await fetch(`https://api.github.com${endpoint}`, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github.v3+json",
      "User-Agent": "metaswarm",
    },
  });

  if (!response.ok) {
    throw new Error(`GitHub API error: ${response.status} ${response.statusText}`);
  }

  return response.json() as Promise<T>;
}

function identifyReviewer(
  username: string
): "coderabbit" | "bugbot" | "greptile" | "copilot" | "human" | "unknown" {
  const lowerUser = username.toLowerCase();

  for (const [type, names] of Object.entries(KNOWN_REVIEWERS)) {
    if (names.some(n => lowerUser.includes(n.toLowerCase().replace("[bot]", "")))) {
      return type as "coderabbit" | "bugbot" | "greptile" | "copilot";
    }
  }

  // Check if it's an unknown bot
  if (lowerUser.includes("[bot]") || lowerUser.includes("-bot")) {
    return "unknown";
  }

  return "human";
}

// =============================================================================
// Main
// =============================================================================

interface GHSearchResult {
  items: Array<{ number: number }>;
}

interface GHPullRequest {
  number: number;
  title: string;
}

interface GHReviewComment {
  id: number;
  user: { login: string } | null;
  body: string;
  path: string;
  html_url: string;
  created_at: string;
}

interface GHIssueComment {
  id: number;
  user: { login: string } | null;
  body: string;
  html_url: string;
  created_at: string;
}

function pushIfReviewerKnown(
  buf: PrComment[],
  byReviewerType: Record<string, number>,
  base: { prNumber: number; prTitle: string },
  c: GHReviewComment | GHIssueComment,
  filePath?: string,
): boolean {
  if (!c.body || c.body.length < 50) {
    return false;
  }
  const reviewerType = identifyReviewer(c.user?.login || "unknown");
  if (reviewerType === "unknown") {
    return false;
  }
  byReviewerType[reviewerType] = (byReviewerType[reviewerType] || 0) + 1;
  buf.push({
    prNumber: base.prNumber,
    prTitle: base.prTitle,
    reviewer: c.user?.login || "unknown",
    reviewerType,
    body: c.body,
    ...(filePath !== undefined ? { filePath } : {}),
    url: c.html_url,
    createdAt: c.created_at,
  });
  return true;
}

async function collectCommentsForPr(
  owner: string,
  repo: string,
  prNumber: number,
  token: string,
  out: PrComment[],
  byReviewerType: Record<string, number>,
): Promise<number> {
  const pr = await githubFetch<GHPullRequest>(
    `/repos/${owner}/${repo}/pulls/${prNumber}`,
    token,
  );
  const reviewComments = await githubFetch<GHReviewComment[]>(
    `/repos/${owner}/${repo}/pulls/${prNumber}/comments`,
    token,
  );
  const issueComments = await githubFetch<GHIssueComment[]>(
    `/repos/${owner}/${repo}/issues/${prNumber}/comments`,
    token,
  );
  const base = { prNumber, prTitle: pr.title };
  let count = 0;
  for (const c of reviewComments) {
    if (pushIfReviewerKnown(out, byReviewerType, base, c, c.path)) {
      count += 1;
    }
  }
  for (const c of issueComments) {
    if (pushIfReviewerKnown(out, byReviewerType, base, c)) {
      count += 1;
    }
  }
  return count;
}

async function searchMergedPrs(
  owner: string,
  repo: string,
  sinceDate: string,
  untilDate: string,
  token: string,
): Promise<GHSearchResult> {
  console.log("Searching for merged PRs...");
  const query = encodeURIComponent(
    `repo:${owner}/${repo} is:pr is:merged merged:${sinceDate}..${untilDate}`,
  );
  const result = await githubFetch<GHSearchResult>(
    `/search/issues?q=${query}&sort=updated&order=desc&per_page=100`,
    token,
  );
  console.log(`Found ${sanitizeForLog(result.items.length)} merged PRs\n`);
  return result;
}

async function main() {
  console.log("BEADS PR Comment Fetcher\n");

  const token = await getGitHubToken();
  const { owner, repo } = await getRepoInfo();

  console.log(`Repository: ${owner}/${repo}`);
  console.log(`Looking back: ${DAYS} days`);
  console.log(`Output: ${OUTPUT_PATH}\n`);

  const since = new Date();
  since.setDate(since.getDate() - DAYS);
  const until = new Date();

  const sinceDate = since.toISOString().split("T")[0];
  const untilDate = until.toISOString().split("T")[0];

  const searchResults = await searchMergedPrs(owner, repo, sinceDate, untilDate, token);

  const allComments: PrComment[] = [];
  const byReviewerType: Record<string, number> = {};

  for (const item of searchResults.items) {
    process.stdout.write(`PR #${item.number}: `);
    const count = await collectCommentsForPr(
      owner,
      repo,
      item.number,
      token,
      allComments,
      byReviewerType,
    );
    console.log(`${count} comments`);
  }

  // Prepare output
  const output: OutputData = {
    fetchedAt: new Date().toISOString(),
    period: {
      since: since.toISOString(),
      until: until.toISOString(),
      days: DAYS,
    },
    repository: { owner, repo },
    summary: {
      prsFound: searchResults.items.length,
      commentsFound: allComments.length,
      byReviewerType,
    },
    comments: allComments,
  };

  // Ensure output directory exists
  const dir = dirname(OUTPUT_PATH);
  if (!existsSync(dir)) {
    mkdirSync(dir, { recursive: true });
  }

  // Write output
  writeFileSync(OUTPUT_PATH, JSON.stringify(output, null, 2));

  console.log(
    `\nFetched ${sanitizeForLog(allComments.length)} comments from ${sanitizeForLog(searchResults.items.length)} PRs`,
  );
  console.log(`\nBy reviewer type:`);
  for (const [type, count] of Object.entries(byReviewerType)) {
    console.log(`  ${type}: ${count}`);
  }
  console.log(`\nOutput written to: ${OUTPUT_PATH}`);
  console.log("\nNext: Run '/self-reflect' to analyze with Claude Code");
}

main().catch(error => {
  console.error("Error:", error.message);
  process.exit(1);
});
