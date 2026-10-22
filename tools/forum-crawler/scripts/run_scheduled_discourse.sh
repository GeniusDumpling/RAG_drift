#!/usr/bin/env bash
# One bounded capture (incremental by default) + independent processing pass.
# No runtime dependency installation. Host and Docker use the same entrypoint.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"
if [[ -z "${PY:-}" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then PY="$ROOT/.venv/bin/python"
  else PY="$(command -v python3 || command -v python)"; fi
fi

exec 8>"${FORUM_LOCK_FILE:-/tmp/rag-forum-pipeline.lock}"
flock -n 8 || { echo 'Forum pipeline already running; skipped'; exit 0; }
echo "=== $(date '+%F %T') forum capture ==="
capture_status=0
"$PY" "$ROOT/tools/forum-crawler/scripts/discourse_ingest.py" \
  --forums ardupilot px4 --mode incremental \
  --max-pages "${FORUM_MAX_PAGES:-10}" --max-topics "${FORUM_MAX_TOPICS:-20}" \
  --refresh-limit "${FORUM_REFRESH_LIMIT:-3}" \
  --request-delay "${FORUM_REQUEST_DELAY:-1}" --json "$@" || capture_status=$?

# Process existing archives even when discovery or one forum failed this run.
echo "=== $(date '+%F %T') forum archive processing ==="
process_args=(--forums ardupilot px4 --limit "${FORUM_PROCESS_LIMIT:-20}" --json)
[[ "${FORUM_EMBED:-0}" != 1 ]] || process_args+=(--embed --embed-limit "${FORUM_EMBED_LIMIT:-200}")
process_status=0
"$PY" "$ROOT/tools/forum-crawler/scripts/discourse_process.py" "${process_args[@]}" || process_status=$?
if (( capture_status != 0 || process_status != 0 )); then
  echo "Forum pipeline failed/partial: capture=$capture_status processing=$process_status" >&2
  exit 1
fi
