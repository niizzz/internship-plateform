@echo off
rem One-click launcher: starts backend + frontend if not already running, opens the app.
setlocal
set "ROOT=%~dp0"

rem --- Backend (port 8000) ---
netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul 2>&1
if errorlevel 1 (
    start "Internship DB - backend" /min cmd /c "cd /d "%ROOT%backend" && .venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000"
)

rem --- Frontend (port 5173) ---
netstat -ano | findstr /R /C:":5173 .*LISTENING" >nul 2>&1
if errorlevel 1 (
    start "Internship DB - frontend" /min cmd /c "cd /d "%ROOT%frontend" && npm run dev"
)

rem --- Wait until the frontend answers, then open the browser ---
echo Starting Internship Plateform...
for /l %%i in (1,1,30) do (
    powershell -NoProfile -Command "try { (New-Object Net.Sockets.TcpClient('127.0.0.1',5173)).Close(); exit 0 } catch { exit 1 }" >nul 2>&1
    if not errorlevel 1 goto open
    timeout /t 1 /nobreak >nul
)
:open
start "" http://localhost:5173
endlocal
