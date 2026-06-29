#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_REF="${1:-${INTELLIGENCE_RAG_IMAGE:-intelligence-rag-app:latest}}"
SAFE_IMAGE_REF="${IMAGE_REF//\//_}"
SAFE_IMAGE_REF="${SAFE_IMAGE_REF//:/_}"
OUTPUT_PATH="${2:-$ROOT_DIR/dist/docker/${SAFE_IMAGE_REF}.tar}"

mkdir -p "$(dirname "$OUTPUT_PATH")"
printf 'Saving Docker image %s to %s\n' "$IMAGE_REF" "$OUTPUT_PATH"
docker save "$IMAGE_REF" -o "$OUTPUT_PATH"
printf 'Saved Docker image archive: %s\n' "$OUTPUT_PATH"
