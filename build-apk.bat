@echo off

set "JAVA_HOME=C:\Program Files\Eclipse Adoptium\jdk-21.0.12.101-hotspot"
set "PATH=%JAVA_HOME%\bin;%PATH%"

cd /d "%~dp0android"

call gradlew.bat --stop
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