#!/usr/bin/env bash
# Seed the database and launch the API + prototype UI.
# Data source follows $OSRS_DATA_SOURCE (default: dataset — the committed bank).
# To refresh Grand Exchange prices from the live wiki API first:
#   OSRS_DATA_SOURCE=wiki ./run.sh
set -euo pipefail
cd "$(dirname "$0")"

python3 -m pip install -q -r requirements.txt
python3 -m app.seed
echo "Starting ScapeMaster on http://127.0.0.1:8000 ..."
exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
