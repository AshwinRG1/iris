#!/usr/bin/env bash
# Deploy a ref to the box and roll back by itself if /health does not come up. Run as root.
#
#   bash /opt/iris/deploy/update.sh <ref>      # branch (means origin/<branch>), tag or commit
#
# Steps: fetch, check out <ref>, install requirements, record IRIS_COMMIT, restart iris, wait
# for /health to report ready with auth on. If that fails, the previous commit and the previous
# IRIS_COMMIT come back, and the script exits non-zero. Re-running the ref that is already
# checked out only verifies health. An unknown ref exits before anything changes.
#
# A kill (HUP, INT, TERM) after the checkout rolls back too. Even so, run it under nohup or
# systemd-run so a dropped SSH session does not interrupt it at all.
#
# The script never sources, prints or rewrites anything in the env file except IRIS_COMMIT,
# and never touches the models directory or torch.
#
# Environment (defaults are right for the box; tests override them):
#   IRIS_APP_DIR           checkout to update (default /opt/iris).
#   IRIS_ENV_FILE          service env file (default /etc/iris.env).
#   IRIS_VENV              virtualenv holding pip (default /opt/iris/.venv).
#   IRIS_HEALTH_URL        health endpoint (default http://127.0.0.1:8000/health).
#   IRIS_HEALTH_TIMEOUT_S  seconds to wait for ready (default 60).
#   IRIS_SYSTEMCTL         systemctl command (default systemctl).
#   IRIS_GIT_SSH           git SSH command; default is the deploy key from bootstrap.sh
#                          (IRIS_DEPLOY_KEY, /root/.ssh/iris_deploy). An inherited
#                          GIT_SSH_COMMAND is ignored.
#   IRIS_LOCK_FILE         flock file so two updates cannot overlap (default /run/iris-update.lock).
set -euo pipefail

APP="${IRIS_APP_DIR:-/opt/iris}"
ENV_FILE="${IRIS_ENV_FILE:-/etc/iris.env}"
VENV="${IRIS_VENV:-/opt/iris/.venv}"
HEALTH_URL="${IRIS_HEALTH_URL:-http://127.0.0.1:8000/health}"
HEALTH_TIMEOUT_S="${IRIS_HEALTH_TIMEOUT_S:-60}"
SYSTEMCTL="${IRIS_SYSTEMCTL:-systemctl}"
LOCK_FILE="${IRIS_LOCK_FILE:-/run/iris-update.lock}"
IRIS_DEPLOY_KEY="${IRIS_DEPLOY_KEY:-/root/.ssh/iris_deploy}"
GIT_SSH_COMMAND="${IRIS_GIT_SSH:-ssh -i $IRIS_DEPLOY_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/root/.ssh/known_hosts}"
export GIT_SSH_COMMAND
unset GIT_DIR GIT_WORK_TREE

log() { printf '==> %s\n' "$*"; }
die() { printf '%s\n' "$*" >&2; exit 1; }

# Reads a /health body on stdin; prints ready, authoff, error:<load_error> or wait.
health_state() {
  python3 -c '
import json, sys
try:
    h = json.load(sys.stdin)
except ValueError:
    print("wait")
    raise SystemExit
if h.get("load_error"):
    print("error:" + h["load_error"])
elif not h.get("ready"):
    print("wait")
elif h.get("auth") != "on":
    print("authoff")
else:
    print("ready")
'
}

# Returns 0 once /health is ready with auth on; 1 on timeout, auth off or load_error.
wait_health() {
  local body state _
  log "waiting up to ${HEALTH_TIMEOUT_S} s for /health"
  for _ in $(seq 1 "$HEALTH_TIMEOUT_S"); do
    body="$(curl -fsS --max-time 2 "$HEALTH_URL" 2>/dev/null || true)"
    state="$(health_state <<<"$body")"
    case "$state" in
      ready) return 0 ;;
      authoff)
        echo "/health reports auth off" >&2
        return 1
        ;;
      error:*)
        echo "model failed to load (${state#error:}); see: journalctl -u iris -n 100" >&2
        return 1
        ;;
    esac
    sleep 1
  done
  echo "server did not report ready; see: journalctl -u iris -n 100" >&2
  return 1
}

# Give $2 the mode and owner of $1. GNU --reference on the box; stat fallback elsewhere.
copy_perms() {
  if chown --reference="$1" "$2" 2>/dev/null; then
    chmod --reference="$1" "$2"
  else
    chown "$(stat -f '%u:%g' "$1")" "$2"
    chmod "$(stat -f '%Lp' "$1")" "$2"
  fi
}

# Replace the IRIS_COMMIT line with $1 (a whole "IRIS_COMMIT=..." line), or append it. Every other
# line stays byte-for-byte. The new file is built beside the old one and renamed over it, so a
# crash leaves either the old file or the new one, and a failure leaves no copy of the key behind.
set_commit_line() {
  local tmp
  tmp="$(mktemp "$(dirname "$ENV_FILE")/.iris.env.XXXXXX")" || return 1
  {
    NEWLINE="$1" awk '
      /^IRIS_COMMIT=/ { if (!done) print ENVIRON["NEWLINE"]; done = 1; next }
      { print }
      END { if (!done) print ENVIRON["NEWLINE"] }
    ' "$ENV_FILE" >"$tmp" &&
      copy_perms "$ENV_FILE" "$tmp" &&
      sync &&
      mv -f "$tmp" "$ENV_FILE"
  } || {
    rm -f "$tmp"
    return 1
  }
}

# Each step is checked explicitly: inside `if` or `&&` set -e is off, so a failed pip must
# return here instead of being masked by the restart.
install_and_restart() {
  "$VENV/bin/pip" install -q -r "$APP/server/requirements-ml.txt" || return 1
  "$SYSTEMCTL" restart iris || return 1
}

# Put the previous commit and IRIS_COMMIT back, restart, check health, and exit non-zero.
# Signals are ignored meanwhile so a second kill cannot leave the box half rolled back.
rollback() {
  trap '' HUP INT TERM
  log "$1; rolling back to $OLD_SHORT"
  if git -C "$APP" checkout -q -f --detach "$OLD_SHA" &&
    set_commit_line "$OLD_LINE" &&
    install_and_restart && wait_health; then
    die "deploy of $NEW_SHORT failed; rolled back to $OLD_SHORT"
  fi
  die "deploy of $NEW_SHORT failed and ROLLBACK ALSO FAILED on $OLD_SHORT; see: journalctl -u iris -n 100"
}

{ [ $# -eq 1 ] && [ -n "$1" ]; } || die "Usage: $0 <ref>   (branch, tag or commit to deploy)"
REF="$1"

# One update at a time. The lock is held on fd 9 until this script exits.
exec 9>"$LOCK_FILE"
flock -n 9 || die "another update is already running (lock $LOCK_FILE)"

OLD_SHA="$(git -C "$APP" rev-parse --verify 'HEAD^{commit}')"
OLD_SHORT="$(git -C "$APP" rev-parse --short "$OLD_SHA")"
OLD_LINE="$(grep -m1 '^IRIS_COMMIT=' "$ENV_FILE" || true)"
[ -n "$OLD_LINE" ] || OLD_LINE="IRIS_COMMIT=$OLD_SHORT"

log "fetching"
git -C "$APP" fetch -q --tags --force --prune origin
# A branch means origin/<branch>; a tag or commit resolves as given.
TARGET="$REF"
if git -C "$APP" show-ref -q --verify "refs/remotes/origin/$REF"; then
  TARGET="origin/$REF"
fi
NEW_SHA="$(git -C "$APP" rev-parse --verify "$TARGET^{commit}" 2>/dev/null)" ||
  die "unknown ref: $REF (nothing was changed)"
NEW_SHORT="$(git -C "$APP" rev-parse --short "$NEW_SHA")"

if [ "$NEW_SHA" = "$OLD_SHA" ]; then
  log "$REF is already deployed ($NEW_SHORT); verifying health only"
  wait_health || die "unhealthy on $NEW_SHORT; nothing to roll back to"
  log "ready, auth on (commit $NEW_SHORT)"
  exit 0
fi

log "deploying $NEW_SHORT (was $OLD_SHORT)"
# From here a kill (dropped SSH session, Ctrl-C) rolls back instead of leaving the new commit.
trap 'rollback "interrupted"' HUP INT TERM
if git -C "$APP" checkout -q -f --detach "$NEW_SHA" &&
  set_commit_line "IRIS_COMMIT=$NEW_SHORT" &&
  install_and_restart && wait_health; then
  trap - HUP INT TERM
  log "ready, auth on (commit $NEW_SHORT)"
  exit 0
fi
rollback "deploy of $NEW_SHORT failed"
