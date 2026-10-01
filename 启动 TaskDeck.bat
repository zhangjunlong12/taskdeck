@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "VENV=C:\Users\admin\.workbuddy\binaries\python\envs\task-manager"
set "PYW=%VENV%\Scripts\pythonw.exe"
set "PY=%VENV%\Scripts\python.exe"

if not exist "%PY%" (
    echo [错误] 未找到 Python 环境：%VENV%
    echo 请先运行「安装依赖.bat」
    pause
    exit /b 1
)

if exist "%PYW%" (
    start "" "%PYW%" app.py
) else (
    start "" "%PY%" app.py
)
