# Omnea Windows PowerShell Task Runner
# Usage: .\run.ps1 <command> [args]

param (
    [Parameter(Position=0)]
    [string]$Command = "help",

    [Parameter(Position=1, ValueFromRemainingArguments=$true)]
    [string[]]$Args
)

$VenvPython = ".\.venv\Scripts\python.exe"
$VenvPytest = ".\.venv\Scripts\pytest.exe"
$VenvRuff   = ".\.venv\Scripts\ruff.exe"
$VenvMypy   = ".\.venv\Scripts\mypy.exe"
$VenvAlembic= ".\.venv\Scripts\alembic.exe"
$VenvUvicorn= ".\.venv\Scripts\uvicorn.exe"

switch ($Command.ToLower()) {
    "help" {
        Write-Host "Omnea Windows Developer Commands:" -ForegroundColor Cyan
        Write-Host "  .\run.ps1 lint             Run ruff check & format check"
        Write-Host "  .\run.ps1 format           Auto-format with ruff"
        Write-Host "  .\run.ps1 typecheck        Run mypy type checker"
        Write-Host "  .\run.ps1 test             Run all pytest tests"
        Write-Host "  .\run.ps1 test-eval        Run Phase 3 entity resolution & ablation benchmarks"
        Write-Host "  .\run.ps1 migrate          Run alembic upgrade head"
        Write-Host "  .\run.ps1 run-api          Run FastAPI app locally"
        Write-Host "  .\run.ps1 docker-up        Start docker compose containers"
        Write-Host "  .\run.ps1 docker-down      Stop docker compose containers"
    }
    "lint" {
        & $VenvRuff check .
        & $VenvRuff format --check .
    }
    "format" {
        & $VenvRuff check --fix .
        & $VenvRuff format .
    }
    "typecheck" {
        & $VenvMypy packages\procure_core packages\procure_context services\api\src\api\routes\review.py
    }
    "test" {
        & $VenvPytest -v $Args
    }
    "test-eval" {
        & $VenvPytest -v tests\eval\test_embedding_ablation.py tests\eval\test_entity_resolution_benchmark.py
    }
    "migrate" {
        & $VenvAlembic -c infra/alembic/alembic.ini upgrade head
    }
    "run-api" {
        & $VenvUvicorn api.main:app --reload --host 0.0.0.0 --port 8000
    }
    "docker-up" {
        docker.exe compose -f infra/docker-compose.yml up -d
    }
    "docker-down" {
        docker.exe compose -f infra/docker-compose.yml down
    }
    default {
        Write-Warning "Unknown command '$Command'. Run .\run.ps1 help for available commands."
    }
}
