#!/usr/bin/env bash
# Redeploy FrankensteinCentral from the latest pushed code.
#
# Runs on the OptiPlex (the box that hosts the stack). The GitHub Actions
# self-hosted runner calls this on every push; you can also run it by hand.
#
#   FRANKENSTEIN_DIR   where the repo is cloned (default: $HOME/FrankensteinCentral)
#   $1                 branch to deploy (default: whatever is checked out)
set -euo pipefail

DIR="${FRANKENSTEIN_DIR:-$HOME/FrankensteinCentral}"
cd "$DIR"

BRANCH="${1:-$(git rev-parse --abbrev-ref HEAD)}"

# Support both Docker Compose v2 ("docker compose") and the older v1
# ("docker-compose"), so the pipeline works whatever the box has installed.
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  echo "!! Neither 'docker compose' nor 'docker-compose' is installed."
  echo "!! Install it with:  sudo apt-get install -y docker-compose-plugin"
  exit 1
fi

echo "==> Deploying '$BRANCH' from $DIR (using: $DC)"

# Pull the exact pushed code. reset --hard leaves untracked files (like .env
# and docker volumes) alone, so your secrets and data survive.
git fetch --prune origin "$BRANCH"
git checkout "$BRANCH" 2>/dev/null || git checkout -b "$BRANCH" "origin/$BRANCH"
git reset --hard "origin/$BRANCH"

if [ ! -f .env ]; then
  echo "!! No .env found in $DIR — copy .env.example to .env and fill it in."
  echo "!! Bringing the stack up anyway with built-in defaults / sample data."
fi

# Build changed images and (re)start everything. --remove-orphans cleans up
# any services that were removed from the compose file.
$DC up -d --build --remove-orphans

# Keep disk tidy — drop dangling images from old builds.
docker image prune -f >/dev/null 2>&1 || true

echo "==> Deployed $(git rev-parse --short HEAD) on '$BRANCH'"
docker compose ps
