#!/usr/bin/env bash
# Seed the database and launch the API + prototype UI.
# Data source follows $F1_DATA_SOURCE (default: synthetic). Use the real,
# cached, weekly Jolpica ETL with:  F1_DATA_SOURCE=jolpica ./run.sh
set -euo pipefail
cd "$(dirname "$0")"

python3 -m pip install -q -r requirements.txt
python3 -m app.seed
echo "Starting GridMaster on http://127.0.0.1:8000 ..."
exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
