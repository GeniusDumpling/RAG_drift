.PHONY: install infra-up infra-down migrate test lint api worker-once frontend seed smoke

install:
	@if [ ! -x .venv/bin/python ] || ! .venv/bin/python -m pip --version >/dev/null 2>&1; then \
		rm -rf .venv; \
		python3 -m venv .venv || { \
			rm -rf .venv; \
			command -v uv >/dev/null 2>&1 && uv venv --seed --python python3 .venv || { \
				echo "Failed to create .venv with pip. On Debian/Ubuntu, install python3.12-venv or python3-venv and retry."; \
				exit 1; \
			}; \
		}; \
	fi
	.venv/bin/python -m pip install --upgrade pip
	.venv/bin/python -m pip install -e ".[dev]"

infra-up:
	docker compose up -d postgres qdrant

infra-down:
	docker compose down

migrate:
	alembic upgrade head

test:
	pytest -q

lint:
	ruff check backend worker scripts
	mypy backend worker

api:
	uvicorn app.main:app --app-dir backend --reload --host $${API_HOST:-0.0.0.0} --port $${API_PORT:-8000}

worker-once:
	python3 scripts/run_worker_once.py

frontend:
	cd frontend && npm run dev -- --host 0.0.0.0

seed:
	python3 scripts/seed_demo.py

smoke:
	bash scripts/smoke_demo.sh
