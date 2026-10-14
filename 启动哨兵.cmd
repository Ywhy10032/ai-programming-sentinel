@echo off
setlocal
set "SENTINEL_DIR="
for /d %%D in ("%~dp0output\*-1.3.0") do set "SENTINEL_DIR=%%~fD"
if not defined SENTINEL_DIR exit /b 2
for %%E in ("%SENTINEL_DIR%\*.exe") do if /i not "%%~nxE"=="sentinel.exe" start "" "%%~fE"
