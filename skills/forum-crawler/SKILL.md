---
name: dji-lito-demo-ingestion
description: Use when demonstrating /root/intelligence-rag, exposing the demo frontend, importing DJI BBS lito threads or comments, or showing how to inspect/search the ingested demo corpus.
---

# DJI Lito Demo Ingestion

## Overview

This project demo shows two effects: public forum data ingestion with raw snapshot preservation, and evidence-backed RAG retrieval over the ingested corpus. Read this skill before running or explaining the `intelligence-rag` demo.

## Safety and Scope

- Use only public, unauthenticated DJI BBS endpoints.
- Do not bypass login, captcha, anti-bot controls, or rate limits.
- Default demo scope is small: latest 5 threads and up to 20 comments per thread.
- Treat vector retrieval as environment-dependent. On this development host, Qdrant is not running, so demo retrieval mainly uses keyword fallback with Evidence objects.

## Current Demo URLs

- Public frontend: `http://62.234.15.249/`
- Health: `http://62.234.15.249/health`
- Local API behind proxy: `http://127.0.0.1:18000`

If public serving is down, rebuild the frontend with same-origin API calls and start the demo proxy:

```bash
cd /root/intelligence-rag/frontend
VITE_API_BASE_URL= npm run build
cd /root/intelligence-rag
nohup python3 skills/dji-lito-demo-ingestion/scripts/demo_proxy.py \
  --host 0.0.0.0 --port 80 \
  --dist /root/intelligence-rag/frontend/dist \
  --api http://127.0.0.1:18000 \
  > /root/logs/intel-rag-frontend-demo-80.log 2>&1 &
```

## Ingest DJI Lito Threads and Comments

Run from the project root:

```bash
cd /root/intelligence-rag
.venv/bin/python skills/dji-lito-demo-ingestion/scripts/ingest_dji_lito.py \
  --series lito \
  --thread-limit 5 \
  --reply-limit 20 \
  --json
```

The script:

1. Calls DJI public APIs:
   - thread list: `/api/v2/forum/thread/list`
   - thread detail: `/api/v2/forum/thread/info/{tid}`
   - comments: `/api/v2/forum/thread/{tid}/reply`
2. Creates or reuses Source and Job records.
3. Creates a Run with observable events.
4. Preserves raw API JSON in `raw_pages.raw_json`.
5. Normalizes threads as `item_type=thread` and replies as `item_type=comment`.
6. Builds chunks for RAG retrieval.

## Verify After Import

Use the script JSON output for `source_id` and `run_id`, then verify:

```bash
curl -fsS http://127.0.0.1:18000/runs/$RUN_ID | python3 -m json.tool
curl -fsS "http://127.0.0.1:18000/contents?source_site_id=$SOURCE_ID&limit=10" | python3 -m json.tool
curl -fsS -X POST http://127.0.0.1:18000/search \
  -H 'Content-Type: application/json' \
  -d "{\"query\":\"报送 固件\",\"mode\":\"search\",\"filters\":{\"source_site_id\":\"$SOURCE_ID\"},\"top_k\":3}" | python3 -m json.tool
```

A good demo run has `status=success`, nonzero `parsed_count`, nonzero `chunked_count`, and search Evidence for DJI content.

## Frontend Demo Path

1. Open `http://62.234.15.249/`.
2. Go to `运行记录 Runs`, paste the Run ID, and show counters plus Event Timeline.
3. Go to `内容详情 Content`, inspect imported threads/comments and Raw page lineage.
4. Go to `检索问答 Search` and use space-separated demo queries:
   - `报送 固件`
   - `lito 起飞 恭喜`
   - `模拟器 飞控`
5. Click Evidence content links to show the source item, chunks, and raw snapshot.

## Known Demo Limitation

The fake query optimizer splits mostly on spaces. For Chinese keyword retrieval, prefer separated keywords such as `报送 固件 评论` instead of a long natural sentence. This is a demo limitation, not a storage failure.
