.PHONY: install infra-up infra-down migrate test lint api worker-once frontend seed smoke

PY := .venv/bin/python
PYTEST := .venv/bin/pytest
ALEMBIC := .venv/bin/alembic
UVICORN := .venv/bin/uvicorn
RUFF := .venv/bin/ruff
MYPY := .venv/bin/mypy

install:
	@if [ ! -x $(PY) ] || ! $(PY) -m pip --version >/dev/null 2>&1; then \
		rm -rf .venv; \
		python3 -m venv .venv || { \
			rm -rf .venv; \
			command -v uv >/dev/null 2>&1 && uv venv --seed --python python3 .venv || { \
				echo "Failed to create .venv with pip. On Debian/Ubuntu, install python3.12-venv or python3-venv and retry."; \
				exit 1; \
			}; \
		}; \
	fi
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"

infra-up:
	docker compose up -d postgres qdrant

infra-down:
	docker compose down

migrate:
	$(ALEMBIC) upgrade head

test:
	$(PYTEST) -q

lint:
	$(RUFF) check backend worker scripts
	$(MYPY) backend worker

api:
	$(UVICORN) app.main:app --app-dir backend --reload --host $${API_HOST:-0.0.0.0} --port $${API_PORT:-8000}

worker-once:
	$(PY) scripts/run_worker_once.py

frontend:
	cd frontend && npm run dev -- --host 0.0.0.0

seed:
	$(PY) scripts/seed_demo.py

smoke:
	bash scripts/smoke_demo.sh
