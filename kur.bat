@echo off
rem DWHFlow kurulumu. Cift tiklayin ya da: kur.bat  (gelistirme araclariyla: kur.bat -Dev)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0kur.ps1" %*
echo.
pause
