# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70 AS http-base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp \
    PYTHONPATH=/app/engine OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
WORKDIR /app
COPY requirements/api.txt requirements/api.txt
RUN python -m pip install --no-cache-dir -r requirements/api.txt
COPY services/ services/
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/readyz', timeout=4)"

FROM http-base AS catalog
ENV CATALOG_DB=/app/data/catalog/chemicals.sqlite3
COPY engine/catalog_db.py engine/catalog_db.py
COPY data/catalog/chemicals.sqlite3 data/catalog/chemicals.sqlite3
RUN chmod -R a+rX /app
USER 1001:0
CMD ["python", "-m", "uvicorn", "services.catalog_api:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--limit-concurrency", "32", "--no-access-log"]

FROM http-base AS web
ENV CATALOG_URL=http://catalog:8080 PREDICTOR_URL=http://predictor:8080
USER 1001:0
CMD ["python", "-m", "uvicorn", "services.web_api:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--limit-concurrency", "32", "--no-access-log"]

FROM http-base AS ml-base
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY engine/ engine/
RUN python -m pip install --no-cache-dir -r engine/requirements-benchmark.txt

FROM ml-base AS predictor
ENV MODEL_RUN_DIR=/app/runs/benchmark-v1
COPY runs/benchmark-v1/model_bundle.joblib runs/benchmark-v1/run_manifest.json runs/benchmark-v1/
RUN chmod -R a+rX /app
USER 1001:0
CMD ["python", "-m", "uvicorn", "services.predictor_api:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--limit-concurrency", "8", "--no-access-log"]

FROM ml-base AS pipeline
HEALTHCHECK NONE
USER 1001:0
ENTRYPOINT ["python"]
CMD ["engine/benchmark.py", "--help"]
