# IEEE Literature Research

The literature-research workflow is independent from `/search` and should be run by one worker
only, because concurrent workers cannot safely share one Chromium browser profile.

1. Install the optional dependencies and browser:

   ```powershell
   python -m pip install -e ".[dev,literature]"
   python -m playwright install chromium
   ```

2. Copy `.env.literature.example` values into the untracked `.env` file and set the database,
   LLM, and browser-profile values locally. Never commit API keys, browser cookies, or profiles.

3. Apply migrations and start the API, literature worker, and frontend in separate terminals:

   ```powershell
   docker compose up -d postgres qdrant
   python -m alembic upgrade head
   python -m uvicorn app.main:app --app-dir backend --reload
   python scripts/run_literature_worker.py
   npm --prefix frontend run dev -- --host 0.0.0.0
   ```

The first authorized IEEE use must run with `LITERATURE_HEADLESS=false`, so the user can finish
institutional login in the displayed browser. Once the profile is available to the dedicated worker
account, the worker may run headlessly. Use `--once`, `--run-id <UUID>`, and `--json` for one-shot
or scheduled execution.
