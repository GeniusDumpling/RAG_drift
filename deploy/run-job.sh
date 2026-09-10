#!/usr/bin/env bash
# One foreground Compose job; dependencies must already be running.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
job="${1:-}"
case "$job" in
  video-collect) command=(tools/video-crawler/scripts/run_scheduled_collect.sh); entrypoint=/bin/bash ;;
  discourse-collect) command=(tools/forum-crawler/scripts/run_scheduled_discourse.sh); entrypoint=/bin/bash ;;
  supplier-pipeline) command=(tools/supplier-information/pipeline.py); entrypoint=python ;;
  supplier-verify) command=(tools/supplier-information/supplier_verify.py); entrypoint=python ;;
  *) echo "Usage: $0 {video-collect|discourse-collect|supplier-pipeline|supplier-verify}" >&2; exit 64 ;;
esac
if [[ $# == 2 && "$2" == --validate ]]; then
  entrypoint=python
  command=(-c 'import pathlib, yaml, dotenv, trafilatura, yt_dlp, cv2, scenedetect, curl_cffi, faster_whisper, sqlalchemy, psycopg, qdrant_client; [compile(p.read_text(), str(p), "exec") for p in pathlib.Path("tools").rglob("*.py")]; print("Job dependencies and Python syntax OK (no network or ingestion)")')
elif [[ $# != 1 ]]; then
  echo 'Expected a job name and optional --validate' >&2; exit 64
fi
lock_dir="${JOB_LOCK_DIR:-$ROOT/.job-locks}"
mkdir -p -- "$lock_dir"
# Supplier verification and collection share mutable output files.
lock_job="$job"
[[ "$job" != supplier-* ]] || lock_job=supplier
exec 9>"$lock_dir/$lock_job.lock"
flock -n 9 || { echo "Job $job already running; skipped"; exit 0; }
name="${JOB_CONTAINER_PREFIX:-intelligence-rag}-$job"
# Capture ID and label together; never remove by name or delete foreign containers.
read -r token < /proc/sys/kernel/random/uuid
cleanup() {
  local status=$? record id owner
  trap - EXIT
  record="$(timeout 10s docker inspect --format '{{.Id}} {{index .Config.Labels "org.intelligence-rag.job-token"}}' "$name" 2>/dev/null)" || record=''
  read -r id owner <<< "$record" || true
  if [[ -n "$id" && "$owner" == "$token" ]]; then
    timeout 20s docker rm -f "$id" >/dev/null || echo "WARNING: cleanup failed for owned container $id" >&2
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# No dependency startup, API process, runtime installs, or host .env sourcing.
timeout --signal=TERM --kill-after=30s "${JOB_TIMEOUT:-2h}" \
  docker compose -f "${JOB_COMPOSE_FILE:-$ROOT/docker-compose.deploy.yml}" --profile jobs run \
  --rm --no-deps --name "$name" \
  --label "org.intelligence-rag.job-token=$token" \
  --entrypoint "$entrypoint" jobs "${command[@]}"
