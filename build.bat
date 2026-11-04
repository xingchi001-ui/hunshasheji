@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 goto :error
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :error
python -m pip install --upgrade pip
if errorlevel 1 goto :error
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist release rmdir /s /q release

python -m PyInstaller --noconfirm --clean --windowed --onefile --name ShowGownStudio main.py
if errorlevel 1 goto :error

mkdir release\ShowGownStudio
copy /y dist\ShowGownStudio.exe release\ShowGownStudio\ShowGownStudio.exe >nul
copy /y config.example.json release\ShowGownStudio\config.example.json >nul
copy /y start.bat release\ShowGownStudio\start.bat >nul
if not exist release\ShowGownStudio\data mkdir release\ShowGownStudio\data
copy /y data\README.md release\ShowGownStudio\data\README.md >nul

echo Build completed.
echo Output: release\ShowGownStudio\ShowGownStudio.exe
echo Copy config.example.json to config.json and fill your API key before running.
echo Put local training data at data\dress_training\segments.jsonl if needed.
goto :end

:error
echo Build failed with errorlevel %errorlevel%.
exit /b %errorlevel%

:end
endlocal
