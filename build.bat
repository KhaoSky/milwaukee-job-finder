@echo off
echo ════════════════════════════════════════════════
echo   Milwaukee Job Finder — Windows Build
echo ════════════════════════════════════════════════
echo.

REM Install / upgrade required packages
pip install -r requirements.txt --quiet
pip install pyinstaller --quiet

REM Remove previous build artifacts
if exist build   rmdir /s /q build
if exist dist    rmdir /s /q dist
if exist MKEJobFinder.spec del /q MKEJobFinder.spec

echo Building executable...
pyinstaller ^
  --onefile ^
  --windowed ^
  --name "MKEJobFinder" ^
  --add-data "templates;templates" ^
  --hidden-import=pystray._win32 ^
  --hidden-import=flask ^
  --hidden-import=anthropic ^
  --hidden-import=pdfplumber ^
  --hidden-import=docx ^
  --hidden-import=apscheduler ^
  --hidden-import=apscheduler.schedulers.background ^
  --hidden-import=apscheduler.executors.pool ^
  --hidden-import=apscheduler.triggers.interval ^
  main.py

echo.
if exist "dist\MKEJobFinder.exe" (
    echo ✓ Build successful^^!
    echo.
    echo   EXE location: %cd%\dist\MKEJobFinder.exe
    echo.
    echo   To install: copy MKEJobFinder.exe anywhere you like and run it.
    echo   To auto-start with Windows: right-click the tray icon after launch.
) else (
    echo ✗ Build failed. Check the output above for errors.
)
echo.
pause
