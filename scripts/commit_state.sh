#!/usr/bin/env bash
# Commit data/jobs.db + data/jobs.json + docs/index.html and push with conflict recovery.
# Usage: COMMIT_MSG="scan run" BRANCH=main bash scripts/commit_state.sh
set -euo pipefail

BRANCH="${BRANCH:?BRANCH is required}"
COMMIT_MSG="${COMMIT_MSG:?COMMIT_MSG is required}"
MAX_RETRIES="${MAX_RETRIES:-5}"
BACKOFF_BASE="${BACKOFF_BASE:-5}"
RUN_DB="${RUNNER_TEMP:-/tmp}/yc-jobs-db-run.sqlite"
STATE_PATHS="data/jobs.db data/jobs.json docs/index.html"

git config user.name "yc-job-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

cp data/jobs.db "$RUN_DB"
git add $STATE_PATHS
git commit -m "$COMMIT_MSG" || true

rebuild_from_run_db() {
  echo "Conflict/push race — reset to origin/$BRANCH, restore this run's DB, regenerate dashboard"
  git rebase --abort 2>/dev/null || true
  git reset --hard "origin/$BRANCH"
  cp "$RUN_DB" data/jobs.db
  python src/dashboard.py
  git add $STATE_PATHS
  git commit -m "$COMMIT_MSG (rebuilt after conflict)" || true
}

for attempt in $(seq 1 "$MAX_RETRIES"); do
  git fetch origin "$BRANCH"

  if git pull --rebase origin "$BRANCH"; then
    if git push origin "HEAD:$BRANCH"; then
      echo "Pushed on attempt $attempt"
      exit 0
    fi
    echo "Push rejected on attempt $attempt"
    rebuild_from_run_db
  else
    echo "Rebase conflict on attempt $attempt"
    rebuild_from_run_db
  fi

  sleep_time=$((BACKOFF_BASE * (2 ** (attempt - 1)) + (RANDOM % 5)))
  echo "Retrying in ${sleep_time}s (attempt $attempt/$MAX_RETRIES)..."
  sleep "$sleep_time"
done

echo "Failed to push after $MAX_RETRIES attempts" >&2
exit 1
