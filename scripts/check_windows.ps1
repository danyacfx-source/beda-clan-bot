$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Error "Не найдено .venv. Создайте окружение: py -3.11 -m venv .venv"
}

& ".venv\Scripts\python.exe" scripts\preflight.py
& ".venv\Scripts\python.exe" -m pytest -q
& ".venv\Scripts\ruff.exe" check .
& ".venv\Scripts\mypy.exe" app rewrite --no-incremental
& ".venv\Scripts\python.exe" -m compileall -q app rewrite tests scripts
Write-Host "Полный Windows QA завершён успешно." -ForegroundColor Green