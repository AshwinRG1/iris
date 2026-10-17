#!/usr/bin/env bash
# Provision a fresh box for the Iris server. Run once as root; safe to re-run.
# The image must be Ubuntu 24.04 LTS or newer (python3 3.12+, like CI).
#
#   IRIS_REPO_URL=https://github.com/AshwinRG1/iris.git bash bootstrap.sh
#
# Environment:
#   IRIS_REPO_URL    required. HTTPS URL of the repo (public), or an SSH URL with IRIS_DEPLOY_KEY if the repo is ever made private.
#   IRIS_DEPLOY_KEY  path to the deploy private key, only used with an SSH IRIS_REPO_URL (default /root/.ssh/iris_deploy).
#   IRIS_REF         branch, tag or commit to deploy (default main).
#   IRIS_PUBLIC_IP   skip public IP detection.
#
# The script stops loudly while IRIS_API_KEY in /etc/iris.env is missing, short or duplicated.
# Set it, re-run.
set -euo pipefail

: "${IRIS_REPO_URL:?set IRIS_REPO_URL to the repo URL}"
IRIS_DEPLOY_KEY="${IRIS_DEPLOY_KEY:-/root/.ssh/iris_deploy}"
IRIS_REF="${IRIS_REF:-main}"

# Pinned installs. torch and torchvision match the shared dev venv.
PIP_VERSION=26.2.1
TORCH_VERSION=2.14.1+cpu
TORCHVISION_VERSION=0.29.1+cpu
YOLO26S_URL=https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26s.pt
YOLO26S_SHA256=646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b

APP=/opt/iris
MODELS=$APP/models
LOGS=$APP/logs
VENV=$APP/.venv
ENV_FILE=/etc/iris.env
DEPLOY_DIR=$APP/deploy

log() { printf '==> %s\n' "$*"; }
die() { printf '%s\n' "$*" >&2; exit 1; }

# True only for four dot-separated octets, each 0-255.
is_ipv4() {
  local IFS=. o
  local -a octets
  read -ra octets <<<"$1"
  [ "${#octets[@]}" -eq 4 ] || return 1
  for o in "${octets[@]}"; do
    [[ "$o" =~ ^[0-9]{1,3}$ ]] && [ "$((10#$o))" -le 255 ] || return 1
  done
}

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

[ "$(id -u)" -eq 0 ] || die "run as root"
command -v python3 >/dev/null 2>&1 || die "python3 not found; use Ubuntu 24.04 LTS or newer"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' ||
  die "python3 is older than 3.12; use Ubuntu 24.04 LTS or newer"

log "packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3-venv python3-pip curl ca-certificates gnupg \
  debian-keyring debian-archive-keyring apt-transport-https libgl1 libglib2.0-0

if ! command -v caddy >/dev/null 2>&1; then
  log "installing caddy"
  install -d -m 755 /usr/share/keyrings
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key |
    gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    -o /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq
  apt-get install -y -qq caddy
fi

log "iris user and directories"
id iris >/dev/null 2>&1 ||
  useradd --system --home-dir /var/lib/iris --create-home --shell /usr/sbin/nologin iris
# Code and models are root-owned and read-only to iris; only the log directory is writable.
install -d -m 755 "$APP"
install -d -m 755 -o root -g root "$MODELS"
install -d -m 750 -o iris -g iris "$LOGS"

log "repo"
# GitHub's published SSH host keys, fetched over TLS from the API, replace any github.com lines.
install -d -m 700 /root/.ssh
touch /root/.ssh/known_hosts
GH_KEYS="$(curl -fsS https://api.github.com/meta |
  python3 -c 'import json, sys; print("\n".join("github.com " + k for k in json.load(sys.stdin)["ssh_keys"]))')"
[ -n "$GH_KEYS" ] || die "could not read GitHub SSH host keys"
grep -v '^github\.com ' /root/.ssh/known_hosts >/root/.ssh/known_hosts.new || true
printf '%s\n' "$GH_KEYS" >>/root/.ssh/known_hosts.new
mv /root/.ssh/known_hosts.new /root/.ssh/known_hosts
export GIT_SSH_COMMAND="ssh -i $IRIS_DEPLOY_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/root/.ssh/known_hosts"
if [ -d "$APP/.git" ]; then
  git -C "$APP" fetch --tags --force --prune origin
else
  # $APP already exists (models, logs), so clone aside and move the .git dir in.
  rm -rf "$APP.clone"
  git clone "$IRIS_REPO_URL" "$APP.clone"
  mv "$APP.clone/.git" "$APP/.git"
  rm -rf "$APP.clone"
fi
git -C "$APP" checkout -q -f "$IRIS_REF"
# A branch follows origin; a tag or commit is already exact.
if git -C "$APP" show-ref -q --verify "refs/remotes/origin/$IRIS_REF"; then
  git -C "$APP" reset -q --hard "origin/$IRIS_REF"
fi
COMMIT="$(git -C "$APP" rev-parse --short=12 HEAD)"
log "deploying commit $COMMIT"

log "venv (CPU torch first)"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q "pip==$PIP_VERSION"
"$VENV/bin/pip" install -q "torch==$TORCH_VERSION" "torchvision==$TORCHVISION_VERSION" \
  --index-url https://download.pytorch.org/whl/cpu
"$VENV/bin/pip" install -q -r "$APP/server/requirements.txt"
"$VENV/bin/pip" install -q -r "$APP/server/requirements-ml.txt"

log "stock weights in $MODELS"
# Root-owned, so the service cannot rewrite its own weights. The service runs from $MODELS,
# so the stock fallback finds this file and never downloads into some other cwd.
# Never construct YOLO(...) here: that torch-loads the file as root before it is verified.
sha_ok() { echo "$YOLO26S_SHA256  $1" | sha256sum -c --quiet - 2>/dev/null; }
if [ ! -f "$MODELS/yolo26s.pt" ] || ! sha_ok "$MODELS/yolo26s.pt"; then
  tmp="$(mktemp "$MODELS/.yolo26s.XXXXXX")"
  if ! curl -fsSL "$YOLO26S_URL" -o "$tmp" || ! sha_ok "$tmp"; then
    rm -f "$tmp"
    die "yolo26s.pt download failed its sha256 check and was deleted"
  fi
  mv -f "$tmp" "$MODELS/yolo26s.pt"
fi
chown -R root:root "$MODELS"
chmod 755 "$MODELS"
chmod 644 "$MODELS"/*

log "env file"
if [ ! -f "$ENV_FILE" ]; then
  install -m 600 -o root -g root "$APP/server/.env.example" "$ENV_FILE"
fi
chmod 600 "$ENV_FILE"
# Record the deployed commit: replace the line if present, else append.
if grep -q '^IRIS_COMMIT=' "$ENV_FILE"; then
  sed -i "s|^IRIS_COMMIT=.*|IRIS_COMMIT=$COMMIT|" "$ENV_FILE"
else
  printf 'IRIS_COMMIT=%s\n' "$COMMIT" >>"$ENV_FILE"
fi

key_lines="$(grep -c '^IRIS_API_KEY=' "$ENV_FILE" || true)"
key_value="$(sed -n 's/^IRIS_API_KEY=//p' "$ENV_FILE" | head -n1)"
# systemd strips surrounding quotes, so judge the value the service will see.
key_value="${key_value#[\"\']}"
key_value="${key_value%[\"\']}"
if [ "$key_lines" -ne 1 ] || [ "${#key_value}" -lt 32 ]; then
  cat >&2 <<MSG

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
  IRIS_API_KEY in $ENV_FILE is empty, shorter than 32 characters, or set on
  more than one line (found $key_lines lines). Bootstrap will NOT finish.
  Keep exactly one line, then re-run this script, for example:
    sed -i "s|^IRIS_API_KEY=.*|IRIS_API_KEY=\$(openssl rand -hex 24)|" $ENV_FILE
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
MSG
  exit 1
fi

log "caddy"
IP="${IRIS_PUBLIC_IP:-$(curl -fsS4 https://ipv4.icanhazip.com | tr -d '[:space:]')}"
is_ipv4 "$IP" || die "bad public IP: '$IP'"
HOST="${IP//./-}.nip.io"
sed "s|__HOST__|$HOST|" "$DEPLOY_DIR/Caddyfile" >/etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile

log "services"
# Caddy's admin API listens on /run/caddy/admin.sock (see the Caddyfile), so the iris process
# cannot reach localhost:2019. The socket needs its directory; the packaged unit's
# `caddy reload --config /etc/caddy/Caddyfile` reads the admin address from that config.
install -d -m 755 /etc/systemd/system/caddy.service.d
printf '[Service]\nRuntimeDirectory=caddy\nRuntimeDirectoryMode=0750\n' \
  >/etc/systemd/system/caddy.service.d/iris-admin-socket.conf
install -m 644 "$DEPLOY_DIR/iris.service" /etc/systemd/system/iris.service
systemctl daemon-reload
systemctl enable iris caddy
systemctl restart iris
# Restart, not reload: the drop-in and the admin address only take effect on a restart.
systemctl restart caddy

log "waiting up to 60 s for /health"
for _ in $(seq 1 60); do
  body="$(curl -fsS http://127.0.0.1:8000/health 2>/dev/null || true)"
  state="$(health_state <<<"$body")"
  case "$state" in
    ready)
      log "ready, auth on: https://$HOST/health (commit $COMMIT)"
      exit 0
      ;;
    authoff) die "/health reports auth off; refusing to finish" ;;
    error:*) die "model failed to load (${state#error:}); see: journalctl -u iris -n 100" ;;
  esac
  sleep 1
done
die "server did not report ready; see: journalctl -u iris -n 100"
