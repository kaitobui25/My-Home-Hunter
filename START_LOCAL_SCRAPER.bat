@echo off
TITLE Home Hunter - Local Scraper
setlocal
cd /d "%~dp0"

:: Set color to yellow on black
color 0E

echo ============================================================
echo   🔍 HOME HUNTER - DANG QUET TIN MOI (LOCAL)
echo ============================================================
echo.

:: Detect virtual environment
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
) else if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
)

echo [System] Dang bat dau qua trinh quet...
echo          (Trinh duyet se mo ra de vuot WAF)
echo.

:: Run Scraper
python -m src.local.run_local

echo.
echo ------------------------------------------------------------
echo [XONG] Da quet xong! Kiem tra Telegram hoac Map Viewer.
echo ------------------------------------------------------------
pause
