@echo off
setlocal
chcp 65001 >nul
set "SENTINEL_EXE="
for /d %%D in ("%~dp0output\*-1.3.0") do set "SENTINEL_EXE=%%~fD\sentinel.exe"
if not defined SENTINEL_EXE exit /b 2
"%SENTINEL_EXE%" %*
exit /b %errorlevel%
