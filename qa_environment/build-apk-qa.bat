@echo off
setlocal
rem ============================================================================
rem  QA Android APK  --  BUILD-001
rem
rem  This script used to build QA by rewriting the generated www\index.html to
rem  inject window.CAULDRA_API_BASE_URL, then excluding the parity gate with
rem  "-x verifyCauldraFrontend" because that injection broke it. Both of those
rem  are gone. QA is now an ordinary, supported, fully verified build target.
rem
rem  It is kept only as a convenience wrapper that also drops the APK where the
rem  QA harness expects it. Every path is resolved from this script's own
rem  location, never from the caller's working directory.
rem ============================================================================
for %%I in ("%~dp0..") do set "REPO=%%~fI"
cd /d "%REPO%"

call "%REPO%\build-apk.bat" qa
if errorlevel 1 exit /b 1

set "APK_PATH=%REPO%\build\apk\cauldra-qa.apk"
if not exist "%APK_PATH%" goto :missing

if not exist "%REPO%\qa_environment\APK" mkdir "%REPO%\qa_environment\APK"
copy /Y "%APK_PATH%" "%REPO%\qa_environment\APK\cauldra-qa.apk" >nul
if errorlevel 1 goto :failed

echo.
echo QA APK: %REPO%\qa_environment\APK\cauldra-qa.apk
certutil -hashfile "%REPO%\qa_environment\APK\cauldra-qa.apk" SHA256

echo.
echo Re-checking the copy that will actually be installed:
call node "%REPO%\scripts\verify-apk-target.js" --apk="%REPO%\qa_environment\APK\cauldra-qa.apk" --target=qa
if errorlevel 1 goto :failed
exit /b 0

:missing
echo ERROR: build-apk.bat reported success but %APK_PATH% is missing.
goto :failed

:failed
echo.
echo QA APK build failed. Review the error above.
exit /b 1
