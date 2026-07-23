@echo off
TITLE Home Hunter - Progressive Local Scraper
setlocal
cd /d "%~dp0"

color 0E

echo ============================================================
echo   HOME HUNTER - DANG QUET TIN MOI (PROGRESSIVE)
echo ============================================================
echo.

if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
) else if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
)

echo [System] Bat dau quet tung trang va cap nhat Map Viewer ngay.
echo.

python -m src.local.run_local_v2 --headless
set "SCRAPER_EXIT=%ERRORLEVEL%"

echo.
if not "%SCRAPER_EXIT%"=="0" (
    echo ------------------------------------------------------------
    echo [LOI] Scraper dung voi ma loi %SCRAPER_EXIT%.
    echo Kiem tra log phia tren; khong danh dau la da quet xong.
    echo ------------------------------------------------------------
    pause
    exit /b %SCRAPER_EXIT%
)

echo ------------------------------------------------------------
echo [XONG] Da quet xong! Kiem tra Telegram hoac Map Viewer.
echo ------------------------------------------------------------
pause
