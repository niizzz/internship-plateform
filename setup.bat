@echo off
rem First-time setup: installs Python 3.14 + Node.js if missing (via winget),
rem then the backend venv, the Chromium used by the scrapers, and the frontend
rem packages. Safe to re-run. launch.bat calls this automatically the first time.
setlocal
title Internship Plateform - setup
cd /d "%~dp0"
echo.
echo  === Internship Plateform - first-time setup ===
echo  This takes 3-10 minutes. Leave this window open.
echo.

rem ---------------- Python 3.14 ----------------
call :findpy
if defined PY goto havepy
echo [1/5] Python 3.14 not found - installing it...
call :needwinget || goto fail
winget install -e --id Python.Python.3.14 --scope user --silent --accept-package-agreements --accept-source-agreements
call :findpy
if defined PY goto havepy
echo.
echo  Python was installed but cannot be found yet.
echo  Close this window and double-click launch.bat again.
goto fail
:havepy
echo [1/5] Python OK: %PY%

rem ---------------- Node.js ----------------
call :findnode
if defined HAVENODE goto havenode
echo [2/5] Node.js not found - installing it...
call :needwinget || goto fail
winget install -e --id OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements
call :findnode
if defined HAVENODE goto havenode
echo.
echo  Node.js was installed but cannot be found yet.
echo  Close this window and double-click launch.bat again.
goto fail
:havenode
echo [2/5] Node.js OK

rem ---------------- Backend ----------------
if exist "backend\.venv\Scripts\python.exe" goto havevenv
echo [3/5] Creating the Python environment...
%PY% -m venv "backend\.venv" || goto fail
:havevenv
echo [3/5] Installing Python packages...
"backend\.venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r "backend\requirements.txt" || goto fail

echo [4/5] Installing the scraper browser (Chromium)...
"backend\.venv\Scripts\python.exe" -m playwright install chromium || goto fail

rem ---------------- Frontend ----------------
echo [5/5] Installing the website packages...
pushd frontend
call npm ci --no-audit --no-fund || (popd & goto fail)
popd

echo ok> "backend\.venv\.setup-ok"
echo.
echo  === Setup complete ===
echo.
endlocal
exit /b 0

:fail
echo.
echo  !!! Setup did not finish. Take a screenshot of this window and send it over.
echo.
pause
endlocal
exit /b 1

rem ---------------- helpers ----------------
:findpy
set "PY="
py -3.14 -c "import sys" >nul 2>&1 && (set "PY=py -3.14" & exit /b 0)
if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" (set PY="%LOCALAPPDATA%\Programs\Python\Python314\python.exe" & exit /b 0)
python -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 14) else 1)" >nul 2>&1 && (set "PY=python" & exit /b 0)
exit /b 0

:findnode
set "HAVENODE="
where npm >nul 2>&1 && (set "HAVENODE=1" & exit /b 0)
if exist "%ProgramFiles%\nodejs\npm.cmd" (set "PATH=%ProgramFiles%\nodejs;%PATH%" & set "HAVENODE=1" & exit /b 0)
exit /b 0

:needwinget
where winget >nul 2>&1 && exit /b 0
echo.
echo  winget is not available on this PC, so it can't install things automatically.
echo  Install these two by hand, then double-click launch.bat again:
echo    - Python 3.14 : https://www.python.org/downloads/  (tick "Add python.exe to PATH")
echo    - Node.js LTS : https://nodejs.org/
exit /b 1
