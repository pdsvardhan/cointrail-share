#!/usr/bin/env bash
# Run the Ingest unit tests + golden corpus in a throwaway container (iter-18 HP2).
# Nothing touches the live database: tests build their own SQLite files under /tmp.
#   scripts/run-tests.sh [repo_dir] [extra pytest args...]
set -euo pipefail
REPO="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
shift || true
IMAGE="${INGEST_TEST_IMAGE:-ingest-ingest}"
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  IMAGE="python:3.12-slim"
  SETUP="pip install -q -r requirements.txt -r requirements-dev.txt"
else
  SETUP="pip install -q -r requirements-dev.txt"
fi
docker run --rm -v "$REPO":/src:ro -w /src \
  -e INGEST_DB=/tmp/never-used.db -e CARDS_CONFIG_PATH=/tmp/cards.json \
  --entrypoint sh "$IMAGE" -c "cp -r /src /work && cd /work && ${SETUP} 2>/dev/null; python -m pytest $*" \
  || exit $?
