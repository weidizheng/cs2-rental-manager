@echo off
setlocal
cd /d "%~dp0"

rem Feature-branch PyInstaller work directories are disposable caches.  Keep
rem only the active build directory; release\CS2租赁管理.exe is overwritten below.
for /d %%D in ("build-*") do if exist "%%~fD" rmdir /s /q "%%~fD"

python -m compileall -q main.py modules || exit /b 1
python -m unittest discover -v || exit /b 1
python tools\create_app_icon.py || exit /b 1
python -m PyInstaller --noconfirm --clean ^
  --distpath "release" ^
  --workpath "build" ^
  "CS2租赁管理.spec" || exit /b 1

echo.
echo Build complete: release\CS2租赁管理.exe
pause
