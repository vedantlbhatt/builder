# Railway builds this with the DOCKERFILE builder.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so a code change does not reinstall the world.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Railway sets PORT. Single worker: the process is I/O bound on Postgres and the box is
# small; more workers would multiply connections without adding throughput.
CMD ["sh", "-c", "uvicorn builder.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
