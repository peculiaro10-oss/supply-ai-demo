@echo off
setlocal
if not defined JAVA_HOME set "JAVA_HOME=C:\Program Files\Eclipse Adoptium\jdk-21.0.12.101-hotspot"
cd /d "%~dp0"

if not exist "%JAVA_HOME%\bin\java.exe" goto :jdk_error
if not exist "%JAVA_HOME%\bin\jlink.exe" goto :jdk_error
"%JAVA_HOME%\bin\java.exe" -version 2>&1 | findstr /c:"21.0." >nul
if errorlevel 1 goto :jdk_error

if not exist "node_modules\@capacitor\cli\bin\capacitor" (
    echo Restoring npm dependencies because node_modules is missing...
    call npm.cmd ci
    if errorlevel 1 goto :failed
)

call npm.cmd run android:prepare
if errorlevel 1 goto :failed

set "APK_PATH=%~dp0android\app\build\outputs\apk\debug\app-debug.apk"
if exist "%APK_PATH%" del /q "%APK_PATH%"
cd android
call gradlew.bat "-Dorg.gradle.java.home=%JAVA_HOME%" assembleDebug
if errorlevel 1 goto :failed
if not exist "%APK_PATH%" goto :missing_apk

echo.
echo Fresh APK: %APK_PATH%
for %%I in ("%APK_PATH%") do echo Built: %%~tI   Size: %%~zI bytes
certutil -hashfile "%APK_PATH%" SHA256
echo Build complete.
pause
exit /b 0

:jdk_error
echo ERROR: Set JAVA_HOME to a complete JDK 21 installation before building.
goto :failed

:missing_apk
echo ERROR: Gradle completed without producing %APK_PATH%

:failed
set "BUILD_EXIT=%ERRORLEVEL%"
if "%BUILD_EXIT%"=="0" set "BUILD_EXIT=1"
echo.
echo APK build failed. Review the error above.
pause
exit /b %BUILD_EXIT%
