# Portable container for the F1 StatGuesser prototype.
# Works on any Docker host (Render, Railway, Fly.io, Hugging Face Spaces, ...).
FROM python:3.11-slim

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY frontend/ frontend/

# Ephemeral, writable DB location; the app self-seeds on first boot.
ENV F1_DB_PATH=/tmp/f1stats.db
WORKDIR /app/backend

EXPOSE 8000
# Honour the host-provided $PORT (defaults to 8000 locally).
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
