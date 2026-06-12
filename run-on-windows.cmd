@echo off
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
  powershell -ExecutionPolicy Bypass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
)

uv run streamlit run streamlit_app.py --server.address localhost --browser.gatherUsageStats false
pause
