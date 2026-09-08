@echo off
setlocal EnableExtensions

cd /d "%~dp0"

rem -----------------------------------------------------------------------------
rem Java 21 validation
rem -----------------------------------------------------------------------------
if not defined JAVA_HOME set "JAVA_HOME=C:\Program Files\Eclipse Adoptium\jdk-21.0.12.101-hotspot"

echo Checking JDK 21...
echo JAVA_HOME=%JAVA_HOME%

if not exist "%JAVA_HOME%\bin\java.exe" goto :jdk_error
if not exist "%JAVA_HOME%\bin\javac.exe" goto :jdk_error
if not exist "%JAVA_HOME%\bin\jlink.exe" goto :jdk_error

set "JAVA_VERSION_FILE=%TEMP%\cauldra-java-version-%RANDOM%-%RANDOM%.txt"
set "JAVA_VERSION_LINE="
set "JAVA_VERSION="
set "JAVA_MAJOR="

"%JAVA_HOME%\bin\javac.exe" -version > "%JAVA_VERSION_FILE%" 2>&1
if errorlevel 1 goto :jdk_cleanup_error

set /p JAVA_VERSION_LINE=<"%JAVA_VERSION_FILE%"
del /q "%JAVA_VERSION_FILE%" >nul 2>&1

if not defined JAVA_VERSION_LINE goto :jdk_error

for /f "tokens=2" %%V in ("%JAVA_VERSION_LINE%") do set "JAVA_VERSION=%%V"
if not defined JAVA_VERSION goto :jdk_error

for /f "tokens=1 delims=." %%M in ("%JAVA_VERSION%") do set "JAVA_MAJOR=%%M"
if not "%JAVA_MAJOR%"=="21" goto :jdk_error

echo Detected javac %JAVA_VERSION%
echo JDK 21 check passed.
echo.

rem -----------------------------------------------------------------------------
rem Restore Node dependencies only when required native packages are missing.
rem -----------------------------------------------------------------------------
set "NEED_NPM_CI=0"
if not exist "node_modules\@capacitor\cli\bin\capacitor" set "NEED_NPM_CI=1"
if not exist "node_modules\@capacitor\app" set "NEED_NPM_CI=1"
if not exist "node_modules\@capacitor\inappbrowser" set "NEED_NPM_CI=1"

if "%NEED_NPM_CI%"=="1" (
    if not exist "package-lock.json" (
        echo ERROR: Required Node dependencies are missing and package-lock.json was not found.
        goto :failed
    )
    echo Restoring npm dependencies because required node_modules packages are missing...
    call npm.cmd ci
    if errorlevel 1 goto :failed
)

rem -----------------------------------------------------------------------------
rem Build/sync canonical frontend into the Android project.
rem -----------------------------------------------------------------------------
call npm.cmd run android:prepare
if errorlevel 1 goto :failed

set "APK_PATH=%~dp0android\app\build\outputs\apk\debug\app-debug.apk"

rem Delete the previous APK so a failed Gradle run can never look like success.
if exist "%APK_PATH%" del /q "%APK_PATH%"

cd /d "%~dp0android"
call gradlew.bat "-Dorg.gradle.java.home=%JAVA_HOME%" assembleDebug
if errorlevel 1 goto :failed
if not exist "%APK_PATH%" goto :missing_apk

echo.
echo Fresh APK: %APK_PATH%
for %%I in ("%APK_PATH%") do echo Built: %%~tI   Size: %%~zI bytes
certutil -hashfile "%APK_PATH%" SHA256
if errorlevel 1 goto :failed

echo Build complete.
pause
exit /b 0

:jdk_cleanup_error
if exist "%JAVA_VERSION_FILE%" del /q "%JAVA_VERSION_FILE%" >nul 2>&1
goto :jdk_error

:jdk_error
echo.
echo ERROR: JAVA_HOME must point to a complete JDK 21 installation.
echo Current JAVA_HOME=%JAVA_HOME%
if exist "%JAVA_HOME%\bin\java.exe" "%JAVA_HOME%\bin\java.exe" -version
if exist "%JAVA_HOME%\bin\javac.exe" "%JAVA_HOME%\bin\javac.exe" -version
goto :failed

:missing_apk
echo ERROR: Gradle completed without producing %APK_PATH%
goto :failed

:failed
set "BUILD_EXIT=%ERRORLEVEL%"
if "%BUILD_EXIT%"=="0" set "BUILD_EXIT=1"
echo.
echo APK build failed. Review the error above.
pause
exit /b %BUILD_EXIT%
