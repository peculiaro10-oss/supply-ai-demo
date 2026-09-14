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
rem  QA harness expects it.
rem ============================================================================
cd /d "%~dp0.."

call build-apk.bat qa
if errorlevel 1 exit /b 1

set "APK_PATH=%cd%\build\apk\cauldra-qa.apk"
if not exist "%APK_PATH%" goto :missing

if not exist "qa_environment\APK" mkdir "qa_environment\APK"
copy /Y "%APK_PATH%" "qa_environment\APK\cauldra-qa.apk" >nul
if errorlevel 1 goto :failed

echo.
echo QA APK: %cd%\qa_environment\APK\cauldra-qa.apk
certutil -hashfile "qa_environment\APK\cauldra-qa.apk" SHA256
echo.
echo Re-checking the copy that will actually be installed:
call node scripts\verify-apk-target.js --apk="qa_environment\APK\cauldra-qa.apk" --target=qa
if errorlevel 1 goto :failed
exit /b 0

:missing
echo ERROR: build-apk.bat reported success but %APK_PATH% is missing.
goto :failed

:failed
echo.
echo QA APK build failed. Review the error above.
exit /b 1
