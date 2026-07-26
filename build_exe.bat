@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "EXE_NAME=!CS2_RENTAL_EXE_NAME!"
if "!EXE_NAME!"=="" set "EXE_NAME=CS2租赁管理"

rem Feature-branch PyInstaller work directories are disposable caches.  Keep
rem only the active build directory; release\CS2租赁管理.exe is overwritten below.
for /d %%D in ("build-*") do if exist "%%~fD" rmdir /s /q "%%~fD"

python -m compileall -q main.py modules || exit /b 1
python -m unittest discover -s tests -v || exit /b 1
python tools\create_app_icon.py || exit /b 1
python -m PyInstaller --noconfirm --clean --distpath "release" --workpath "build" "CS2租赁管理.spec" || exit /b 1

rem The work directory is useful after a failed build, but disposable after a
rem successful EXE is produced. Keep the project folder compact by removing it.
if exist "build" rmdir /s /q "build"

echo.
echo Build complete: release\!EXE_NAME!.exe
pause
