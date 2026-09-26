@echo off
rem ============================================================
rem  EFORT ER3-600 Robot Simulator - one-click launcher
rem ------------------------------------------------------------
rem  ASCII ONLY before the "chcp 65001" line; all non-ASCII text
rem  stays inside Python / Vue output so cmd.exe never misparses.
rem
rem  usage:  start.bat          dev mode  (vite :3000 + api :5000)
rem          start.bat build    build frontend, serve all on :5000
rem
rem  Python runs inside backend\venv (auto-created on first run).
rem ============================================================
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "NEED_BUILD=0"
if /i "%~1"=="build" set "NEED_BUILD=1"

echo ============================================================
echo   EFORT ER3-600  six-axis robot simulator
echo ============================================================
echo.

rem ---- venv layout ----
set "VENV=backend\venv"
set "VENV_PY=%VENV%\Scripts\python.exe"
set "VENV_PIP=%VENV%\Scripts\pip.exe"

rem ---- 1. host Python (only used to *create* the venv) ----
where python >nul 2>&1
if errorlevel 1 (
  echo [X] Python not found in PATH.
  echo     Install Python 3.10 or 3.11, then reopen this window.
  goto :fail
)
python -c "import sys;sys.exit(0 if (3,10)<=sys.version_info[:2]<(3,13) else 1)" >nul 2>&1
if errorlevel 1 (
  echo [!] Host Python is not 3.10/3.11. The venv will still be
  echo     created, but eventlet / faster-whisper may misbehave on 3.12+.
)

rem ---- 2. create venv if missing ----
if not exist "%VENV_PY%" (
  echo [1/5] creating virtualenv at %VENV% ...
  python -m venv "%VENV%"
  if errorlevel 1 ( echo [X] venv creation failed. && goto :fail )
) else (
  echo [1/5] venv ............... OK
)

rem ---- 3. backend\.env -------------------------------------------
if exist "backend\.env" (
  echo [2/5] backend\.env ...... OK
) else (
  if exist "backend\.env.example" (
    copy /y "backend\.env.example" "backend\.env" >nul
    echo [2/5] backend\.env ...... created from .env.example
    echo       -^> open it and fill in ZHIPU_API_KEY, otherwise the
    echo          natural-language features stay degraded.
  ) else (
    echo [2/5] backend\.env ...... MISSING - AI features degraded
  )
)

rem ---- 4. python deps (installed into the venv) -----------------
"%VENV_PY%" -c "import flask, flask_socketio, flask_cors, numpy, dotenv, faster_whisper, edge_tts" >nul 2>&1
if errorlevel 1 (
  echo [3/5] installing python deps into venv, please wait
  "%VENV_PIP%" install -r backend\requirements.txt
  if errorlevel 1 ( echo [X] pip install failed. && goto :fail )
) else (
  echo [3/5] python deps ....... OK
)

rem ---- 5. node deps ----------------------------------------------
if exist "frontend\node_modules" (
  echo [4/5] node_modules ....... OK
) else (
  echo [4/5] npm install, first run - please wait
  pushd frontend
  call npm install
  if errorlevel 1 ( popd && echo [X] npm install failed. && goto :fail )
  popd
)

rem ---- launch -----------------------------------------------------
if "%NEED_BUILD%"=="1" goto :build

echo [5/5] starting backend + frontend ...
start "ER3-600 Backend"  cmd /k "cd /d %~dp0backend && %VENV_PY% app.py"
start "ER3-600 Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"

echo.
echo   frontend : http://localhost:3000
echo   backend  : http://localhost:5000
echo   health   : http://localhost:5000/api/health
echo   stop     : close the two console windows
echo.
pause
exit /b 0

:build
echo [5/5] building frontend bundle ...
pushd frontend
call npm run build
if errorlevel 1 ( popd && echo [X] npm run build failed. && goto :fail )
popd
echo.
echo   Build done. The backend now serves the bundle itself:
echo   open  http://localhost:5000
echo.
echo   launching backend only ...
start "ER3-600 Backend" cmd /k "cd /d %~dp0backend && %VENV_PY% app.py"
pause
exit /b 0

:fail
echo.
echo Startup aborted.
pause
exit /b 1
