#!/usr/bin/env bash
# OpenRAG demo-instance installer.
#
# Deploys the Docker Compose stack from this repository behind the host's
# existing nginx, and issues a Let's Encrypt certificate for the domain you
# pass in. Designed for a single-node Ubuntu server that already has nginx
# installed and a DNS A record pointing at the machine.
#
# Usage:
#   sudo ./install/install.sh \
#     --domain ragdemo.example.com \
#     --admin-email admin@example.com \
#     --admin-password 'change-me' \
#     --letsencrypt-email you@example.com
#
# Every value can also be supplied as an environment variable
# (OPENRAG_DOMAIN, OPENRAG_ADMIN_EMAIL, OPENRAG_ADMIN_PASSWORD,
# OPENRAG_LETSENCRYPT_EMAIL) or left out to be prompted for interactively.
# No domain is hardcoded in this script.

set -euo pipefail

# BuildKit attaches a fresh provenance/SBOM attestation to every build by
# default, so re-running `docker compose build` on completely unchanged
# source still produces a new image digest. Compose then recreates every
# container sharing that image tag (12+ backend services here) even when
# nothing changed, which is expensive and, on a single-vCPU host, can starve
# the API's own startup. Disabling default attestations makes a no-op build
# an actual no-op.
export BUILDX_NO_DEFAULT_ATTESTATIONS=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="deploy/compose.yaml"

DOMAIN="${OPENRAG_DOMAIN:-}"
ADMIN_EMAIL="${OPENRAG_ADMIN_EMAIL:-}"
ADMIN_PASSWORD="${OPENRAG_ADMIN_PASSWORD:-}"
LETSENCRYPT_EMAIL="${OPENRAG_LETSENCRYPT_EMAIL:-}"
WEB_PORT="${OPENRAG_WEB_PORT:-5173}"
EMBEDDING_BACKEND="${OPENRAG_EMBEDDING_BACKEND:-tei}"
SKIP_NGINX=0
SKIP_TLS=0
SKIP_DOCKER_INSTALL=0
ASSUME_YES=0

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$1" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$1" >&2; exit 1; }

usage() {
  sed -n '2,20p' "${BASH_SOURCE[0]}"
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    --domain=*) DOMAIN="${1#*=}"; shift ;;
    --admin-email) ADMIN_EMAIL="$2"; shift 2 ;;
    --admin-email=*) ADMIN_EMAIL="${1#*=}"; shift ;;
    --admin-password) ADMIN_PASSWORD="$2"; shift 2 ;;
    --admin-password=*) ADMIN_PASSWORD="${1#*=}"; shift ;;
    --letsencrypt-email) LETSENCRYPT_EMAIL="$2"; shift 2 ;;
    --letsencrypt-email=*) LETSENCRYPT_EMAIL="${1#*=}"; shift ;;
    --web-port) WEB_PORT="$2"; shift 2 ;;
    --web-port=*) WEB_PORT="${1#*=}"; shift ;;
    --embedding-backend) EMBEDDING_BACKEND="$2"; shift 2 ;;
    --embedding-backend=*) EMBEDDING_BACKEND="${1#*=}"; shift ;;
    --skip-nginx) SKIP_NGINX=1; shift ;;
    --skip-tls) SKIP_TLS=1; shift ;;
    --skip-docker-install) SKIP_DOCKER_INSTALL=1; shift ;;
    -y|--yes) ASSUME_YES=1; shift ;;
    -h|--help) usage ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "run this script as root (e.g. with sudo)."
[[ -f "${REPO_DIR}/${COMPOSE_FILE}" ]] || die "${COMPOSE_FILE} not found under ${REPO_DIR}; run this script from a cloned openrag checkout."

prompt() {
  local var_name="$1" message="$2" secret="${3:-0}"
  local current value
  current="$(eval "printf '%s' \"\${${var_name}}\"")"
  [[ -n "$current" ]] && return 0
  [[ -t 0 ]] || die "${var_name} is required; pass it as a flag or environment variable in non-interactive mode."
  if [[ "$secret" == "1" ]]; then
    read -r -s -p "${message}: " value; echo
  else
    read -r -p "${message}: " value
  fi
  [[ -n "$value" ]] || die "${var_name} must not be empty."
  eval "${var_name}=\"\${value}\""
}

prompt DOMAIN "Domain this instance will be served from (e.g. ragdemo.example.com)"
prompt ADMIN_EMAIL "Demo/admin login email"
prompt ADMIN_PASSWORD "Demo/admin login password" 1
[[ -z "$LETSENCRYPT_EMAIL" ]] && LETSENCRYPT_EMAIL="$ADMIN_EMAIL"

[[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || die "'${DOMAIN}' does not look like a valid domain name."
[[ "$ADMIN_EMAIL" =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]] || die "'${ADMIN_EMAIL}' does not look like a valid email address."
[[ ${#ADMIN_PASSWORD} -ge 12 ]] || die "the demo password should be at least 12 characters long."

log "Installer plan"
cat <<PLAN
  Repository directory : ${REPO_DIR}
  Domain                : ${DOMAIN}
  Admin/demo email      : ${ADMIN_EMAIL}
  Let's Encrypt email   : ${LETSENCRYPT_EMAIL}
  Web port (internal)   : ${WEB_PORT}
  Embedding backend     : ${EMBEDDING_BACKEND} (starts the Compose 'ml' profile when 'tei')
  Configure nginx site  : $([[ $SKIP_NGINX -eq 1 ]] && echo "no (--skip-nginx)" || echo "yes")
  Request TLS cert      : $([[ $SKIP_TLS -eq 1 ]] && echo "no (--skip-tls)" || echo "yes, via certbot --nginx")
  Install Docker Engine : $([[ $SKIP_DOCKER_INSTALL -eq 1 ]] && echo "no (--skip-docker-install)" || echo "if missing")
PLAN

if [[ $ASSUME_YES -ne 1 ]]; then
  [[ -t 0 ]] || die "refusing to proceed without a TTY; pass -y/--yes for non-interactive runs."
  read -r -p "Proceed? [y/N] " confirm
  [[ "$confirm" =~ ^[Yy]$ ]] || die "aborted by user."
fi

export DEBIAN_FRONTEND=noninteractive

log "Installing base packages (curl, ca-certificates, gnupg, git)"
apt-get update -qq
apt-get install -y -qq curl ca-certificates gnupg git >/dev/null

if [[ $SKIP_DOCKER_INSTALL -ne 1 ]] && ! command -v docker >/dev/null 2>&1; then
  log "Installing Docker Engine + Compose plugin"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
  systemctl enable --now docker
else
  command -v docker >/dev/null 2>&1 || die "docker is not installed and --skip-docker-install was set."
fi

docker compose version >/dev/null 2>&1 || die "docker compose (v2 plugin) is not available."

cd "${REPO_DIR}"

log "Preparing data directory and event-bus secret"
install -d -m 700 data
if [[ ! -f data/event_redis_password ]]; then
  openssl rand -hex -out data/event_redis_password 32
else
  log "data/event_redis_password already exists; leaving its contents unchanged"
fi
# Compose bind-mounts this file as-is (its host owner/mode carry straight into
# the container), but api/worker run as the non-root 'openrag' user. A
# root-only 0600 file is unreadable to them and crashes the API at startup
# with 'event_redis_password_file_unreadable'. The containing data/ directory
# is already 700 root-only, so 0644 here does not weaken host-level exposure.
chmod 644 data/event_redis_password

log "Preparing .env"
if [[ ! -f .env ]]; then
  cp .env.example .env
fi

set_env_var() {
  local key="$1" value="$2"
  local tmp
  tmp="$(mktemp)"
  grep -vE "^${key}=" .env > "$tmp" || true
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  mv "$tmp" .env
}

set_env_var OPENRAG_BOOTSTRAP_EMAIL "$ADMIN_EMAIL"
set_env_var OPENRAG_BOOTSTRAP_PASSWORD "$ADMIN_PASSWORD"
set_env_var OPENRAG_ENVIRONMENT "production"
set_env_var OPENRAG_WEB_PORT "$WEB_PORT"
set_env_var OPENRAG_EMBEDDING_BACKEND "$EMBEDDING_BACKEND"
chmod 600 .env

COMPOSE_PROFILE_ARGS=()
[[ "$EMBEDDING_BACKEND" == "tei" ]] && COMPOSE_PROFILE_ARGS=(--profile ml)

AVAILABLE_KB="$(df -Pk . | tail -n1 | awk '{print $4}')"
if [[ "$AVAILABLE_KB" -lt 15000000 ]]; then
  warn "only $((AVAILABLE_KB / 1024 / 1024))G free on this filesystem; the build below needs headroom for one ~1-2GB backend image and one small frontend image. Consider pruning old images/build cache (docker system prune) first."
fi

# Every backend-based service (api, worker, migrate, bootstrap, ...) shares one
# image via the compose file's YAML anchor (image: openrag-backend:local).
# Building only 'api' and 'web' produces both distinct images once; `up`
# without --build then reuses them for every other service. Passing --build
# to `up` directly would instead build once per *service*, repeatedly
# unpacking the same multi-hundred-MB image and can exhaust disk on small
# hosts for no benefit.
log "Building images (backend and frontend images are each built once and shared)"
docker compose -f "$COMPOSE_FILE" "${COMPOSE_PROFILE_ARGS[@]}" build api web

log "Starting infrastructure and the API (this can take several minutes on first run)"
# Starting every service in one `up -d` makes Compose launch all ~13
# containers at once, including 8 Celery workers that each import the same
# heavy ML stack (torch, docling, agno) as the API. On a modest host that
# starves the API of CPU during its own cold start for long enough to fail
# its health check and go 'unhealthy' -- which then permanently blocks `web`
# (depends_on: api healthy) until someone intervenes by hand. Starting the
# API on its own first, with no worker competing for CPU, avoids that.
docker compose -f "$COMPOSE_FILE" "${COMPOSE_PROFILE_ARGS[@]}" up -d \
  postgres redis event-redis qdrant minio migrate bootstrap authority-provisioner api

log "Waiting for the API to become healthy before starting workers"
api_ready=0
for _ in $(seq 1 60); do
  status="$(docker inspect openrag-api-1 --format '{{.State.Health.Status}}' 2>/dev/null || echo unknown)"
  [[ "$status" == "healthy" ]] && { api_ready=1; break; }
  [[ "$status" == "unhealthy" ]] && break
  sleep 5
done
if [[ $api_ready -ne 1 ]]; then
  warn "api did not become healthy; inspect it before continuing:"
  warn "  docker compose -f ${COMPOSE_FILE} logs --tail=200 api"
  die "aborting before starting workers and web against an unhealthy api."
fi

log "Starting workers and the web frontend"
docker compose -f "$COMPOSE_FILE" "${COMPOSE_PROFILE_ARGS[@]}" up -d

log "Waiting for the API to report ready"
ready=0
for _ in $(seq 1 90); do
  if curl --fail --silent --max-time 3 "http://127.0.0.1:${WEB_PORT}/readyz" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 10
done
if [[ $ready -ne 1 ]]; then
  warn "the stack did not report ready within 15 minutes; inspect it with:"
  warn "  docker compose -f ${COMPOSE_FILE} ps"
  warn "  docker compose -f ${COMPOSE_FILE} logs --tail=200 migrate bootstrap api worker web"
else
  log "OpenRAG is ready on 127.0.0.1:${WEB_PORT}"
fi

if [[ $SKIP_NGINX -ne 1 ]]; then
  command -v nginx >/dev/null 2>&1 || die "nginx was not found; install it first or pass --skip-nginx."

  log "Writing nginx site for ${DOMAIN}"
  SITE_FILE="/etc/nginx/sites-available/${DOMAIN}.conf"
  cat > "$SITE_FILE" <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};
    client_max_body_size 101m;

    location / {
        proxy_pass http://127.0.0.1:${WEB_PORT};
        proxy_http_version 1.1;
        proxy_request_buffering off;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "";
        # Chat responses stream over SSE; buffering would delay tokens.
        proxy_buffering off;
        proxy_read_timeout 300s;
    }
}
NGINX

  ln -sf "$SITE_FILE" "/etc/nginx/sites-enabled/${DOMAIN}.conf"
  nginx -t
  systemctl reload nginx

  if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
    ufw allow "Nginx Full" >/dev/null 2>&1 || true
  fi

  if [[ $SKIP_TLS -ne 1 ]]; then
    if ! command -v certbot >/dev/null 2>&1; then
      log "Installing certbot"
      apt-get install -y -qq certbot python3-certbot-nginx >/dev/null
    fi
    log "Requesting a Let's Encrypt certificate for ${DOMAIN}"
    certbot --nginx -d "$DOMAIN" -m "$LETSENCRYPT_EMAIL" --agree-tos --redirect --non-interactive
  else
    warn "skipped TLS issuance (--skip-tls); ${DOMAIN} is currently served over plain HTTP."
  fi
else
  warn "skipped nginx configuration (--skip-nginx); the app is only reachable on 127.0.0.1:${WEB_PORT}."
fi

log "Done"
cat <<SUMMARY

  URL      : https://${DOMAIN}
  Email    : ${ADMIN_EMAIL}
  Password : (as provided)

Next steps in the UI: sign in, register a completion model under
Superadmin -> Models (and Superadmin -> Embeddings if you want to switch
embedding profiles later), then create a workspace and upload a document.

Useful commands from ${REPO_DIR}:
  docker compose -f ${COMPOSE_FILE} ps
  docker compose -f ${COMPOSE_FILE} logs -f api worker web
  docker compose -f ${COMPOSE_FILE} down            # stop, keep data
SUMMARY
