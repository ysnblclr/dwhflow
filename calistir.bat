@echo off
rem DWHFlow baslatir. Cift tiklayin ya da: calistir.bat  (ekip icin: calistir.bat -Ekip)
rem Durdurmak icin bu pencerede Ctrl+C.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0calistir.ps1" %*
if errorlevel 1 pause
