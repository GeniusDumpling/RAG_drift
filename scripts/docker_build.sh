#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_REF="${1:-${INTELLIGENCE_RAG_IMAGE:-intelligence-rag-app:latest}}"
MODEL_CACHE_DIR="$ROOT_DIR/model-cache/huggingface/models--BAAI--bge-small-zh-v1.5"

if [[ ! -d "$MODEL_CACHE_DIR" ]]; then
  cat >&2 <<EOF
Missing local embedding model cache: $MODEL_CACHE_DIR

Download it before building the deploy image:
  HF_HOME="$ROOT_DIR/model-cache/huggingface" \
  SENTENCE_TRANSFORMERS_HOME="$ROOT_DIR/model-cache/huggingface" \
  "$ROOT_DIR/.venv/bin/python" -c 'from huggingface_hub import snapshot_download; snapshot_download(repo_id="BAAI/bge-small-zh-v1.5", cache_dir="model-cache/huggingface")'

The deploy image vendors this cache so offline target machines can use local 512-dimensional embeddings directly.
EOF
  exit 1
fi

printf 'Building Intelligence RAG image: %s\n' "$IMAGE_REF"
docker build -t "$IMAGE_REF" "$ROOT_DIR"
printf 'Built image: %s\n' "$IMAGE_REF"
