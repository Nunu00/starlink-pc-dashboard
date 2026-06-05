@echo off
title Starlink Debug Dashboard Launcher
echo ===================================================
echo   STARLINK DEBUG DASHBOARD LAUNCHER
echo ===================================================
echo.
echo Starting dashboard server...
cd /d "%~dp0dashboard"
python star_debug_server.py %*
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Failed to start the dashboard server.
    echo Please ensure Python 3 is installed and in your PATH.
    echo Also verify that dependencies are installed:
    echo   pip install grpcio
    echo.
    pause
)
