@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo [首次运行] 正在创建虚拟环境并安装依赖...
    py -3 -m venv .venv || python -m venv .venv
    .venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
)
if exist .venv\Scripts\pythonw.exe (
    start "" .venv\Scripts\pythonw.exe web_app.py
) else (
    start "" pythonw web_app.py
)
