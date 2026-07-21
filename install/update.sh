#!/usr/bin/env bash
# OpenRAG update script.
#
# Pulls the latest code for a branch/tag from the configured git remote and
# redeploys the Docker Compose stack in place. Intended to run on the same
# host and out of the same checkout that install/install.sh deployed.
#
# Usage:
#   sudo ./install/update.sh                 # pull origin/main, rebuild, redeploy
#   sudo ./install/update.sh --ref v1.2.0     # deploy a specific branch or tag
#   sudo ./install/update.sh --no-build       # restart without rebuilding images
#
# The target directory must already be a git checkout (the way `git clone`
# leaves it). install/install.sh does not create one on its own when it is
# handed a plain file copy; run `git init && git remote add origin <url> &&
# git fetch && git reset <commit>` once beforehand if you need to adopt an
# existing non-git deployment (this leaves the working tree untouched and
# just teaches git about it).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="deploy/compose.yaml"

REMOTE="origin"
REF="main"
WEB_PORT_DEFAULT=5173
DO_BUILD=1
DO_PRUNE=1
FORCE=0
ASSUME_YES=0

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$1" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$1" >&2; exit 1; }

usage() {
  sed -n '2,19p' "${BASH_SOURCE[0]}"
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --remote) REMOTE="$2"; shift 2 ;;
    --remote=*) REMOTE="${1#*=}"; shift ;;
    --ref) REF="$2"; shift 2 ;;
    --ref=*) REF="${1#*=}"; shift ;;
    --no-build) DO_BUILD=0; shift ;;
    --no-prune) DO_PRUNE=0; shift ;;
    --force) FORCE=1; shift ;;
    -y|--yes) ASSUME_YES=1; shift ;;
    -h|--help) usage ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "run this script as root (e.g. with sudo)."
[[ -f "${REPO_DIR}/${COMPOSE_FILE}" ]] || die "${COMPOSE_FILE} not found under ${REPO_DIR}."

cd "${REPO_DIR}"

[[ -d .git ]] || die "$(cat <<MSG
${REPO_DIR} is not a git checkout, so there is nothing to pull from GitHub.
If this directory was deployed by copying files (e.g. rsync) rather than
'git clone', adopt it once with:
  git init
  git remote add origin https://github.com/<owner>/openrag.git
  git fetch origin
  git reset \${REMOTE:-origin}/${REF}
That points git at the remote history without touching any files already on
disk, so the next 'git status' shows your real local differences.
MSG
)"

git remote get-url "$REMOTE" >/dev/null 2>&1 || die "git remote '${REMOTE}' is not configured. Run: git remote add ${REMOTE} <repo-url>"

if [[ -n "$(git status --porcelain)" && $FORCE -ne 1 ]]; then
  die "working tree has uncommitted changes; commit/stash them or pass --force to discard local edits (data/ and .env are untracked and unaffected either way)."
fi

log "Fetching ${REMOTE}"
git fetch --tags "$REMOTE"

CURRENT_SHA="$(git rev-parse --short HEAD)"
TARGET_SHA="$(git rev-parse --short "${REMOTE}/${REF}" 2>/dev/null || git rev-parse --short "${REF}")"

if [[ "$CURRENT_SHA" == "$TARGET_SHA" ]]; then
  log "Already at ${TARGET_SHA} (${REF}); nothing to pull. Redeploying anyway."
else
  log "Updating ${CURRENT_SHA} -> ${TARGET_SHA} (${REF})"
fi

if [[ $ASSUME_YES -ne 1 ]]; then
  [[ -t 0 ]] || die "refusing to proceed without a TTY; pass -y/--yes for non-interactive runs."
  read -r -p "Proceed? [y/N] " confirm
  [[ "$confirm" =~ ^[Yy]$ ]] || die "aborted by user."
fi

if [[ $FORCE -eq 1 ]]; then
  git checkout -f "$REF" 2>/dev/null || git checkout -f -B "$REF" "${REMOTE}/${REF}"
  git reset --hard "${REMOTE}/${REF}" 2>/dev/null || git reset --hard "$REF"
else
  git checkout "$REF" 2>/dev/null || git checkout -B "$REF" "${REMOTE}/${REF}"
  if git show-ref --verify --quiet "refs/remotes/${REMOTE}/${REF}"; then
    git merge --ff-only "${REMOTE}/${REF}"
  fi
fi

DEPLOYED_SHA="$(git rev-parse --short HEAD)"
[[ "$DEPLOYED_SHA" == "$TARGET_SHA" ]] || die "checkout resolved to ${DEPLOYED_SHA}, expected ${TARGET_SHA}; refusing to deploy an unexpected revision."

[[ -f .env ]] || die ".env is missing; run install/install.sh first to provision this host."

get_env_var() {
  local key="$1" default="$2"
  local line
  line="$(grep -E "^${key}=" .env | tail -n1 || true)"
  [[ -n "$line" ]] && printf '%s' "${line#*=}" || printf '%s' "$default"
}

WEB_PORT="$(get_env_var OPENRAG_WEB_PORT "$WEB_PORT_DEFAULT")"
EMBEDDING_BACKEND="$(get_env_var OPENRAG_EMBEDDING_BACKEND hash)"

COMPOSE_PROFILE_ARGS=()
[[ "$EMBEDDING_BACKEND" == "tei" ]] && COMPOSE_PROFILE_ARGS=(--profile ml)

if [[ $DO_BUILD -eq 1 ]]; then
  # api/web are the only two distinct images (every other backend-based
  # service shares api's image via the compose file's YAML anchor). Building
  # just these two and then running `up` without --build avoids Compose
  # rebuilding and re-unpacking the same shared image once per service.
  log "Rebuilding images (backend and frontend images are each built once and shared)"
  docker compose -f "$COMPOSE_FILE" "${COMPOSE_PROFILE_ARGS[@]}" build api web
fi

# Migrations must run from the newly built backend image. Running this before
# the build would execute the previous release's migration set.
log "Applying any pending database migrations"
docker compose -f "$COMPOSE_FILE" "${COMPOSE_PROFILE_ARGS[@]}" run --rm migrate

log "Restarting the OpenRAG stack"
docker compose -f "$COMPOSE_FILE" "${COMPOSE_PROFILE_ARGS[@]}" up -d

log "Waiting for the API to report ready"
ready=0
for _ in $(seq 1 60); do
  if curl --fail --silent --max-time 3 "http://127.0.0.1:${WEB_PORT}/readyz" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 5
done

if [[ $ready -ne 1 ]]; then
  warn "the stack did not report ready within 5 minutes after the update; inspect it with:"
  warn "  docker compose -f ${COMPOSE_FILE} ps"
  warn "  docker compose -f ${COMPOSE_FILE} logs --tail=200 migrate bootstrap api worker web"
  warn "roll back with: git reset --hard ${CURRENT_SHA} && docker compose -f ${COMPOSE_FILE} ${COMPOSE_PROFILE_ARGS[*]} up -d --build"
  exit 1
fi

log "OpenRAG is ready on 127.0.0.1:${WEB_PORT} at commit ${TARGET_SHA}"

if [[ $DO_PRUNE -eq 1 ]]; then
  log "Pruning dangling images and build cache to reclaim disk"
  docker image prune -f >/dev/null
  docker builder prune -f --filter until=168h >/dev/null 2>&1 || true
fi

log "Done"
cat <<SUMMARY

  Previous commit : ${CURRENT_SHA}
  Deployed commit  : ${TARGET_SHA} (${REF})

Useful commands from ${REPO_DIR}:
  docker compose -f ${COMPOSE_FILE} ps
  docker compose -f ${COMPOSE_FILE} logs -f api worker web
  git log --oneline -5
SUMMARY
