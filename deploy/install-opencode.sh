#!/usr/bin/env bash
# Install a pinned, verified OpenCode CLI for the Facai service.
#
# The distribution is a single self-contained binary, so no Node/npm runtime is
# required on the host. We pin an exact version *and* its SHA-256: a changed or
# tampered download is refused instead of installed silently. The binary lands on
# a system path (/usr/local/bin) that the hardened systemd unit can execute.
#
#   sudo ./deploy/install-opencode.sh
#   sudo OPENCODE_VERSION=1.18.33 OPENCODE_SHA256=<hash> ./deploy/install-opencode.sh
#
# It never reads, prints or writes a model credential; OpenCode authenticates
# from the service environment (DEEPSEEK_API_KEY) at run time.
set -euo pipefail

OPENCODE_VERSION="${OPENCODE_VERSION:-1.18.32}"
OPENCODE_SHA256="${OPENCODE_SHA256:-3046e0404fdc60fb80307e7a47824ba07477364178a4d09baa8548496dd6d43b}"
OPENCODE_ASSET="${OPENCODE_ASSET:-opencode-linux-x64.tar.gz}"
INSTALL_DIR="${INSTALL_DIR:-/usr/local/bin}"
TARGET="${INSTALL_DIR}/opencode"
BASE_URL="https://github.com/anomalyco/opencode/releases/download/v${OPENCODE_VERSION}"

log() { printf '[facai-opencode] %s\n' "$*"; }
die() { printf '[facai-opencode] ERROR: %s\n' "$*" >&2; exit 1; }
need_root() { [ "$(id -u)" -eq 0 ] || die "run as root (use sudo)"; }

need_root
for tool in curl tar sha256sum install find mktemp; do
  command -v "$tool" >/dev/null 2>&1 || die "missing required tool: $tool"
done

# Idempotent: a correct version already in place is left untouched.
if [ -x "$TARGET" ] && [ "$("$TARGET" --version 2>/dev/null || true)" = "$OPENCODE_VERSION" ]; then
  log "opencode ${OPENCODE_VERSION} already installed at ${TARGET}"
  exit 0
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
archive="${tmp}/${OPENCODE_ASSET}"

log "downloading ${BASE_URL}/${OPENCODE_ASSET} (v${OPENCODE_VERSION})"
curl -fsSL --retry 3 -o "$archive" "${BASE_URL}/${OPENCODE_ASSET}"

actual="$(sha256sum "$archive" | awk '{print $1}')"
[ "$actual" = "$OPENCODE_SHA256" ] \
  || die "SHA-256 mismatch for ${OPENCODE_ASSET}: got ${actual}, expected ${OPENCODE_SHA256}"
log "checksum verified (${OPENCODE_SHA256})"

tar xzf "$archive" -C "$tmp"
binary="$(find "$tmp" -maxdepth 3 -type f -name opencode | head -1)"
[ -n "$binary" ] || die "opencode binary not found inside ${OPENCODE_ASSET}"

install -d -m 0755 "$INSTALL_DIR"
install -m 0755 "$binary" "$TARGET"
log "installed opencode $("$TARGET" --version) at ${TARGET}"
