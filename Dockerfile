# The API, built from the REPOSITORY ROOT so the server can run the same sessionizer the
# uploader runs: hook-delivered transcripts (docs/hooks-capture.md) are cut into sessions
# with `capture/` + `analysis/` + `scripts/measure_boundaries.py` + `spec/strip.v1.json`.
# `server/Dockerfile` is the older server-only build and stays valid for a service whose
# root directory is `server/`; point Railway's root directory at `/` to use this one.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app:/app/server

WORKDIR /app

COPY server/requirements.txt server/requirements.txt
RUN pip install -r server/requirements.txt

COPY server/ server/
COPY capture/ capture/
COPY analysis/ analysis/
COPY scripts/measure_boundaries.py scripts/measure_boundaries.py
COPY spec/strip.v1.json spec/strip.v1.json
COPY privacy/ privacy/

WORKDIR /app/server
CMD ["sh", "-c", "uvicorn builder.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
