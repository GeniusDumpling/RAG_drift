# syntax=docker/dockerfile:1

FROM node:20-alpine AS frontend-builder
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=prod \
    API_HOST=0.0.0.0 \
    API_PORT=8000 \
    FRONTEND_DIST_DIR=/app/frontend/dist \
    QDRANT_COLLECTION=content_chunks_v2 \
    EMBEDDING_PROVIDER=sentence-transformers \
    EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5 \
    EMBEDDING_DIMENSION=512 \
    HF_HOME=/models/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/models/huggingface \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    LLM_PROVIDER=fake \
    AGENT_TIMEOUT_SECONDS=20 \
    WORKER_POLL_INTERVAL_SECONDS=2

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml alembic.ini README.md ./
COPY backend ./backend
COPY worker ./worker
COPY scripts ./scripts
COPY skills ./skills
COPY model-cache/huggingface /models/huggingface
RUN python -m pip install --upgrade pip \
    && python -m pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && python -m pip install '.[local-embeddings]'

COPY --from=frontend-builder /frontend/dist ./frontend/dist
COPY docker/entrypoint.sh /usr/local/bin/intelligence-rag-entrypoint
RUN sed -i 's/\r$//' /usr/local/bin/intelligence-rag-entrypoint \
    && chmod +x /usr/local/bin/intelligence-rag-entrypoint

EXPOSE 8000
ENTRYPOINT ["intelligence-rag-entrypoint"]
CMD ["api"]
