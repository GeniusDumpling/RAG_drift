#!/usr/bin/env bash
# 常态化定时采集：单次运行，由 cron/systemd timer 每 ~15 分钟触发一次。
# 每次轮换一个搜索主题 → 搜索 → 去重 → 最多入库 1 个新视频。
set -euo pipefail

cd "$(dirname "$0")/../../.."

# 代理：yt-dlp 下载视频流必需（仓库 .env 不含代理，这里显式兜底，可用环境变量覆盖）
export HTTPS_PROXY="${HTTPS_PROXY:-http://host.docker.internal:7890}"
export https_proxy="${https_proxy:-$HTTPS_PROXY}"
export HTTP_PROXY="${HTTP_PROXY:-$HTTPS_PROXY}"
export http_proxy="${http_proxy:-$HTTP_PROXY}"

export NO_PROXY="${NO_PROXY:+$NO_PROXY,}localhost,127.0.0.1,::1,qdrant,postgres,app"
export no_proxy="$NO_PROXY"

PY="${PY:-python3}"

# 主题轮换：每 2 小时（7200 秒）切换一个搜索主题，均匀覆盖
QUERIES=(
  "DJI drone"
  "drone GPS spoofing jamming"
  "drone fail crash flyaway GPS lost"
  "drone MAVLink telemetry security"
  "drone remote control hijacking"
)
INTERVAL=7200
N=${#QUERIES[@]}
IDX=$(( $(date +%s) / INTERVAL % N ))
QUERY="${QUERIES[$IDX]}"

echo "=== $(date '+%F %T') 采集开始 query=$QUERY ==="
"$PY" skills/video-crawler/scripts/search_and_ingest.py \
  --query "$QUERY" \
  --caption-only \
  --language en \
  --max-results 50 \
  --video-limit 30 \
  --json
