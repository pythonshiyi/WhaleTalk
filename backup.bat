@echo off
chcp 65001 >nul
title 备份鲸语 WhaleTalk
cd /d "%~dp0"
python backup.py
echo.
pause
