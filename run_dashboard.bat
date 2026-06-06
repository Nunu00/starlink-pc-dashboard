@echo off
title Starlink Debug Dashboard Launcher
echo ===================================================
echo   STARLINK DEBUG DASHBOARD LAUNCHER
echo ===================================================
echo.
echo Starting dashboard server...
cd /d "%~dp0"
uv run dashboard/star_debug_server.py %*
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Failed to start the dashboard server.
    echo Please ensure uv is installed: https://docs.astral.sh/uv/getting-started/installation/
    echo.
    pause
)
