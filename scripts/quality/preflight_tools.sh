# shellcheck shell=bash
# Pinned-binary provisioning for scripts/quality/preflight.sh (sourced, not run).
#
# Each tool is fetched as its pinned GitHub release ASSET with `curl -f` and
# installed only after its SHA-256 matches `.quality/tool-pins.yml`
# (asset_sha256) -- the same contract the CI lean gate follows. Two defects this
# replaces: gitleaks was requested as `<os>_amd64` although its x86-64 assets
# are named `<os>_x64` (a 404, so gate 5 was skipped on every x86-64 Linux and
# macOS box), and opengrep's install.sh installs under $HOME/.opengrep while the
# caller looked in the cache dir (so gate 4 was always skipped).
#
# Inputs (set by the caller): os (linux|darwin|windows|unknown),
# arch (amd64|arm64|unknown), CACHE_DIR, PINS_FILE, QZP_NO_INSTALL, PIN_GITLEAKS,
# PIN_OSV, PIN_OPENGREP. Optional: QZP_LIBC=musl|glibc overrides libc detection.
# QZP_PREFLIGHT_PINNED_ONLY=1 ignores same-named tools already on PATH and
# always uses the verified pinned binaries (strict local/CI parity).
# Each resolve_* prints the binary path and returns 0, or returns 1 (the caller
# turns that into a visible lane SKIP).

# qzp_sha256 <file> -- SHA-256 hex digest (sha256sum, else macOS shasum).
qzp_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | cut -d' ' -f1
  else
    return 1
  fi
}

# qzp_asset_digest <key> -- the pinned digest for an asset key, or empty.
qzp_asset_digest() {
  sed -n "s/^  $1: \"\\([0-9a-f]\\{64\\}\\)\"\$/\\1/p" "$PINS_FILE" | head -1
}

# qzp_fetch_verified <url> <dest> <digest-key> -- download, verify, then install.
qzp_fetch_verified() {
  local url="$1" dest="$2" key="$3" expected actual
  expected="$(qzp_asset_digest "$key")"
  if [ -z "$expected" ]; then
    echo "ERROR preflight: no pinned SHA-256 for '$key' in $PINS_FILE (asset_sha256); refusing an unverified download" >&2
    return 1
  fi
  mkdir -p "$(dirname "$dest")"
  curl -fsSL --retry 3 -o "$dest.download" "$url" || { rm -f "$dest.download"; return 1; }
  actual="$(qzp_sha256 "$dest.download")" || { rm -f "$dest.download"; return 1; }
  if [ "$actual" != "$expected" ]; then
    echo "ERROR preflight: SHA-256 mismatch for $url (expected $expected, got $actual); not installed" >&2
    rm -f "$dest.download"
    return 1
  fi
  mv "$dest.download" "$dest"
}

# qzp_ensure_verified <url> <dest> <digest-key> -- reuse a cached <dest> only
# while it still matches the pinned digest; a stale, corrupted or replaced
# cache (e.g. one written by the old unverified downloader) is re-fetched.
qzp_ensure_verified() {
  local url="$1" dest="$2" key="$3" expected
  expected="$(qzp_asset_digest "$key")"
  if [ -n "$expected" ] && [ -f "$dest" ] && [ "$(qzp_sha256 "$dest" 2>/dev/null)" = "$expected" ]; then
    return 0
  fi
  rm -f "$dest"
  qzp_fetch_verified "$url" "$dest" "$key"
}

# qzp_is_musl -- true on musl libc (Alpine); QZP_LIBC overrides detection.
qzp_is_musl() {
  case "${QZP_LIBC:-}" in
    musl) return 0 ;;
    glibc) return 1 ;;
  esac
  ldd /bin/sh 2>&1 | grep -qi musl
}

# qzp_path_tool_is_pinned <cmd> <pin> <version-args...> -- true when <cmd> is
# on PATH AND reports EXACTLY the pinned version: one whitespace-separated word
# of its version output, less a leading "v", must equal the pin (so 1.32.0-rc1
# or 1.32.01 never pass for 1.32.0). Any other installed version (the previous
# pin, say) would run a different engine than CI under the pin's name, so it
# falls through to the verified pinned asset instead. The output is captured
# first: under pipefail, `cmd | grep -q` can lose a match to SIGPIPE.
qzp_path_tool_is_pinned() {
  local cmd="$1" pin="${2#v}" out line word
  local -a words
  shift 2
  command -v "$cmd" >/dev/null 2>&1 || return 1
  out="$("$cmd" "$@" 2>/dev/null)" || true
  while IFS= read -r line; do
    read -ra words <<< "$line"
    for word in "${words[@]}"; do
      [ "${word#v}" = "$pin" ] && return 0
    done
  done <<< "$out"
  return 1
}

resolve_gitleaks() {
  if [ "${QZP_PREFLIGHT_PINNED_ONLY:-0}" != "1" ] && qzp_path_tool_is_pinned gitleaks "$PIN_GITLEAKS" version; then command -v gitleaks; return 0; fi
  [ "$QZP_NO_INSTALL" = "1" ] && return 1
  local garch
  case "$arch" in amd64) garch=x64 ;; arm64) garch=arm64 ;; *) return 1 ;; esac
  case "$os" in linux|darwin) : ;; windows) [ "$garch" = "x64" ] || return 1 ;; *) return 1 ;; esac
  local dir="$CACHE_DIR/gitleaks/$PIN_GITLEAKS" base="https://github.com/gitleaks/gitleaks/releases/download/v$PIN_GITLEAKS"
  local bin="$dir/gitleaks"
  [ "$os" = "windows" ] && bin="$dir/gitleaks.exe"
  # The digest pins the ARCHIVE, so the cached archive is re-verified and the
  # binary re-extracted from it on every run (a replaced binary cannot survive).
  if [ "$os" = "windows" ]; then
    qzp_ensure_verified "$base/gitleaks_${PIN_GITLEAKS}_windows_x64.zip" "$dir/gitleaks.zip" gitleaks_windows_x64 || return 1
    unzip -oq "$dir/gitleaks.zip" gitleaks.exe -d "$dir" || return 1
  else
    qzp_ensure_verified "$base/gitleaks_${PIN_GITLEAKS}_${os}_${garch}.tar.gz" "$dir/gitleaks.tar.gz" "gitleaks_${os}_${garch}" || return 1
    tar -xzf "$dir/gitleaks.tar.gz" -C "$dir" gitleaks || return 1
  fi
  chmod +x "$bin" 2>/dev/null || true
  [ -x "$bin" ] || return 1
  printf '%s' "$bin"
}

resolve_osv() {
  if [ "${QZP_PREFLIGHT_PINNED_ONLY:-0}" != "1" ] && qzp_path_tool_is_pinned osv-scanner "$PIN_OSV" --version; then command -v osv-scanner; return 0; fi
  [ "$QZP_NO_INSTALL" = "1" ] && return 1
  case "$arch" in amd64|arm64) : ;; *) return 1 ;; esac
  local asset key bin="$CACHE_DIR/osv-scanner/$PIN_OSV/osv-scanner"
  case "$os" in
    linux|darwin) asset="osv-scanner_${os}_${arch}"; key="$asset" ;;
    windows) [ "$arch" = "amd64" ] || return 1; asset="osv-scanner_windows_amd64.exe"; key="osv-scanner_windows_amd64"; bin="$bin.exe" ;;
    *) return 1 ;;
  esac
  # the asset IS the binary: a cached copy is reused only while it verifies
  qzp_ensure_verified "https://github.com/google/osv-scanner/releases/download/$PIN_OSV/$asset" "$bin" "$key" || return 1
  chmod +x "$bin" 2>/dev/null || true
  [ -x "$bin" ] || return 1
  printf '%s' "$bin"
}

resolve_opengrep() {
  if [ "${QZP_PREFLIGHT_PINNED_ONLY:-0}" != "1" ] && qzp_path_tool_is_pinned opengrep "$PIN_OPENGREP" --version; then command -v opengrep; return 0; fi
  [ "$QZP_NO_INSTALL" = "1" ] && return 1
  local asset
  case "$os/$arch" in
    linux/amd64) if qzp_is_musl; then asset=opengrep_musllinux_x86; else asset=opengrep_manylinux_x86; fi ;;
    linux/arm64) if qzp_is_musl; then asset=opengrep_musllinux_aarch64; else asset=opengrep_manylinux_aarch64; fi ;;
    darwin/amd64) asset=opengrep_osx_x86 ;;
    darwin/arm64) asset=opengrep_osx_arm64 ;;
    *) return 1 ;;  # no opengrep CLI build for this platform
  esac
  local bin="$CACHE_DIR/opengrep/$PIN_OPENGREP/bin/opengrep"
  # the asset IS the binary: a cached copy is reused only while it verifies
  qzp_ensure_verified "https://github.com/opengrep/opengrep/releases/download/$PIN_OPENGREP/$asset" "$bin" "$asset" || return 1
  chmod +x "$bin" 2>/dev/null || true
  [ -x "$bin" ] || return 1
  printf '%s' "$bin"
}
