.PHONY: help install lint format typecheck test-unit test-integration test docker-up docker-down docker-logs migrate run-api slack-tunnel parse eval

help:
	@echo "Available commands:"
	@echo "  make install           Install dependencies using uv workspace"
	@echo "  make lint              Run ruff linter & formatter check"
	@echo "  make format            Auto-format with ruff"
	@echo "  make typecheck         Run mypy strict type checker"
	@echo "  make test-unit         Run unit test suite"
	@echo "  make test-integration  Run integration test suite"
	@echo "  make test              Run all tests"
	@echo "  make parse             Parse a procurement request CLI: make parse TEXT=\"...\""
	@echo "  make eval              Run golden LLM evaluation benchmark"
	@echo "  make docker-up         Start backing services and API via Docker Compose"
	@echo "  make docker-down       Stop Docker Compose services"
	@echo "  make docker-logs       Follow Docker Compose logs"
	@echo "  make migrate           Apply Alembic database migrations"
	@echo "  make run-api           Run FastAPI app locally with hot reload"
	@echo "  make slack-tunnel      Start local HTTPS tunnel for Slack webhooks"

install:
	uv sync --all-packages

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy packages services tests

test-unit:
	uv run pytest tests/unit -m unit

test-integration:
	uv run pytest tests/integration -m integration

test:
	uv run pytest

docker-up:
	docker compose -f infra/docker-compose.yml up -d --build

docker-down:
	docker compose -f infra/docker-compose.yml down

docker-logs:
	docker compose -f infra/docker-compose.yml logs -f

migrate:
	uv run alembic -c infra/alembic/alembic.ini upgrade head

parse:
	uv run python -m workers.cli parse "$(TEXT)"

eval:
	uv run pytest -m llm tests/eval/test_golden_eval.py -s

run-api:
	uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

slack-tunnel:
	@echo "Starting local HTTPS tunnel for Slack webhooks on port 8000..."
	@if command -v cloudflared >/dev/null 2>&1; then \
		cloudflared tunnel --url http://localhost:8000; \
	elif command -v ngrok >/dev/null 2>&1; then \
		ngrok http 8000; \
	else \
		echo "Tunnel tool not found. Run: ngrok http 8000 or cloudflared tunnel --url http://localhost:8000"; \
	fi
