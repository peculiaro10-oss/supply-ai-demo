@echo off
cd /d "%~dp0android"

call gradlew.bat assembleDebug

if errorlevel 1 (
    echo.
    echo BUILD FAILED
    pause
    exit /b 1
)

echo.
echo BUILD SUCCESSFUL
echo APK:
echo %~dp0android\app\build\outputs\apk\debug\app-debug.apk
pause