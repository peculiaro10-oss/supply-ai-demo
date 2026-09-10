@echo off
setlocal

cd /d "%~dp0.."

if not defined JAVA_HOME set "JAVA_HOME=C:\Program Files\Eclipse Adoptium\jdk-21.0.12.101-hotspot"

if not exist "%JAVA_HOME%\bin\java.exe" goto :jdk_error
if not exist "%JAVA_HOME%\bin\jlink.exe" goto :jdk_error

echo.
echo === Building clean web assets ===
call npm run build:www
if errorlevel 1 goto :failed

echo.
echo === Verifying clean source/build parity ===
node scripts\verify-native-bundle.js --www-only
if errorlevel 1 goto :failed

echo.
echo === Injecting QA API endpoint into generated www only ===
powershell -NoProfile -Command ^
  "$p='www\index.html';" ^
  "$html=[System.IO.File]::ReadAllText($p);" ^
  "$pattern='<script\s+src=[\"'']/js/app\.js[\"'']\s*></script>';" ^
  "$qa='<script>window.CAULDRA_API_BASE_URL=\"https://cauldra-qa.up.railway.app\";</script>' + [Environment]::NewLine + '    <script src=\"/js/app.js\"></script>';" ^
  "$updated=[regex]::Replace($html,$pattern,$qa,1);" ^
  "if($updated -eq $html){throw 'app.js script marker not found in www\index.html'};" ^
  "[System.IO.File]::WriteAllText($p,$updated,(New-Object System.Text.UTF8Encoding($false)))"
if errorlevel 1 goto :failed

echo.
echo === Syncing QA bundle to Android ===
call npx cap sync android
if errorlevel 1 goto :failed

echo.
echo === Verifying QA URL is present in Android packaged assets ===
findstr /S /N /I "cauldra-qa.up.railway.app" android\app\src\main\assets\public\*.html android\app\src\main\assets\public\js\*.js
if errorlevel 1 goto :failed

set "APK_PATH=%cd%\android\app\build\outputs\apk\debug\app-debug.apk"
if exist "%APK_PATH%" del /q "%APK_PATH%"

cd android
call gradlew.bat "-Dorg.gradle.java.home=%JAVA_HOME%" assembleDebug -x verifyCauldraFrontend
if errorlevel 1 goto :failed
cd ..

if not exist "%APK_PATH%" goto :missing_apk

if not exist "qa_environment\APK" mkdir "qa_environment\APK"

copy /Y "%APK_PATH%" "qa_environment\APK\cauldra-qa.apk" >nul
if errorlevel 1 goto :failed

echo.
echo QA APK built successfully:
echo %cd%\qa_environment\APK\cauldra-qa.apk
certutil -hashfile "qa_environment\APK\cauldra-qa.apk" SHA256

echo.
echo === Restoring normal generated www from untouched frontend source ===
call npm run build:www

echo.
echo Done.
exit /b 0

:jdk_error
echo ERROR: JDK 21 installation not found.
goto :failed

:missing_apk
echo ERROR: Gradle completed without producing the APK.
goto :failed

:failed
echo.
echo QA APK build failed. Review the error above.

echo.
echo Attempting to restore normal generated www...
call npm run build:www

exit /b 1