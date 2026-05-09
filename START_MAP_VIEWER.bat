@echo off
TITLE Home Hunter - Map Viewer
setlocal
cd /d "%~dp0"

:: Set color to bright green on black
color 0A

echo ============================================================
echo   🏠 HOME HUNTER - TRUNG TAM DIEU KHIEN
echo ============================================================
echo.
echo [1/3] Dang kiem tra moi truong Python...

:: Detect virtual environment
if exist venv\Scripts\activate.bat (
    echo [2/3] Da tim thay venv. Dang kich hoat...
    call venv\Scripts\activate.bat
) else if exist .venv\Scripts\activate.bat (
    echo [2/3] Da tim thay .venv. Dang kich hoat...
    call .venv\Scripts\activate.bat
) else (
    echo [2/3] Khong tim thay venv, su dung Python he thong.
)

:: Open browser
echo [3/3] Dang mo ban do tai: http://localhost:5001
echo       (Vui long cho vai giay de server khoi dong)
start "" "http://localhost:5001"

echo.
echo ------------------------------------------------------------
echo Server dang chay... 
echo DE TAT: Hay dong cua so nay hoac nhan Ctrl+C.
echo ------------------------------------------------------------
echo.

:: Run Flask
python -m src.web.app

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [LOI] Co loi xay ra khi chay Flask server.
    echo Kiem tra xem ban da cai dat: pip install -r requirements.txt chua?
    pause
)
