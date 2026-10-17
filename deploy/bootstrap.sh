#!/usr/bin/env bash
# Provision a fresh Ubuntu LTS box for the Iris server. Run once as root; safe to re-run.
#
#   IRIS_REPO_URL=git@github.com:<org>/iris.git bash bootstrap.sh
#
# Environment:
#   IRIS_REPO_URL    required. SSH URL of the repo (private, read-only deploy key).
#   IRIS_DEPLOY_KEY  path to the deploy private key (default /root/.ssh/iris_deploy).
#   IRIS_REF         branch, tag or commit to deploy (default main).
#   IRIS_PUBLIC_IP   skip public IP detection.
#
# The script stops loudly while IRIS_API_KEY in /etc/iris.env is empty. Set it, re-run.
set -euo pipefail

: "${IRIS_REPO_URL:?set IRIS_REPO_URL to the repo SSH URL}"
IRIS_DEPLOY_KEY="${IRIS_DEPLOY_KEY:-/root/.ssh/iris_deploy}"
IRIS_REF="${IRIS_REF:-main}"

APP=/opt/iris
MODELS=$APP/models
LOGS=$APP/logs
VENV=$APP/.venv
ENV_FILE=/etc/iris.env
DEPLOY_DIR=$APP/deploy

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }

log() { printf '==> %s\n' "$*"; }

log "packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3-venv python3-pip curl ca-certificates gnupg \
  debian-keyring debian-archive-keyring apt-transport-https libgl1 libglib2.0-0

if ! command -v caddy >/dev/null 2>&1; then
  log "caddy"
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
# Code is root-owned and read-only to iris; only models and logs are writable by it.
install -d -m 755 "$APP"
install -d -m 755 -o iris -g iris "$MODELS"
install -d -m 750 -o iris -g iris "$LOGS"

log "repo"
export GIT_SSH_COMMAND="ssh -i $IRIS_DEPLOY_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
if [ -d "$APP/.git" ]; then
  git -C "$APP" fetch --tags origin
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
COMMIT="$(git -C "$APP" rev-parse --short HEAD)"
log "deploying commit $COMMIT"

log "venv (CPU torch first)"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q torch torchvision --index-url https://download.pytorch.org/whl/cpu
"$VENV/bin/pip" install -q -r "$APP/server/requirements.txt"
"$VENV/bin/pip" install -q -r "$APP/server/requirements-ml.txt"

log "stock weights in $MODELS"
if [ ! -f "$MODELS/yolo26s.pt" ]; then
  # The service runs from $MODELS, so the stock fallback finds this file and never
  # downloads into some other cwd.
  runuser -u iris -- env HOME=/var/lib/iris bash -c \
    "cd '$MODELS' && '$VENV/bin/python' -c \"from ultralytics import YOLO; YOLO('yolo26s.pt')\""
fi
[ -f "$MODELS/yolo26s.pt" ] || { echo "yolo26s.pt was not placed in $MODELS" >&2; exit 1; }

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

if ! grep -Eq '^IRIS_API_KEY=.+' "$ENV_FILE"; then
  cat >&2 <<MSG

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
  IRIS_API_KEY is EMPTY in $ENV_FILE.
  The server would accept frames from anyone. Bootstrap will NOT finish.
  Set it, then re-run this script, for example:
    sed -i "s|^IRIS_API_KEY=.*|IRIS_API_KEY=\$(openssl rand -hex 24)|" $ENV_FILE
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
MSG
  exit 1
fi

log "caddy"
IP="${IRIS_PUBLIC_IP:-$(curl -fsS4 https://ipv4.icanhazip.com | tr -d '[:space:]')}"
case "$IP" in
  *[!0-9.]* | "") echo "bad public IP: '$IP'" >&2; exit 1 ;;
esac
HOST="${IP//./-}.nip.io"
sed "s|__HOST__|$HOST|" "$DEPLOY_DIR/Caddyfile" >/etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile

log "services"
install -m 644 "$DEPLOY_DIR/iris.service" /etc/systemd/system/iris.service
systemctl daemon-reload
systemctl enable iris caddy
systemctl restart iris
systemctl reload-or-restart caddy

log "waiting up to 60 s for /health"
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/health | grep -Eq '"ready": *true'; then
    log "ready: https://$HOST/health (commit $COMMIT)"
    exit 0
  fi
  sleep 1
done
echo "server did not report ready; see: journalctl -u iris -n 100" >&2
exit 1
