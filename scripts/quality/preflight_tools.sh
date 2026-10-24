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

# qzp_is_musl -- true on musl libc (Alpine); QZP_LIBC overrides detection.
qzp_is_musl() {
  case "${QZP_LIBC:-}" in
    musl) return 0 ;;
    glibc) return 1 ;;
  esac
  ldd /bin/sh 2>&1 | grep -qi musl
}

resolve_gitleaks() {
  if [ "${QZP_PREFLIGHT_PINNED_ONLY:-0}" != "1" ] && command -v gitleaks >/dev/null 2>&1; then command -v gitleaks; return 0; fi
  [ "$QZP_NO_INSTALL" = "1" ] && return 1
  local garch
  case "$arch" in amd64) garch=x64 ;; arm64) garch=arm64 ;; *) return 1 ;; esac
  case "$os" in linux|darwin) : ;; windows) [ "$garch" = "x64" ] || return 1 ;; *) return 1 ;; esac
  local dir="$CACHE_DIR/gitleaks/$PIN_GITLEAKS" base="https://github.com/gitleaks/gitleaks/releases/download/v$PIN_GITLEAKS"
  local bin="$dir/gitleaks"
  [ "$os" = "windows" ] && bin="$dir/gitleaks.exe"
  if [ ! -x "$bin" ]; then
    if [ "$os" = "windows" ]; then
      qzp_fetch_verified "$base/gitleaks_${PIN_GITLEAKS}_windows_x64.zip" "$dir/gitleaks.zip" gitleaks_windows_x64 || return 1
      unzip -oq "$dir/gitleaks.zip" gitleaks.exe -d "$dir" || return 1
    else
      qzp_fetch_verified "$base/gitleaks_${PIN_GITLEAKS}_${os}_${garch}.tar.gz" "$dir/gitleaks.tar.gz" "gitleaks_${os}_${garch}" || return 1
      tar -xzf "$dir/gitleaks.tar.gz" -C "$dir" gitleaks || return 1
    fi
    chmod +x "$bin" 2>/dev/null || true
  fi
  [ -x "$bin" ] || return 1
  printf '%s' "$bin"
}

resolve_osv() {
  if [ "${QZP_PREFLIGHT_PINNED_ONLY:-0}" != "1" ] && command -v osv-scanner >/dev/null 2>&1; then command -v osv-scanner; return 0; fi
  [ "$QZP_NO_INSTALL" = "1" ] && return 1
  case "$arch" in amd64|arm64) : ;; *) return 1 ;; esac
  local asset key bin="$CACHE_DIR/osv-scanner/$PIN_OSV/osv-scanner"
  case "$os" in
    linux|darwin) asset="osv-scanner_${os}_${arch}"; key="$asset" ;;
    windows) [ "$arch" = "amd64" ] || return 1; asset="osv-scanner_windows_amd64.exe"; key="osv-scanner_windows_amd64"; bin="$bin.exe" ;;
    *) return 1 ;;
  esac
  if [ ! -x "$bin" ]; then
    qzp_fetch_verified "https://github.com/google/osv-scanner/releases/download/$PIN_OSV/$asset" "$bin" "$key" || return 1
    chmod +x "$bin" 2>/dev/null || true
  fi
  [ -x "$bin" ] || return 1
  printf '%s' "$bin"
}

resolve_opengrep() {
  if [ "${QZP_PREFLIGHT_PINNED_ONLY:-0}" != "1" ] && command -v opengrep >/dev/null 2>&1; then command -v opengrep; return 0; fi
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
  if [ ! -x "$bin" ]; then
    qzp_fetch_verified "https://github.com/opengrep/opengrep/releases/download/$PIN_OPENGREP/$asset" "$bin" "$asset" || return 1
    chmod +x "$bin" 2>/dev/null || true
  fi
  [ -x "$bin" ] || return 1
  printf '%s' "$bin"
}
