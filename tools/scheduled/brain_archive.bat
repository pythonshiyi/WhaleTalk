@echo off
REM Daily brain snapshot + external mirror. Fires from Windows Task Scheduler.
REM Uses manifest.archive_mirror, so no hard-coded backup path here.
setlocal
cd /d "%~dp0..\.."
if not exist "data\logs" mkdir "data\logs"
echo [%date% %time%] brain archive start>> "data\logs\brain_archive.log"
python brainkit.py archive >> "data\logs\brain_archive.log" 2>&1
echo [%date% %time%] exit=%errorlevel%>> "data\logs\brain_archive.log"
endlocal
