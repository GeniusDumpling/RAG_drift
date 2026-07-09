#!/usr/bin/env bash
# 每三小时抓取 DJI Lito 最新帖子（5 个帖子，每个最多 20 条回复）
set -e
cd /home/admin/.openclaw/workspace/RAG_drift

# 绕过代理（DJI API 直连更快）
no_proxy="bbs.dji.com,bbs.djicdn.com,djicdn.com" \
http_proxy="" \
https_proxy="" \
.venv/bin/python skills/dji-lito-demo-ingestion/scripts/ingest_dji_lito.py \
  --series lito \
  --thread-limit 5 \
  --reply-limit 20 \
  --json \
  >> /home/admin/logs/dji_lito_cron.log 2>&1

echo "--- $(date) ---" >> /home/admin/logs/dji_lito_cron.log
