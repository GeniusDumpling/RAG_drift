#!/usr/bin/env bash
# 常态化定时采集：单次运行，由 cron/systemd timer 周期触发。
# 主题轮换、搜索/入库参数均来自 conf.yaml（scripts/conf.yaml）；
# 代理与 API Key 由 pipeline.py 从 conf.yaml / 仓库根 .env 注入。
set -euo pipefail

cd "$(dirname "$0")"

UV_BIN="${UV:-$(command -v uv || echo /home/gaowei/.local/bin/uv)}"

echo "=== $(date '+%F %T') 采集开始 ==="
# video-asr extra 仅在 conf.yaml 开启 whisper_fallback 时真正需要，这里一并带上
exec "$UV_BIN" run --extra video-keyframes --extra video-asr --extra local-embeddings python3 main.py --scheduled --json
