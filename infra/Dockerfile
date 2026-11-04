# One image for the ingest service and every Python tool (seed, fleet, feeds, ops, tests).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/ingest:/app/feeds:/app/ops:/app/fleet:/app/db

WORKDIR /app
COPY requirements.txt /app/requirements.txt
# Optional extra CA (corporate TLS proxy): set EXTRA_CA_FILE in .env. Not baked into the image.
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install --no-cache-dir -r /app/requirements.txt

COPY pyproject.toml /app/
COPY db /app/db
COPY fleet /app/fleet
COPY ingest /app/ingest
COPY feeds /app/feeds
COPY ops /app/ops
COPY flows /app/flows

EXPOSE 8000
CMD ["uvicorn", "ingest.main:app", "--host", "0.0.0.0", "--port", "8000"]
