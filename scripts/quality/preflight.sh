#!/usr/bin/env bash
# QZP lean-gate LOCAL PREFLIGHT — reproduces every lane of
# .github/workflows/reusable-quality.yml in a CONSUMER repo before pushing,
# with the SAME pinned tool versions (read from .quality/tool-pins.yml, the
# single pin source).
#
# Usage (from the consumer repo's root, pointing at a QZP checkout):
#   bash /path/to/quality-zero-platform/scripts/quality/preflight.sh
#
# Or from inside a QZP checkout with the consumer elsewhere:
#   bash scripts/quality/preflight.sh --repo-dir /path/to/consumer
#
# Lanes mirror CI one-for-one (same guards, same commands, same pinned
# versions):
#   gate 1 py    ruff check + ruff format --check
#   gate 1 jsts  oxlint --deny-warnings + biome ci (formatter-only)
#   gate 2 ts    tsc --noEmit (per tsconfig, like CI)
#   gate 2 py    basedpyright (caller config honored, else --typecheckingmode standard)
#   gate 3 py    pytest --cov --cov-branch --cov-fail-under=100 (or unittest+coverage)
#   gate 3 jsts  caller test:coverage script + jsts_coverage_assert --min-percent
#   gate 3 go/c++ caller's own coverage-gate script (default scripts/coverage-gate.sh)
#   gate 4       opengrep scan --config .quality/opengrep --error
#   gate 5       gitleaks detect --redact (ALWAYS, like CI)
#   gate 6       osv-scanner scan source + the T0 severity-floor classifier
#   charter      .quality/charter_check.sh when the caller ships the charter
#
# The two costliest rules, stated here because they cost the most red runs:
#   * Coverage is STRICT 100% LINE **AND** BRANCH. Line-only 100% (e.g.
#     pytest-cov without --cov-branch) typically sits at ~99.x once branches
#     are counted and still FAILs. This preflight always passes --cov-branch.
#   * The format lane formats CONFIG FILES TOO, including biome.json itself.
#     A hand-edited unformatted biome.json fails gate 1 exactly like source.
#     Self-format with: npx --yes @biomejs/biome@<pin> format --write .
#
# Missing pinned binaries (gitleaks / osv-scanner / opengrep) are downloaded
# into QZP_PREFLIGHT_CACHE (default ~/.cache/qzp-preflight) at the pinned
# version. Set QZP_PREFLIGHT_NO_INSTALL=1 to skip downloads; the lane then
# SKIPs (never silently passes — the skip is printed and listed in the summary).
set -euo pipefail

usage() {
  sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
}

REPO_DIR="$(pwd)"
QZP_NO_INSTALL="${QZP_PREFLIGHT_NO_INSTALL:-0}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo-dir) REPO_DIR="$2"; shift 2 ;;
    --no-install) QZP_NO_INSTALL=1; shift ;;
    -h|--help) usage ;;
    *) echo "preflight: unknown argument: $1 (see --help)" >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QZP_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PINS_FILE="${PINS_FILE:-$QZP_ROOT/.quality/tool-pins.yml}"

fail() { echo "FAIL $1: $2" >&2; exit "${3:-1}"; }

[ -f "$PINS_FILE" ] || fail preflight "pin file not found at $PINS_FILE (set PINS_FILE or run from a QZP checkout)"

# pin <name> — extract `  <name>: "value"` from the pin file.
pin() {
  local value
  value="$(sed -n "s/^  $1: \"\\(.*\\)\"\$/\\1/p" "$PINS_FILE" | head -1)"
  [ -n "$value" ] || fail preflight "pin '$1' missing from $PINS_FILE — add it before running"
  printf '%s' "$value"
}

PIN_RUFF="$(pin ruff)"
PIN_BASEDPYRIGHT="$(pin basedpyright)"
PIN_OXLINT="$(pin oxlint)"
PIN_BIOME="$(pin biome)"
PIN_OPENGREP="$(pin opengrep)"
PIN_GITLEAKS="$(pin gitleaks)"
PIN_OSV="$(pin osv-scanner)"
echo "QZP preflight — pins from $PINS_FILE:"
echo "  ruff=$PIN_RUFF basedpyright=$PIN_BASEDPYRIGHT oxlint=$PIN_OXLINT biome=$PIN_BIOME"
echo "  opengrep=$PIN_OPENGREP gitleaks=$PIN_GITLEAKS osv-scanner=$PIN_OSV"

cd "$REPO_DIR" || fail preflight "cannot cd to --repo-dir $REPO_DIR"

# ── Detection (mirrors the reusable-quality.yml detect step) ────────────────
# No pipefail inside detection helpers: `git ls-files | head -1` can SIGPIPE
# (exit 141) exactly like CI's detection step documents; detection must never
# hard-fail.
set +o pipefail
has() { [ -n "$(git ls-files "$@" 2>/dev/null | head -1)" ] && echo true || echo false; }
any() { [ -n "${1:-}" ] && echo true || echo false; }
file_exists() { [ -f "$1" ] && echo true || echo false; }
osv_sources="$(git ls-files \
  '**/requirements*.txt' requirements*.txt \
  '**/package-lock.json' package-lock.json '**/yarn.lock' yarn.lock \
  '**/pnpm-lock.yaml' pnpm-lock.yaml '**/Cargo.lock' Cargo.lock \
  '**/go.mod' go.mod '**/go.sum' go.sum '**/Gemfile.lock' Gemfile.lock \
  '**/poetry.lock' poetry.lock '**/Pipfile.lock' Pipfile.lock \
  '**/pyproject.toml' pyproject.toml '**/composer.lock' composer.lock \
  '**/pom.xml' pom.xml '**/gradle.lockfile' gradle.lockfile 2>/dev/null | head -1)"
py_cov_cfg="$(git ls-files pyproject.toml setup.cfg tox.ini '.coveragerc' 'pytest.ini' 2>/dev/null | head -1)"
py_pyright_cfg="$(git ls-files pyrightconfig.json basedpyrightconfig.json 2>/dev/null | head -1)"
if [ -z "$py_pyright_cfg" ] && [ -f pyproject.toml ] \
  && grep -Eq '^\[tool\.(based)?pyright\]' pyproject.toml 2>/dev/null; then
  py_pyright_cfg=pyproject.toml
fi
py_tests="$(git ls-files 'test_*.py' '*_test.py' '**/test_*.py' '**/*_test.py' \
  'tests/**' '**/tests/**' conftest.py '**/conftest.py' 2>/dev/null | head -1)"
jsts_tests="$(git ls-files \
  '*.test.ts' '*.test.tsx' '*.test.js' '*.test.jsx' '*.test.mjs' '*.test.cjs' \
  '*.spec.ts' '*.spec.tsx' '*.spec.js' '*.spec.jsx' '*.spec.mjs' '*.spec.cjs' \
  '**/*.test.*' '**/*.spec.*' '__tests__/**' '**/__tests__/**' 2>/dev/null | head -1)"
go_tests="$(git ls-files '*_test.go' '**/*_test.go' 2>/dev/null | head -1)"
cpp_tests="$(git ls-files \
  '*_test.cpp' '**/*_test.cpp' '*_test.cc' '**/*_test.cc' \
  '*_test.cxx' '**/*_test.cxx' '*_tests.cpp' '**/*_tests.cpp' \
  'test_*.cpp' '**/test_*.cpp' '*Test.cpp' '**/*Test.cpp' \
  'tests/**/*.cpp' 'test/**/*.cpp' 'tests/**/*.cc' 'test/**/*.cc' 2>/dev/null | head -1)"
DETECT_PYTHON="$(has '*.py')"
DETECT_TS="$(has '*.ts' '*.tsx')"
DETECT_JSTS="$(has '*.ts' '*.tsx' '*.js' '*.jsx' '*.mjs' '*.cjs')"
DETECT_PYRIGHTCFG="$(any "$py_pyright_cfg")"
DETECT_OPENGREP="$([ -d .quality/opengrep ] && echo true || echo false)"
DETECT_GITLEAKS_ALLOWLIST="$(file_exists .gitleaks.toml)"
DETECT_OSVSOURCES="$(any "$osv_sources")"
DETECT_PYCOVCFG="$(any "$py_cov_cfg")"
DETECT_PYTESTS="$(any "$py_tests")"
DETECT_JSTSTESTS="$(any "$jsts_tests")"
DETECT_GO="$(has '*.go')"
DETECT_GOMOD="$(file_exists go.mod)"
DETECT_GOTESTS="$(any "$go_tests")"
DETECT_CPP="$(has '*.cpp' '*.cc' '*.cxx' '*.hpp' '*.hh' '*.hxx')"
DETECT_CPPTESTS="$(any "$cpp_tests")"
DETECT_CHARTER="$([ -f .quality/charter.yml ] && echo true || echo false)"
set -o pipefail
echo "Detected: python=$DETECT_PYTHON ts=$DETECT_TS jsts=$DETECT_JSTS go=$DETECT_GO cpp=$DETECT_CPP \
opengrep=$DETECT_OPENGREP osvsources=$DETECT_OSVSOURCES charter=$DETECT_CHARTER"

declare -a LANES=()
declare -a RESULTS=()
record() { RESULTS+=("$2"); LANES+=("$1"); }
lane_pass() { record "$1" "PASS $1: $2"; echo "PASS $1: $2"; }
lane_fail() { record "$1" "FAIL $1: $2"; echo "FAIL $1: $2" >&2; }
lane_skip() { record "$1" "SKIP $1: $2"; echo "SKIP $1: $2"; }

# ── Pinned-binary resolution (download-once cache) ─────────────────────────
CACHE_DIR="${QZP_PREFLIGHT_CACHE:-$HOME/.cache/qzp-preflight}"
kernel="$(uname -s | tr '[:upper:]' '[:lower:]')"
machine="$(uname -m)"
case "$kernel" in
  msys_nt*|mingw*|cygwin_nt*) os=windows ;;
  linux*) os=linux ;;
  darwin*) os=darwin ;;
  *) os=unknown ;;
esac
case "$machine" in
  x86_64|amd64) arch=amd64 ;;
  aarch64|arm64) arch=arm64 ;;
  *) arch=unknown ;;
esac

resolve_gitleaks() {
  if command -v gitleaks >/dev/null 2>&1; then command -v gitleaks; return 0; fi
  if [ "$QZP_NO_INSTALL" = "1" ] || [ "$os" = "unknown" ] || { [ "$os" = "windows" ] && [ "$arch" != "amd64" ]; } || { [ "$os" != "windows" ] && [ "$arch" = "unknown" ]; }; then
    return 1
  fi
  local bin="$CACHE_DIR/gitleaks/$PIN_GITLEAKS/gitleaks"
  if [ ! -x "$bin" ] && [ "$os" != "windows" ]; then
    mkdir -p "$CACHE_DIR/gitleaks/$PIN_GITLEAKS"
    curl -fsSL -o "$CACHE_DIR/gitleaks/gitleaks.tgz" \
      "https://github.com/gitleaks/gitleaks/releases/download/v${PIN_GITLEAKS}/gitleaks_${PIN_GITLEAKS}_${os}_${arch}.tar.gz" \
      || return 1
    tar -xzf "$CACHE_DIR/gitleaks/gitleaks.tgz" -C "$CACHE_DIR/gitleaks/$PIN_GITLEAKS" gitleaks || return 1
  fi
  if [ ! -x "$bin" ] && [ "$os" = "windows" ]; then
    mkdir -p "$CACHE_DIR/gitleaks/$PIN_GITLEAKS"
    curl -fsSL -o "$CACHE_DIR/gitleaks/gitleaks.zip" \
      "https://github.com/gitleaks/gitleaks/releases/download/v${PIN_GITLEAKS}/gitleaks_${PIN_GITLEAKS}_windows_x64.zip" \
      || return 1
    unzip -oq "$CACHE_DIR/gitleaks/gitleaks.zip" -d "$CACHE_DIR/gitleaks/$PIN_GITLEAKS" || return 1
  fi
  [ -x "$bin" ] || return 1
  printf '%s' "$bin"
}

resolve_osv() {
  local name="osv-scanner"
  [ "$os" = "windows" ] && name="osv-scanner.exe"
  if command -v osv-scanner >/dev/null 2>&1; then command -v osv-scanner; return 0; fi
  if [ "$QZP_NO_INSTALL" = "1" ] || [ "$os" = "unknown" ] || [ "$arch" = "unknown" ]; then
    return 1
  fi
  local url_name="osv-scanner_${os}_${arch}"
  [ "$os" = "windows" ] && url_name="osv-scanner_windows_amd64.exe"
  local bin="$CACHE_DIR/osv-scanner/$PIN_OSV/$name"
  if [ ! -x "$bin" ]; then
    mkdir -p "$CACHE_DIR/osv-scanner/$PIN_OSV"
    curl -fsSL -o "$bin" \
      "https://github.com/google/osv-scanner/releases/download/${PIN_OSV}/${url_name}" || return 1
    chmod +x "$bin" 2>/dev/null || true
  fi
  [ -x "$bin" ] || return 1
  printf '%s' "$bin"
}

resolve_opengrep() {
  if command -v opengrep >/dev/null 2>&1; then command -v opengrep; return 0; fi
  if [ "$QZP_NO_INSTALL" = "1" ]; then return 1; fi
  case "$os" in linux|darwin) : ;; *) return 1 ;; esac
  local dir="$CACHE_DIR/opengrep/$PIN_OPENGREP"
  if [ ! -x "$dir/bin/opengrep" ]; then
    mkdir -p "$dir"
    ( cd "$dir" && curl -fsSL https://raw.githubusercontent.com/opengrep/opengrep/main/install.sh \
      | bash -s -- -v "$PIN_OPENGREP" ) >/dev/null 2>&1 || return 1
  fi
  [ -x "$dir/bin/opengrep" ] || return 1
  printf '%s' "$dir/bin/opengrep"
}

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

# ── GATE 1 (jsts) — oxlint + biome (self-contained, pinned) ────────────────
if [ "$DETECT_JSTS" = "true" ]; then
  echo "== gate-lint-format-jsts (oxlint@$PIN_OXLINT + biome@$PIN_BIOME) =="
  rc=0
  npx --yes "oxlint@$PIN_OXLINT" --deny-warnings \
    --no-error-on-unmatched-pattern \
    --ignore-pattern '**/node_modules/**' \
    --ignore-pattern '**/dist/**' \
    --ignore-pattern '**/build/**' \
    --ignore-pattern '**/out/**' \
    --ignore-pattern '**/.venv/**' \
    --ignore-pattern '**/vendor/**' \
    --ignore-pattern '**/*.min.js' \
    . || rc=$?
  biome_cfg_args=()
  if [ ! -f biome.json ] && [ ! -f biome.jsonc ]; then
    printf '%s' '{"$schema":"https://biomejs.dev/schemas/2.5.0/schema.json","files":{"includes":["**/*.ts","**/*.tsx","**/*.js","**/*.jsx","**/*.mjs","**/*.cjs","!**/node_modules/**","!**/dist/**","!**/build/**","!**/out/**","!**/.venv/**","!**/vendor/**"]},"formatter":{"enabled":true,"indentStyle":"space","indentWidth":2,"lineWidth":100,"lineEnding":"lf"},"javascript":{"formatter":{"quoteStyle":"single","trailingCommas":"all","semicolons":"always"}},"linter":{"enabled":false},"assist":{"enabled":false}}' \
      > "$WORK_DIR/biome.json"
    biome_cfg_args=(--config-path "$WORK_DIR")
    echo "No caller biome.json/biome.jsonc - using the lean template's bundled biome config."
  else
    echo "Honoring caller-shipped biome config."
  fi
  rc2=0
  npx --yes "@biomejs/biome@$PIN_BIOME" ci \
    --formatter-enabled=true --linter-enabled=false --assist-enabled=false \
    "${biome_cfg_args[@]}" . || rc2=$?
  if [ "$rc" -ne 0 ]; then
    lane_fail gate-lint-format-jsts "oxlint@$PIN_OXLINT found lint findings (warnings are denied, like CI)"
  elif [ "$rc2" -ne 0 ]; then
    lane_fail gate-lint-format-jsts "biome@$PIN_BIOME found unformatted ts/js. NOTE: the format gate covers CONFIG FILES TOO, including biome.json itself - self-format with: npx --yes @biomejs/biome@$PIN_BIOME format --write ."
  else
    lane_pass gate-lint-format-jsts "oxlint@$PIN_OXLINT clean, biome@$PIN_BIOME format clean"
  fi
fi

# ── GATE 1 (py) — ruff (self-contained, pinned) ────────────────────────────
if [ "$DETECT_PYTHON" = "true" ]; then
  echo "== gate-lint-format-py (ruff@$PIN_RUFF) =="
  python -m ruff --version >/dev/null 2>&1 || pip install "ruff==$PIN_RUFF" >/dev/null 2>&1 \
    || fail preflight "could not run or install ruff==$PIN_RUFF (pip install it manually)"
  rc=0
  python -m ruff check . || rc=$?
  rc2=0
  python -m ruff format --check . || rc2=$?
  if [ "$rc" -ne 0 ]; then
    lane_fail gate-lint-format-py "ruff@$PIN_RUFF found lint findings (run 'python -m ruff check --fix .')"
  elif [ "$rc2" -ne 0 ]; then
    lane_fail gate-lint-format-py "ruff@$PIN_RUFF found unformatted Python (run 'python -m ruff format .')"
  else
    lane_pass gate-lint-format-py "ruff@$PIN_RUFF check + format clean"
  fi
fi

# ── GATE 2 ts — tsc --noEmit per tsconfig (like CI) ────────────────────────
if [ "$DETECT_TS" = "true" ]; then
  echo "== gate-types tsc =="
  rc=0
  install_deps() {
    if [ -f "$1/package.json" ]; then
      ( cd "$1" && { npm ci || npm install; } ) >/dev/null 2>&1
    fi
  }
  if [ -f tsconfig.json ]; then
    install_deps .
    npx --yes tsc --noEmit || rc=$?
  else
    set +o pipefail
    mapfile -t cfgs < <(git ls-files '**/tsconfig.json' \
      | grep -Ev '(^|/)(node_modules|dist|out|build|\.venv|vendor)/' || true)
    set -o pipefail
    for cfg in "${cfgs[@]}"; do
      d="$(dirname "$cfg")"
      install_deps "$d"
      npx --yes tsc -p "$d" --noEmit || rc=$?
    done
  fi
  if [ "$rc" -ne 0 ]; then
    lane_fail gate-types-tsc "tsc --noEmit reported type errors"
  else
    lane_pass gate-types-tsc "tsc --noEmit clean"
  fi
fi

# ── GATE 2 py — basedpyright (caller config honored, else standard) ────────
if [ "$DETECT_PYTHON" = "true" ]; then
  echo "== gate-types basedpyright@$PIN_BASEDPYRIGHT =="
  python -m basedpyright --version >/dev/null 2>&1 || pip install "basedpyright==$PIN_BASEDPYRIGHT" >/dev/null 2>&1 \
    || fail preflight "could not run or install basedpyright==$PIN_BASEDPYRIGHT (pip install it manually)"
  rc=0
  if [ "$DETECT_PYRIGHTCFG" = "true" ]; then
    echo "Honoring caller-shipped basedpyright/pyright config (running as-is)."
    python -m basedpyright || rc=$?
  else
    echo "No caller basedpyright/pyright config - using standard typeCheckingMode (mirrors Reframe)."
    python -m basedpyright --typecheckingmode standard || rc=$?
  fi
  if [ "$rc" -ne 0 ]; then
    lane_fail gate-types-py "basedpyright@$PIN_BASEDPYRIGHT reported type errors"
  else
    lane_pass gate-types-py "basedpyright@$PIN_BASEDPYRIGHT clean"
  fi
fi

# ── GATE 3 py — tests + STRICT 100% line+branch ────────────────────────────
if [ "$DETECT_PYTHON" = "true" ]; then
  echo "== gate-tests-coverage python =="
  if [ "$DETECT_PYCOVCFG" != "true" ] && [ "$DETECT_PYTESTS" != "true" ]; then
    lane_skip gate-tests-coverage-py "Python source with NO test files and NO coverage config - no test surface by design"
  elif [ "$DETECT_PYCOVCFG" != "true" ]; then
    lane_fail gate-tests-coverage-py "Python source + tests present but NO coverage config (pyproject.toml/setup.cfg/tox.ini/.coveragerc/pytest.ini) - the 100% line+BRANCH gate cannot pass unmeasured. LINE-ONLY 100% is not enough: without branch measurement you typically sit at ~99.x and still fail. Add a coverage config."
  else
    set +o pipefail
    mapfile -t reqs < <(git ls-files \
      requirements.txt 'requirements/*.txt' 'requirements-*.txt' 'requirements*.txt' \
      '**/requirements.txt' 2>/dev/null \
      | grep -Ev '(^|/)(node_modules|\.venv|venv|dist|build|out)/' \
      | grep -Eiv 'requirements.*(dev|test|lint|doc|ci).*\.txt$' || true)
    set -o pipefail
    if [ "${#reqs[@]}" -gt 0 ]; then
      for r in "${reqs[@]}"; do
        echo "pip install -r $r (best-effort, like CI)"
        pip install -r "$r" >/dev/null 2>&1 || echo "WARN: 'pip install -r $r' failed; continuing (tests will surface any missing import)."
      done
    elif [ -f pyproject.toml ] || [ -f setup.cfg ] || [ -f setup.py ]; then
      echo "pip install . (best-effort, like CI)"
      pip install . >/dev/null 2>&1 || echo "WARN: 'pip install .' failed; continuing (tests will surface any missing import)."
    fi
    rc=0
    if grep -rqs 'pytest' pyproject.toml setup.cfg tox.ini pytest.ini 2>/dev/null; then
      pip install pytest pytest-cov >/dev/null 2>&1
      python -m pytest --cov --cov-branch --cov-fail-under=100 || rc=$?
    else
      pip install coverage >/dev/null 2>&1
      python -m coverage run -m unittest discover || rc=$?
      python -m coverage report --fail-under=100 --show-missing || rc=$?
    fi
    if [ "$rc" -ne 0 ]; then
      lane_fail gate-tests-coverage-py "tests failed OR coverage below STRICT 100% line+branch (line-only 100% is ~99.x once branches count and still FAILs; CI enforces --cov-branch identically)"
    else
      lane_pass gate-tests-coverage-py "tests green at 100% line+branch"
    fi
  fi
fi

# ── GATE 3 jsts — caller coverage script + asserted number ─────────────────
if [ "$DETECT_JSTS" = "true" ]; then
  echo "== gate-tests-coverage jsts =="
  has_cov_lane() {
    local d="./$1"
    if node -e "process.exit((require('$d/package.json').scripts||{})['test:coverage']?0:1)" 2>/dev/null; then echo "script"; return 0; fi
    if node -e "const s=(require('$d/package.json').scripts||{}).test||'';process.exit(/(vitest|jest).*--coverage|--coverage.*(vitest|jest)/.test(s)?0:1)" 2>/dev/null; then echo "runner"; return 0; fi
    echo ""; return 0
  }
  declare -a proj_dirs=()
  if [ -f package.json ]; then
    proj_dirs=(.)
  else
    set +o pipefail
    mapfile -t pkgs < <(git ls-files '**/package.json' \
      | grep -Ev '(^|/)(node_modules|dist|out|build|\.venv|vendor)/' || true)
    set -o pipefail
    for p in "${pkgs[@]}"; do proj_dirs+=("$(dirname "$p")"); done
  fi
  ran_any=false
  rc=0
  for d in "${proj_dirs[@]}"; do
    lane_kind="$(has_cov_lane "$d")"
    if [ -n "$lane_kind" ]; then
      slug="$(printf '%s' "$d" | tr -c 'A-Za-z0-9' '_')"
      log="$WORK_DIR/jsts-coverage-$slug.log"
      ( cd "$d" && { npm ci || npm install; } >/dev/null 2>&1 \
        && if [ "$lane_kind" = "script" ]; then npm run test:coverage; else npm test; fi ) 2>&1 | tee "$log" || rc=$?
      python "$QZP_ROOT/scripts/quality/jsts_coverage_assert.py" \
        --project-dir "$d" --log "$log" --min-percent 100 || rc=$?
      ran_any=true
    fi
  done
  if [ "$ran_any" = "true" ]; then
    if [ "$rc" -ne 0 ]; then
      lane_fail gate-tests-coverage-jsts "a measured JS/TS coverage figure was below the 100% line+branch bar (or no figure could be parsed)"
    else
      lane_pass gate-tests-coverage-jsts "measured coverage met the 100% bar in every project dir"
    fi
  elif [ "$DETECT_JSTSTESTS" != "true" ]; then
    lane_skip gate-tests-coverage-jsts "ts/js source with NO test files and no coverage script - no test surface by design"
  else
    lane_fail gate-tests-coverage-jsts "ts/js present (with test files) but no coverage script in any project dir - the 100% gate cannot pass unmeasured. Add a 'test:coverage' npm script."
  fi
fi

# ── GATE 3 compiled — the caller's own coverage gate script ────────────────
needs=""
if [ "$DETECT_GO" = "true" ] && [ "$DETECT_GOTESTS" = "true" ]; then needs="go"; fi
if [ "$DETECT_CPP" = "true" ] && [ "$DETECT_CPPTESTS" = "true" ]; then needs="${needs:+$needs+}c++"; fi
if [ -n "$needs" ]; then
  echo "== gate-tests-coverage compiled ($needs) =="
  cov_script="${QZP_PREFLIGHT_COVERAGE_GATE_SCRIPT:-scripts/coverage-gate.sh}"
  rc=0
  case "$needs" in
    *go*) [ "$DETECT_GOMOD" = "true" ] || { lane_fail gate-tests-coverage-compiled "*_test.go files exist but no root go.mod - coverage cannot be measured"; rc=1; } ;;
  esac
  if [ "$rc" -eq 0 ]; then
    if [ ! -f "$cov_script" ]; then
      lane_fail gate-tests-coverage-compiled "compiled-language test files present but no coverage gate at '$cov_script' - the STRICT 100% gate cannot pass unmeasured"
    else
      bash "$cov_script" && lane_pass gate-tests-coverage-compiled "caller coverage gate '$cov_script' passed" \
        || lane_fail gate-tests-coverage-compiled "caller coverage gate '$cov_script' failed"
    fi
  fi
elif [ "$DETECT_GO" = "true" ] || [ "$DETECT_CPP" = "true" ]; then
  lane_skip gate-tests-coverage-compiled "compiled source present with ZERO test files - no test surface by design"
fi

# ── GATE 4 — opengrep SAST ─────────────────────────────────────────────────
if [ "$DETECT_OPENGREP" = "true" ]; then
  echo "== gate-sast opengrep@$PIN_OPENGREP =="
  if [ ! -f .semgrepignore ]; then
    echo "WARN gate-sast: no .semgrepignore in the repo root - opengrep's built-in template silently skips *_test.go plus test/ and tests/ (same warning CI emits)."
  fi
  og="$(resolve_opengrep)" || og=""
  if [ -z "$og" ]; then
    lane_skip gate-sast "opengrep@$PIN_OPENGREP binary not on PATH and not installable here (set QZP_PREFLIGHT_NO_INSTALL=0 on linux/darwin, or install opengrep). CI still runs this lane."
  else
    rc=0
    "$og" scan --config .quality/opengrep --error \
      --exclude .venv --exclude node_modules --exclude dist --exclude out --exclude build \
      . || rc=$?
    if [ "$rc" -ne 0 ]; then
      lane_fail gate-sast "opengrep@$PIN_OPENGREP reported findings"
    else
      lane_pass gate-sast "opengrep@$PIN_OPENGREP clean"
    fi
  fi
fi

# ── GATE 5 — gitleaks secrets (ALWAYS, like CI round-7) ────────────────────
echo "== gate-secrets gitleaks@$PIN_GITLEAKS =="
gl="$(resolve_gitleaks)" || gl=""
if [ -z "$gl" ]; then
  lane_skip gate-secrets "gitleaks@$PIN_GITLEAKS binary not on PATH and download unavailable (set QZP_PREFLIGHT_NO_INSTALL=0 or install gitleaks). CI ALWAYS runs this lane - do not treat this skip as clean."
else
  args=(detect --no-banner --redact)
  if [ "$DETECT_GITLEAKS_ALLOWLIST" = "true" ]; then
    echo "Honoring the caller's .gitleaks.toml allowlist."
    args+=(--config .gitleaks.toml)
  else
    echo "No .gitleaks.toml in this repo - scanning with the pinned gitleaks default ruleset."
  fi
  rc=0
  "$gl" "${args[@]}" || rc=$?
  if [ "$rc" -ne 0 ]; then
    lane_fail gate-secrets "gitleaks@$PIN_GITLEAKS found secret candidate(s) (--redact on; see report above)"
  else
    lane_pass gate-secrets "gitleaks@$PIN_GITLEAKS 0 findings"
  fi
fi

# ── GATE 6 — osv-scanner deps + T0 severity floor ─────────────────────────
echo "== gate-deps osv-scanner@$PIN_OSV =="
if [ "$DETECT_OSVSOURCES" != "true" ]; then
  lane_pass gate-deps "no dependency manifests or lockfiles present - nothing to scan (CI passes identically)"
else
  osv="$(resolve_osv)" || osv=""
  if [ -z "$osv" ]; then
    lane_skip gate-deps "osv-scanner@$PIN_OSV binary not on PATH and download unavailable. CI still runs this lane."
  else
    # --config only when the file ships (osv-scanner rejects a missing config
    # path with exit 130 — same shape scheduled_cve_scan.py uses).
    osv_config_args=()
    [ -f osv-scanner.toml ] && osv_config_args=(--config=osv-scanner.toml)
    is_scan_error() { case "$1" in 127|129|130) return 0 ;; *) return 1 ;; esac; }
    is_retryable() { case "$1" in 127|129) return 0 ;; *) return 1 ;; esac; }
    attempt=1
    max_attempts=3
    rc=0
    while : ; do
      rc=0
      "$osv" scan source "${osv_config_args[@]}" --recursive . || rc=$?
      if ! is_retryable "$rc" || [ "$attempt" -ge "$max_attempts" ]; then break; fi
      backoff=$(( attempt * 20 ))
      echo "RETRY gate-deps: osv-scanner exited $rc without completing the scan (attempt ${attempt}/${max_attempts}); retrying in ${backoff}s." >&2
      sleep "$backoff"
      attempt=$(( attempt + 1 ))
    done
    if [ "$rc" -eq 0 ]; then
      lane_pass gate-deps "osv-scanner@$PIN_OSV found 0 actionable CVEs"
    elif [ "$rc" -eq 128 ]; then
      lane_pass gate-deps "osv-scanner exited 128 (no package sources found) - nothing to scan"
    elif [ "$rc" -eq 1 ]; then
      jrc=0
      "$osv" scan source "${osv_config_args[@]}" --recursive --format json . > "$WORK_DIR/osv-report.json" || jrc=$?
      if [ "$jrc" -ne 0 ] && [ "$jrc" -ne 1 ]; then
        lane_fail gate-deps "the JSON re-scan needed to classify findings exited $jrc - NOT a vulnerability verdict; the gate blocks rather than passing silently"
      else
        grc=0
        python "$QZP_ROOT/scripts/quality/osv_severity_gate.py" "$WORK_DIR/osv-report.json" || grc=$?
        if [ "$grc" -eq 0 ]; then
          lane_pass gate-deps "0 findings clear the T0 severity floor (T2 inventory non-blocking, same classifier as CI)"
        elif [ "$grc" -eq 1 ]; then
          lane_fail gate-deps "at least one PRODUCTION dependency has a CVSS >= 7.0 advisory WITH a published fix - bump it"
        else
          lane_fail gate-deps "the severity classifier exited $grc (expected 0 or 1) - NOT a vulnerability verdict; the gate blocks"
        fi
      fi
    elif is_scan_error "$rc"; then
      lane_fail gate-deps "osv-scanner could not complete the scan (exit $rc) - NOT a vulnerability finding; re-run once upstream recovers"
    else
      lane_fail gate-deps "osv-scanner exited $rc, not a documented exit code for the pinned $PIN_OSV - treated as a scan failure, NOT a finding"
    fi
  fi
fi

# ── Charter drift guard (caller-side, when shipped) ────────────────────────
if [ "$DETECT_CHARTER" = "true" ]; then
  echo "== charter-check =="
  rc=0
  bash .quality/charter_check.sh || rc=$?
  if [ "$rc" -ne 0 ]; then
    lane_fail charter-check "the active gate set drifted from .quality/charter.yml"
  else
    lane_pass charter-check "gate set matches the charter"
  fi
fi

# ── Summary ────────────────────────────────────────────────────────────────
echo
echo "── preflight summary ─────────────────────────────────────"
failed=0
skipped=0
for r in "${RESULTS[@]}"; do
  echo "$r"
  case "$r" in FAIL*) failed=$(( failed + 1 )) ;; SKIP*) skipped=$(( skipped + 1 )) ;; esac
done
echo "──────────────────────────────────────────────────────────"
if [ "$failed" -gt 0 ]; then
  echo "PREFLIGHT FAIL: $failed lane(s) failed, $skipped skipped. CI (reusable-quality.yml) will fail the same lanes."
  exit 1
fi
echo "PREFLIGHT OK: ${#RESULTS[@]} lane(s) run, 0 failed, $skipped skipped (skips are printed above; CI may still run skipped binary lanes)."
