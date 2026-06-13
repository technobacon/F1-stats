#!/usr/bin/env sh
# Production entrypoint that makes the SQLite database durable on an ephemeral
# host (e.g. Render's free tier, whose filesystem is wiped on every redeploy and
# cold start). It wraps uvicorn with Litestream: restore the latest snapshot from
# object storage on boot, then stream every change back to it while the app runs.
#
# Durability is OPT-IN and degrades gracefully: with no replica bucket configured
# (or no litestream binary present) this just runs uvicorn directly, so local
# dev, Docker and the test suite are completely unaffected. Set the bucket env
# vars (see README → "Free durable accounts") to turn it on.
set -eu
cd "$(dirname "$0")"

DB="${F1_DB_PATH:-/tmp/f1stats.db}"
PORT="${PORT:-8000}"
CONFIG="${LITESTREAM_CONFIG:-litestream.yml}"
export F1_DB_PATH="$DB"   # so litestream.yml's ${F1_DB_PATH} resolves

UVICORN="uvicorn app.main:app --host 0.0.0.0 --port $PORT"

# Locate the litestream binary: an explicit override, then $PATH, then the
# build-downloaded copy under ./bin (the Render native-runtime layout).
LITESTREAM="${LITESTREAM_BIN:-}"
if [ -z "$LITESTREAM" ]; then
  if command -v litestream >/dev/null 2>&1; then
    LITESTREAM="litestream"
  elif [ -x "./bin/litestream" ]; then
    LITESTREAM="./bin/litestream"
  fi
fi

# No replica configured -> ephemeral run (fine for demos / local / Docker hosts
# with their own persistent disk).
if [ -z "${LITESTREAM_REPLICA_BUCKET:-}" ]; then
  echo "[start] No LITESTREAM_REPLICA_BUCKET set — running with an ephemeral DB."
  exec $UVICORN
fi

# Replica wanted but no binary found: don't fail the boot, but make the missing
# durability loud in the logs rather than silently losing accounts.
if [ -z "$LITESTREAM" ]; then
  echo "[start] WARNING: LITESTREAM_REPLICA_BUCKET is set but no litestream binary"
  echo "[start]          was found — running WITHOUT durability. Check the build."
  exec $UVICORN
fi

# Fresh container: pull the latest snapshot back before the app opens the DB.
# -if-replica-exists makes the very first boot (empty bucket) a clean no-op.
if [ ! -f "$DB" ]; then
  echo "[start] Restoring $DB from replica (if one exists)…"
  "$LITESTREAM" restore -if-replica-exists -config "$CONFIG" "$DB" || \
    echo "[start] No replica to restore (first run) — starting fresh."
fi

# Replicate continuously and run uvicorn as a managed child. On SIGTERM (Render's
# graceful spin-down) Litestream flushes a final snapshot before exiting.
echo "[start] Starting uvicorn under Litestream replication → ${LITESTREAM_REPLICA_BUCKET}."
exec "$LITESTREAM" replicate -config "$CONFIG" -exec "$UVICORN"
