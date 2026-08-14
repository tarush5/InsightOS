#!/usr/bin/env bash
# Bring InsightOS up locally and prove it works.
#
# Generates the demo warehouse if missing, loads it into SQLite so the data
# source path is exercised against a real database rather than CSVs, applies
# migrations, starts the API, and runs the end-to-end smoke test.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API="$ROOT/apps/api"
STATE="${INSIGHTOS_STATE_DIR:-$ROOT/.local}"
PORT="${PORT:-8000}"

mkdir -p "$STATE"

export ENV=local
export DATABASE_URL="sqlite+aiosqlite:///$STATE/insightos.db"
export AUTH_SECRET="${AUTH_SECRET:-local-dev-secret-not-for-production-use-32ch}"
export SEED_DIR="$ROOT/seed"
export LLM_PROVIDER="${LLM_PROVIDER:-none}"
# Credentials are resolved by reference; the DSN never reaches the database.
export INSIGHTOS_SECRET_WAREHOUSE="sqlite+aiosqlite:///$STATE/warehouse.db"

if [ ! -f "$SEED_DIR/orders.csv" ]; then
  echo "==> Generating the demo warehouse (a few minutes)"
  python3 "$ROOT/scripts/seed_data.py" --out "$SEED_DIR"
fi

if [ ! -f "$STATE/warehouse.db" ]; then
  echo "==> Loading the CSVs into SQLite"
  python3 - "$SEED_DIR" "$STATE/warehouse.db" <<'PY'
import os, sqlite3, sys
import pandas as pd
seed, target = sys.argv[1], sys.argv[2]
con = sqlite3.connect(target)
for name in ("orders", "customers", "support_tickets", "products", "order_items",
             "refunds", "marketing_campaigns", "inventory", "subscriptions"):
    path = os.path.join(seed, f"{name}.csv")
    if os.path.exists(path):
        pd.read_csv(path).to_sql(name, con, index=False, if_exists="replace")
con.commit(); con.close()
PY
fi

echo "==> Applying migrations"
(cd "$API" && python3 -m alembic upgrade head >/dev/null)

echo "==> Starting the API on :$PORT"
(cd "$API" && python3 -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" \
  --log-level warning) &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT

for _ in $(seq 1 40); do
  curl -sf "http://127.0.0.1:$PORT/api/v1/health" >/dev/null && break
  sleep 1
done

echo "==> Running the end-to-end smoke test"
python3 "$ROOT/scripts/smoke.py" --base-url "http://127.0.0.1:$PORT"
RESULT=$?

echo
echo "The API is still running at http://127.0.0.1:$PORT (docs at /docs)."
echo "Press Ctrl-C to stop it."
wait $SERVER
exit $RESULT
