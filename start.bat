@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
title EduGrade

if exist "backend\venv\Scripts\python.exe" goto run

echo Creating Python environment, please wait...
python -m venv backend\venv
if errorlevel 1 goto novenv
goto run

:novenv
echo Could not create the environment.
echo Install Python 3.10 or newer from python.org and tick Add to PATH.
pause
exit /b 1

:run
"backend\venv\Scripts\python.exe" run.py %*
if errorlevel 1 pause
