#!/usr/bin/env bash
# Independent history cursor; reuse the pipeline's global lock and processing stage.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
echo "=== $(date '+%F %T') forum historical backfill ==="
# Later arguments override the incremental defaults in the shared wrapper.
# Periodic refresh belongs to the incremental task, not the historical scan.
exec /bin/bash "$SCRIPT_DIR/run_scheduled_discourse.sh" --mode backfill --refresh-limit 0 "$@"
