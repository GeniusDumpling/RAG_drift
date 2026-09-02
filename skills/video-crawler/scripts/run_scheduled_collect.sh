#!/usr/bin/env bash
# 常态化定时采集：单次运行，由 cron/systemd timer 每 ~15 分钟触发一次。
# 每次轮换一个搜索主题 → 搜索 → 去重 → 最多入库 1 个新视频。
set -euo pipefail

cd "$(dirname "$0")/../../.."

# 代理：yt-dlp 下载视频流必需（仓库 .env 不含代理，这里显式兜底，可用环境变量覆盖）
export HTTPS_PROXY="${HTTPS_PROXY:-http://127.0.0.1:7897}"
export https_proxy="${https_proxy:-$HTTPS_PROXY}"

UV=/home/gaowei/.local/bin/uv

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
"$UV" run --extra video-keyframes --extra local-embeddings python3 \
  skills/video-crawler/scripts/search_and_ingest.py \
  --query "$QUERY" \
  --caption-only \
  --language en \
  --max-results 50 \
  --video-limit 30 \
  --json