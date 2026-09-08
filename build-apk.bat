@echo off
setlocal
if not defined JAVA_HOME set "JAVA_HOME=C:\Program Files\Eclipse Adoptium\jdk-21.0.12.101-hotspot"
if not exist "%JAVA_HOME%\bin\jlink.exe" (
    echo Set JAVA_HOME to a complete JDK 21 installation before building.
    exit /b 1
)
cd /d "%~dp0"
call npm.cmd run android:prepare
if errorlevel 1 exit /b 1
cd android
call gradlew.bat "-Dorg.gradle.java.home=%JAVA_HOME%" assembleDebug
if errorlevel 1 exit /b 1
echo APK: %~dp0android\app\build\outputs\apk\debug\app-debug.apk
