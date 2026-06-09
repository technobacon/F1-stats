#!/usr/bin/env bash
# Seed the database (offline ETL stand-in) and launch the API + prototype UI.
set -euo pipefail
cd "$(dirname "$0")"

python3 -m pip install -q -r requirements.txt
python3 -m app.seed
echo "Starting F1 StatGuesser on http://127.0.0.1:8000 ..."
exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
