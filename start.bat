@echo off
setlocal
cd /d "%~dp0"
if not exist "ShowGownStudio.exe" (
    echo ShowGownStudio.exe was not found.
    pause
    exit /b 1
)
start "Show Gown Studio" "%~dp0ShowGownStudio.exe"
endlocal
