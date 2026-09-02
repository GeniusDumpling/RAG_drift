#!/usr/bin/env bash
# 论坛常态化增量采集：单次运行，由 cron/systemd timer 每 ~2 小时触发一次。
# 按时间轮换搜索主题，调用 discourse_ingest.py；该脚本内部基于
#   {source_id}:{canonical_url}:post:{content_hash} 去重，
# 因此重复帖子会自动跳过，只需把新命中的帖子增量入库。
set -euo pipefail

cd "$(dirname "$0")/../../.."

PY="${PY:-$PWD/.venv/bin/python}"

# 主题轮换：每 7200 秒（2 小时）切换一个，覆盖 型号/零件/漏洞/安全。
# 注意：Discourse 全文检索对冗长组合词/含特殊字符的词命中很差（如
# "drone firmware vulnerability CVE" → 0）；用短的高命中概念词更稳。
QUERIES=(
  "GPS spoofing"
  "GPS jamming"
  "MAVLink security"
  "EKF failsafe"
  "ESC failure"
  "flyaway"
  "compass calibration"
  "firmware vulnerability"
  "RTK"
  "propeller"
)
INTERVAL=7200
N=${#QUERIES[@]}
IDX=$(( $(date +%s) / INTERVAL % N ))
QUERY="${QUERIES[$IDX]}"
FORUMS="ardupilot px4"
MAX_RESULTS=10

echo "=== $(date '+%F %T') 论坛采集开始 query=$QUERY forums=$FORUMS ==="
"$PY" "$PWD/skills/forum-crawler/scripts/discourse_ingest.py" \
  --query "$QUERY" \
  --forums $FORUMS \
  --max-results "$MAX_RESULTS" \
  --request-delay 0.3 \
  --json