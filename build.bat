@echo off
setlocal
chcp 65001 >nul

cd /d "%~dp0"

set "APP_NAME=FH6Auto"
set "MAIN_FILE=main.py"
set "py=py"

echo.
echo ========================================
echo   Starting FH6Auto build
echo ========================================
echo.

if not exist "%MAIN_FILE%" (
    echo [ERROR] %MAIN_FILE% was not found.
    pause
    exit /b 1
)

%py% --version >nul 2>nul
if errorlevel 1 (
    echo [ERROR] py was not found in PATH.
    pause
    exit /b 1
)

echo [1/4] Checking py dependencies...
%py% -c "import customtkinter, cv2, numpy, pyautogui, pydirectinput, requests, pynput, PIL, win32gui" >nul 2>nul
if errorlevel 1 (
    echo [INFO] Missing dependencies detected. Installing requirements.txt...
    %py% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [ERROR] Failed to install dependencies.
        echo Run this manually, then build again:
        echo %py% -m pip install -r requirements.txt
        pause
        exit /b 1
    )
)

%py% -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo [INFO] PyInstaller is missing. Installing PyInstaller...
    %py% -m pip install pyinstaller
    if errorlevel 1 (
        echo.
        echo [ERROR] Failed to install PyInstaller.
        echo Run this manually, then build again:
        echo %py% -m pip install pyinstaller
        pause
        exit /b 1
    )
)

echo [2/4] Cleaning previous build output...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
mkdir build
mkdir build\%APP_NAME%
mkdir dist

echo [3/4] Verifying source syntax...
%py% -m py_compile "%MAIN_FILE%"
if errorlevel 1 (
    echo.
    echo [ERROR] py syntax check failed.
    pause
    exit /b 1
)

echo [4/4] Running PyInstaller...
%py% -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --uac-admin ^
    --name "%APP_NAME%" ^
    --icon "assets\icon.ico" ^
    --add-data "images;images" ^
    --add-data "assets;assets" ^
    --collect-all customtkinter ^
    --workpath "build" ^
    --distpath "dist" ^
    --specpath "." ^
    "%MAIN_FILE%"

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed.
    pause
    exit /b 1
)

echo.
echo [DONE] Build succeeded: dist\%APP_NAME%.exe
pause
