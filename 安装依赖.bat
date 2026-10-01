@echo off
chcp 65001 >nul
title TaskDeck 依赖安装
cd /d "%~dp0"

set "VENV=C:\Users\admin\.workbuddy\binaries\python\envs\task-manager"
set "PY=%VENV%\Scripts\python.exe"

if not exist "%PY%" (
    echo 未找到虚拟环境，正在创建：%VENV%
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败，请确认已安装 Python 3.10+ 并加入 PATH
        pause
        exit /b 1
    )
)

echo 正在安装依赖（使用清华镜像加速）...
"%PY%" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)

echo.
echo 依赖安装完成，可以运行「启动 TaskDeck.bat」了。
pause
