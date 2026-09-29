@echo off
setlocal
if not exist ".venv\Scripts\python.exe" (
  echo Не найдено .venv. Создайте окружение: py -3.11 -m venv .venv
  exit /b 1
)
call .venv\Scripts\python.exe scripts\preflight.py
if errorlevel 1 exit /b 1
call .venv\Scripts\python.exe -m pytest -q
if errorlevel 1 exit /b 1
call .venv\Scripts\ruff.exe check .
if errorlevel 1 exit /b 1
call .venv\Scripts\mypy.exe app rewrite --no-incremental
if errorlevel 1 exit /b 1
call .venv\Scripts\python.exe -m compileall -q app rewrite tests scripts
if errorlevel 1 exit /b 1
echo Полный Windows QA завершен успешно.