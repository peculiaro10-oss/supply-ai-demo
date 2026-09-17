@echo off
setlocal
rem ============================================================================
rem  Cauldra Android APK build  --  BUILD-001
rem
rem  Usage:  build-apk.bat qa
rem          build-apk.bat production
rem
rem  The target is mandatory. It used to be implicit, and implicit meant
rem  production: a QA build that simply did not say what it was pointed at the
rem  live backend. The parity gate (verifyCauldraFrontend) stays ENABLED for
rem  every target -- never pass -x to work around it -- and the finished APK is
rem  opened and checked before you are told where it is.
rem ============================================================================
if not defined JAVA_HOME set "JAVA_HOME=C:\Program Files\Eclipse Adoptium\jdk-21.0.12.101-hotspot"
cd /d "%~dp0"

set "TARGET=%~1"
if "%TARGET%"=="" goto :no_target
if /i "%TARGET%"=="qa" goto :target_ok
if /i "%TARGET%"=="production" goto :target_ok
echo ERROR: unknown build target "%TARGET%". Use: qa ^| production
goto :failed
:target_ok

if not exist "%JAVA_HOME%\bin\java.exe" goto :jdk_error
if not exist "%JAVA_HOME%\bin\jlink.exe" goto :jdk_error
"%JAVA_HOME%\bin\java.exe" -version 2>&1 | findstr /c:"21.0." >nul
if errorlevel 1 goto :jdk_error

if not exist "node_modules\@capacitor\cli\bin\capacitor" (
    echo Restoring npm dependencies because node_modules is missing...
    call npm.cmd ci
    if errorlevel 1 goto :failed
)

echo.
echo === Preparing bundle for target: %TARGET% ===
call npm.cmd run build:www -- --target=%TARGET%
if errorlevel 1 goto :failed
call npx.cmd cap sync android
if errorlevel 1 goto :failed
call node scripts\verify-native-bundle.js --target=%TARGET%
if errorlevel 1 goto :failed

set "APK_PATH=%~dp0android\app\build\outputs\apk\debug\app-debug.apk"
if exist "%APK_PATH%" del /q "%APK_PATH%"
rem Explicit path: never rely on cmd searching the current directory. Hardened
rem shells set NoDefaultCurrentDirectoryInExePath, under which a bare "gradlew.bat"
rem is "not recognized" even from inside android\.
cd /d "%~dp0android"
call "%~dp0android\gradlew.bat" "-Dorg.gradle.java.home=%JAVA_HOME%" assembleDebug
if errorlevel 1 goto :failed
cd ..
if not exist "%APK_PATH%" goto :missing_apk

echo.
echo === Verifying the PACKAGED artifact before it is installed anywhere ===
call node scripts\verify-apk-target.js --apk="%APK_PATH%" --target=%TARGET%
if errorlevel 1 goto :apk_target_mismatch

if not exist "build\apk" mkdir "build\apk" >nul 2>&1
copy /Y "%APK_PATH%" "build\apk\cauldra-%TARGET%.apk" >nul
if errorlevel 1 goto :failed

echo.
echo Fresh %TARGET% APK: %~dp0build\apk\cauldra-%TARGET%.apk
for %%I in ("%APK_PATH%") do echo Built: %%~tI   Size: %%~zI bytes
echo Build complete.
pause
exit /b 0

:no_target
echo.
echo ERROR: no build target given.
echo.
echo     build-apk.bat qa           -^> https://cauldra-qa.up.railway.app
echo     build-apk.bat production   -^> https://cauldra.up.railway.app
echo.
echo There is no default on purpose: an unstated target would ship production.
goto :failed

:jdk_error
echo ERROR: Set JAVA_HOME to a complete JDK 21 installation before building.
goto :failed

:apk_target_mismatch
echo.
echo ERROR: the packaged APK does not carry the requested target. Not usable. Deleting.
if exist "%APK_PATH%" del /q "%APK_PATH%"
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
