@echo off
TITLE Home Hunter - Optimized Map Viewer
setlocal
cd /d "%~dp0"

color 0A

echo ============================================================
echo   HOME HUNTER - TRUNG TAM DIEU KHIEN (OPTIMIZED)
echo ============================================================
echo.
echo [1/3] Dang kiem tra moi truong Python...

if exist venv\Scripts\activate.bat (
    echo [2/3] Da tim thay venv. Dang kich hoat...
    call venv\Scripts\activate.bat
) else if exist .venv\Scripts\activate.bat (
    echo [2/3] Da tim thay .venv. Dang kich hoat...
    call .venv\Scripts\activate.bat
) else (
    echo [2/3] Khong tim thay venv, su dung Python he thong.
)

echo [3/3] Dang mo ban do tai: http://localhost:5001
echo       Popup chi mo khi click; ket qua duoc sap xep gan truoc.
start "" "http://localhost:5001"

echo.
echo ------------------------------------------------------------
echo Server dang chay...
echo DE TAT: Dong cua so nay hoac nhan Ctrl+C.
echo ------------------------------------------------------------
echo.

python -m src.web.app_v2

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [LOI] Co loi xay ra khi chay Flask server.
    echo Kiem tra: pip install -r requirements.txt
    pause
)
