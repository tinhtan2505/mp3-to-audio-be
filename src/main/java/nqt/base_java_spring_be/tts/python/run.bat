@echo off
chcp 65001 > nul
echo --- KIEM TRA MOI TRUONG ---

REM 1. Kiem tra Python
python --version > nul 2>&1
IF ERRORLEVEL 1 (
    echo [LOI] Khong tim thay Python! Vui long cai dat Python [tick chon Add to PATH] va thu lai.
    pause
    exit /b
)

echo --- BAT DAU CAI DAT ---

REM 2. Tu dong xoa venv neu phat hien bi loi (thieu file activate)
IF EXIST "venv" (
    IF NOT EXIST "venv\Scripts\activate.bat" (
        echo [THONG BAO] Thu muc venv bi loi, dang tu dong xoa de tao lai...
        rmdir /s /q "venv"
    )
)

REM 3. Tao venv neu chua co
IF NOT EXIST "venv" (
    echo Tao moi truong ao [venv]...
    python -m venv venv
)

REM 4. Kiem tra ky lai xem venv da tao thanh cong chua
IF NOT EXIST "venv\Scripts\activate.bat" (
    echo [LOI] Khong the tao venv. Kiem tra lai quyen truy cap hoac cai dat Python.
    pause
    exit /b
)

echo Kich hoat venv...
call venv\Scripts\activate
IF ERRORLEVEL 1 (
    echo [LOI] Khong the kich hoat moi truong ao.
    pause
    exit /b
)

echo Cai dat/Cap nhat thu vien...
pip install -r requirements.txt
IF ERRORLEVEL 1 (
    echo [LOI] Gap loi khi cai dat thu vien. Kiem tra ket noi mang.
    pause
    exit /b
)

echo --- KHOI DONG SERVER VITS ---
echo Server se chay tai: http://localhost:8008
echo Nhan Ctrl+C de dung server.
echo.

python main.py

pause