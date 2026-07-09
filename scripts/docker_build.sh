#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_REF="${1:-${INTELLIGENCE_RAG_IMAGE:-intelligence-rag-app:latest}}"

printf 'Building Intelligence RAG image: %s\n' "$IMAGE_REF"
docker build -t "$IMAGE_REF" "$ROOT_DIR"
printf 'Built image: %s\n' "$IMAGE_REF"
