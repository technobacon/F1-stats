# Portable container for the F1 StatGuesser prototype.
# Works on any Docker host (Render, Railway, Fly.io, Hugging Face Spaces, ...).
FROM python:3.11-slim

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Litestream: streams the SQLite DB to S3-compatible object storage so accounts
# survive an ephemeral filesystem. Engaged only when a replica bucket is
# configured (see start.sh); otherwise the app runs with a plain local DB.
ADD https://github.com/benbjohnson/litestream/releases/download/v0.3.13/litestream-v0.3.13-linux-amd64.tar.gz /tmp/litestream.tar.gz
RUN tar -xzf /tmp/litestream.tar.gz -C /usr/local/bin litestream && rm /tmp/litestream.tar.gz

COPY backend/ backend/
COPY frontend/ frontend/

# Ephemeral, writable DB location; the app self-seeds on first boot and
# Litestream makes it durable when a replica is configured.
ENV F1_DB_PATH=/tmp/f1stats.db
WORKDIR /app/backend

EXPOSE 8000
# start.sh honours $PORT and wraps uvicorn with Litestream when configured.
CMD ["./start.sh"]
