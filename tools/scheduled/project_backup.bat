@echo off
REM Weekly full project backup (source only; caches and browser cache excluded).
setlocal
cd /d "%~dp0..\.."
if not exist "data\logs" mkdir "data\logs"
echo [%date% %time%] project backup start>> "data\logs\project_backup.log"
python backup.py --prune 8 >> "data\logs\project_backup.log" 2>&1
echo [%date% %time%] exit=%errorlevel%>> "data\logs\project_backup.log"
endlocal
