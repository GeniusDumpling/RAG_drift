# IEEE Literature Research

The literature-research workflow is independent from `/search` and should be run by one worker
only, because concurrent workers cannot safely share one Chromium browser profile.

文献数据单独保存在同一 PostgreSQL 实例的 `literature_research` 数据库，主库
`intelligence_rag` 不再保存 `literature_*` 表。文献 API 和 worker 共用独立连接池。
默认沿用主库的服务器和凭据，仅替换数据库名；可以用 `LITERATURE_DATABASE_URL`
及 `LITERATURE_SYNC_DATABASE_URL` 显式指定连接。

全新 Compose 数据卷会通过 `docker/postgres-init-literature.sh` 自动创建文献数据库。
已有数据库请先停止 API 和文献 worker，执行以下迁移，不要直接删除旧表：

```bash
.venv/bin/python scripts/migrate_literature_database.py                 # 查看迁移目标
.venv/bin/python scripts/migrate_literature_database.py --apply --writers-stopped
.venv/bin/alembic upgrade head
.venv/bin/alembic -n literature upgrade head
```

脚本要求目标库不存在，并要求源库、目标库使用同一服务器和账号。它会锁定源文献表的写入，
用同一快照备份和复制，比较每条记录的完整 SHA-256（包含 PDF 二进制）、字段、外键、
约束及索引；通过后才在事务中删除原库的六张表。备份和校验清单保存在被忽略的
`data/backups/literature-<UTC时间>/`，数据库客户端默认使用 `intelligence-rag-postgres-deploy`
容器。可用 `--postgres-container` 和 `--backup-dir` 指定其他容器和备份目录。

主库迁移 `0008_literature_database_split` 遇到尚有数据的旧文献表会拒绝继续，防止漏迁。
主库和文献库各自维护 `alembic_version`；Docker API 启动时会分别升级两个数据库。
数据库浏览页只展示主库表，文献记录及产物继续通过原有文献接口访问。

1. Install the optional dependencies and browser:

   ```powershell
   python -m pip install -e ".[dev,literature]"
   python -m playwright install chromium
   ```

2. Copy `.env.example` to the untracked `.env` file, then enable and set the literature-research
   variables for the database, LLM, and browser profile locally. Never commit API keys, browser
   cookies, or profiles.

3. Apply migrations and start the API, literature worker, and frontend in separate terminals:

   ```powershell
   docker compose up -d postgres qdrant
   python -m alembic upgrade head
   python -m alembic -n literature upgrade head
   python -m uvicorn app.main:app --app-dir backend --reload
   python scripts/run_literature_worker.py
   npm --prefix frontend run dev -- --host 0.0.0.0
   ```

The first authorized IEEE use must run with `LITERATURE_HEADLESS=false`, so the user can finish
institutional login in the displayed browser. Once the profile is available to the dedicated worker
account, the worker may run headlessly. Use `--once`, `--run-id <UUID>`, and `--json` for one-shot
or scheduled execution.
