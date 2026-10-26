#!/usr/bin/env bash
# Build/start an isolated frontend preview, or stop it and return to the unchanged app.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
compose=(docker compose --project-name rag-g6-preview -f "$ROOT/docker-compose.g6-preview.yml")
case "${1:-start}" in
  start)
    docker inspect intelligence-rag-app --format '{{.State.Running}}' | rg -qx true
    build_dir="$ROOT/data/g6-preview/build-context"
    mkdir -p "$build_dir/dist"
    # Record the exact existing runtime as evidence that the preview left it untouched.
    docker inspect intelligence-rag-app --format '{"container_id":{{json .Id}},"image":{{json .Config.Image}},"image_id":{{json .Image}}}' > "$ROOT/data/g6-preview/baseline.json"
    (
      cd "$ROOT/frontend"
      VITE_G6_PREVIEW=true VITE_API_BASE_URL='' npm run build
    )
    # Empty only this script-owned build directory to avoid obsolete hashed bundles.
    python3 - "$ROOT/frontend/dist" "$build_dir/dist" <<'PY'
from pathlib import Path
import shutil, sys
source, target = map(Path, sys.argv[1:])
if target.exists(): shutil.rmtree(target)
shutil.copytree(source, target)
PY
    cp "$ROOT/docker/g6-preview-server.py" "$build_dir/server.py"
    docker build -f "$ROOT/docker/Dockerfile.g6-preview" -t "${G6_PREVIEW_IMAGE:-rag-graph-g6-preview:20261023}" "$build_dir"
    "${compose[@]}" up -d --no-deps graph-preview
    echo "G6 preview: http://localhost:${G6_PREVIEW_PORT:-18000}/?view=VideoGraph"
    echo 'Existing app remains available on its original port. Roll back: bash scripts/g6-preview.sh rollback'
    ;;
  rollback|stop)
    "${compose[@]}" stop graph-preview
    echo 'Preview stopped. The existing app remains unchanged; open its original URL.'
    ;;
  status)
    "${compose[@]}" ps graph-preview
    ;;
  *) echo 'Usage: bash scripts/g6-preview.sh {start|status|rollback}'; exit 64 ;;
esac
