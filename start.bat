@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo [首次运行] 正在创建虚拟环境并安装依赖（清华源，首次约 3-10 分钟）...
    py -3 -m venv .venv || python -m venv .venv
    .venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo.
        echo [失败] 依赖安装未成功，请检查网络后重新运行本脚本。
        echo 提示：程序仍可尝试启动，缺少的组件会在启动时自动重试安装。
        echo.
    )
)
if exist .venv\Scripts\pythonw.exe (
    start "" .venv\Scripts\pythonw.exe web_app.py
) else (
    start "" pythonw web_app.py
)
