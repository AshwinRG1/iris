#!/usr/bin/env bash
# Deploy a ref to the box and roll back by itself if /health does not come up. Run as root.
#
#   bash /opt/iris/deploy/update.sh <ref>      # branch (means origin/<branch>), tag or commit
#
# Steps: fetch, check out <ref>, install requirements, record IRIS_COMMIT, restart iris, wait
# for /health to report ready, auth on and "commit" equal to the new short sha. If that fails,
# the previous commit and the previous IRIS_COMMIT come back, and the script exits non-zero.
# Re-running the ref that is already checked out only verifies health. An unknown ref exits
# before anything changes.
#
# A kill (HUP, INT, TERM) after the checkout rolls back too; after a hangup the output goes to
# IRIS_UPDATE_LOG. Still, run it so a dropped SSH session cannot interrupt it: under nohup, or
# systemd-run -p TimeoutStopSec=300 (the default 90 s stop timeout would SIGKILL a rollback,
# which can take about 180 s: pip, then up to 60 health tries).
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
#                          (IRIS_DEPLOY_KEY, /root/.ssh/iris_deploy). It and the
#                          deploy key only apply when origin is an SSH URL; an HTTPS
#                          clone ignores them. An inherited GIT_SSH_COMMAND is ignored.
#   IRIS_LOCK_FILE         flock file so two updates cannot overlap (default /run/iris-update.lock).
#   IRIS_UPDATE_LOG        where output goes once the terminal is gone
#                          (default /var/log/iris-update.log).
set -euo pipefail

APP="${IRIS_APP_DIR:-/opt/iris}"
ENV_FILE="${IRIS_ENV_FILE:-/etc/iris.env}"
VENV="${IRIS_VENV:-/opt/iris/.venv}"
HEALTH_URL="${IRIS_HEALTH_URL:-http://127.0.0.1:8000/health}"
HEALTH_TIMEOUT_S="${IRIS_HEALTH_TIMEOUT_S:-60}"
SYSTEMCTL="${IRIS_SYSTEMCTL:-systemctl}"
LOCK_FILE="${IRIS_LOCK_FILE:-/run/iris-update.lock}"
UPDATE_LOG="${IRIS_UPDATE_LOG:-/var/log/iris-update.log}"
IRIS_DEPLOY_KEY="${IRIS_DEPLOY_KEY:-/root/.ssh/iris_deploy}"
GIT_SSH_COMMAND="${IRIS_GIT_SSH:-ssh -i $IRIS_DEPLOY_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/root/.ssh/known_hosts}"
export GIT_SSH_COMMAND
unset GIT_DIR GIT_WORK_TREE

ENV_TMP="" # temp copy of the env file while it is being rewritten

# Output is best effort: a hung-up terminal or closed pipe must not stop a rollback under set -e.
# If a write fails, switch all output to the update log and write the line again there. This
# is what covers a launcher that starts us with SIGPIPE ignored (the PIPE trap cannot fire then).
log() { printf '==> %s\n' "$*" 2>/dev/null || { to_update_log; printf '==> %s\n' "$*" || true; }; }
warn() { printf '%s\n' "$*" >&2 2>/dev/null || { to_update_log; printf '%s\n' "$*" || true; }; }
die() {
  warn "$*"
  exit 1
}
# The log is created 0600 (only that file; a global umask would change the checkout's modes).
to_update_log() {
  (umask 077 && : >>"$UPDATE_LOG") 2>/dev/null || true
  exec >>"$UPDATE_LOG" 2>&1 || true
}
cleanup_env_tmp() { [ -z "${ENV_TMP:-}" ] || rm -f "$ENV_TMP"; }
trap cleanup_env_tmp EXIT
# The output pipe closed (ssh without a pty dropped, `| tee` interrupted): keep going and report to
# the log. Not `trap '' PIPE`: on bash 3.2 the failed write's buffered bytes leaked into a later $(...).
trap to_update_log PIPE

# Reads a /health body on stdin; $1 is the expected short commit. Prints ready, authoff,
# error:<load_error>, mismatch:<reported commit or "none"> or wait.
health_state() {
  python3 -c '
import json, re, sys

def is_sha(x):
    return isinstance(x, str) and re.fullmatch(r"[0-9a-f]{7,40}", x) is not None

# Both hex and at least 7 chars, one a prefix of the other: older deploys wrote shorter shas.
def same_commit(reported, expected):
    return is_sha(reported) and is_sha(expected) and (
        reported.startswith(expected) or expected.startswith(reported))

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
elif not same_commit(h.get("commit"), sys.argv[1]):
    c = h.get("commit")
    ok = isinstance(c, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,40}", c)
    print("mismatch:" + (c if ok else "none"))
else:
    print("ready")
' "$1"
}

# Returns 0 once /health is ready, auth on and reporting commit $1; 1 on timeout, auth off or
# load_error.
wait_health() {
  local expected="$1" body state reported="" _
  log "waiting up to ${HEALTH_TIMEOUT_S} s for /health (commit $expected)"
  for _ in $(seq 1 "$HEALTH_TIMEOUT_S"); do
    body="$(curl -fsS --max-time 2 "$HEALTH_URL" 2>/dev/null || true)"
    state="$(health_state "$expected" <<<"$body")"
    case "$state" in
      ready) return 0 ;;
      authoff)
        warn "/health reports auth off"
        return 1
        ;;
      error:*)
        warn "model failed to load (${state#error:}); see: journalctl -u iris -n 100"
        return 1
        ;;
      mismatch:*) reported="${state#mismatch:}" ;;
    esac
    sleep 1
  done
  if [ -n "$reported" ]; then
    warn "commit mismatch: expected $expected, /health reported $reported"
  else
    warn "server did not report ready; see: journalctl -u iris -n 100"
  fi
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
# crash leaves either the old file or the new one. ENV_TMP lets rollback and the EXIT trap remove
# the temp copy (it holds the key) if the script is killed or fails midway.
set_commit_line() {
  ENV_TMP="$(mktemp "$(dirname "$ENV_FILE")/.iris.env.XXXXXX")" || return 1
  if NEWLINE="$1" awk '
    /^IRIS_COMMIT=/ { if (!done) print ENVIRON["NEWLINE"]; done = 1; next }
    { print }
    END { if (!done) print ENVIRON["NEWLINE"] }
  ' "$ENV_FILE" >"$ENV_TMP" &&
    copy_perms "$ENV_FILE" "$ENV_TMP" &&
    sync &&
    mv -f "$ENV_TMP" "$ENV_FILE"; then
    ENV_TMP=""
  else
    cleanup_env_tmp
    ENV_TMP=""
    return 1
  fi
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
  cleanup_env_tmp
  ENV_TMP=""
  # If stdout is gone (hung-up terminal), keep reporting in the update log instead.
  : >>/dev/stdout 2>/dev/null || to_update_log
  log "$1; rolling back to $OLD_SHORT"
  if git -C "$APP" checkout -q -f --detach "$OLD_SHA" &&
    set_commit_line "$OLD_LINE" &&
    install_and_restart && wait_health "${OLD_LINE#IRIS_COMMIT=}"; then
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
OLD_SHORT="$(git -C "$APP" rev-parse --short=12 "$OLD_SHA")"
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
NEW_SHORT="$(git -C "$APP" rev-parse --short=12 "$NEW_SHA")"

if [ "$NEW_SHA" = "$OLD_SHA" ]; then
  log "$REF is already deployed ($NEW_SHORT); verifying health only"
  wait_health "$NEW_SHORT" || die "unhealthy on $NEW_SHORT; nothing to roll back to"
  log "ready, auth on (commit $NEW_SHORT)"
  exit 0
fi

log "deploying $NEW_SHORT (was $OLD_SHORT)"
# From here a kill rolls back instead of leaving the new commit. After a hangup the terminal is
# gone, so HUP switches output to the update log before anything is printed.
trap 'to_update_log; rollback "interrupted (hangup)"' HUP
trap 'rollback "interrupted"' INT TERM
if git -C "$APP" checkout -q -f --detach "$NEW_SHA" &&
  set_commit_line "IRIS_COMMIT=$NEW_SHORT" &&
  install_and_restart && wait_health "$NEW_SHORT"; then
  trap - HUP INT TERM
  log "ready, auth on (commit $NEW_SHORT)"
  exit 0
fi
rollback "deploy of $NEW_SHORT failed"
