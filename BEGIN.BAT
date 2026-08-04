@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Serious Game Web 0.5

rem This launcher uses ports and data dedicated to the web worktree.
set "ROOT_DIR=%~dp0"
set "BACKEND_DIR=%ROOT_DIR%code\backend"
set "WEB_DIR=%ROOT_DIR%code\frontend\web"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONNOUSERSITE=1"
set "PYTHONPATH="
set "PYTHONHOME="
set "VENV_DIR=%ROOT_DIR%.venv"
set "GAME_REPOSITORY=sqlite"
set "GAME_DATABASE_PATH=data/serious_game_web.db"
set "GAME_PORT=8101"
set "GAME_BACKEND_URL=http://127.0.0.1:8101"
set "PORT=3001"
set "ROLE_LLM_TIMEOUT_SECONDS=15"
set "ROLE_LLM_MAX_RETRIES=1"

if not exist "%BACKEND_DIR%\run_server.py" goto :missing_project
if not exist "%WEB_DIR%\package.json" goto :missing_project

set "BASE_PYTHON="
call :try_python "D:\ProgramData\anaconda3\python.exe"
call :try_python "C:\ProgramData\anaconda3\python.exe"
for /d %%D in ("%LocalAppData%\Programs\Python\Python*") do call :try_python "%%~fD\python.exe"
for /f "delims=" %%I in ('where python.exe 2^>nul') do call :try_python "%%I"
for /f "delims=" %%I in ('py.exe -3 -c "import sys; print(sys.executable)" 2^>nul') do call :try_python "%%I"
if not defined BASE_PYTHON goto :missing_python

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Creating an isolated Python environment for the web version...
    "%BASE_PYTHON%" -m venv "%VENV_DIR%"
    if errorlevel 1 goto :venv_failed
)
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"

where wsl.exe >nul 2>&1 || goto :missing_wsl
set "WEB_WSL_DIR="
for /f "delims=" %%I in ('wsl.exe wslpath -a "%WEB_DIR%" 2^>nul') do set "WEB_WSL_DIR=%%I"
if not defined WEB_WSL_DIR goto :missing_wsl
wsl.exe bash -lc "command -v node >/dev/null && command -v npm >/dev/null" || goto :missing_wsl_node
"%PYTHON_EXE%" -c "import fastapi, pydantic, uvicorn, pymysql, cryptography" >nul 2>&1
if errorlevel 1 (
    echo Installing Python dependencies for this version...
    "%PYTHON_EXE%" -m pip install -r "%BACKEND_DIR%\requirements.txt"
    if errorlevel 1 goto :install_failed
)

if /i "%~1"=="--check" (
    echo Web worktree check passed.
    echo Backend: %BACKEND_DIR%
    echo Web:     %WEB_DIR%
    echo WSL Web: %WEB_WSL_DIR%
    echo URLs:    http://127.0.0.1:8101 and http://127.0.0.1:3001
    exit /b 0
)

echo Preparing the Linux web runtime...
wsl.exe bash -lc "cd '%WEB_WSL_DIR%' && if [ ! -f .wsl-deps-ready ]; then npm ci && touch .wsl-deps-ready; fi && npm run build"
if errorlevel 1 goto :web_prepare_failed

echo Starting dedicated web backend on http://127.0.0.1:8101 ...
powershell.exe -NoProfile -Command "try { $r=Invoke-RestMethod 'http://127.0.0.1:8101/health/ready' -TimeoutSec 2; if($r.repository -eq 'sqlite'){exit 0}else{exit 2} } catch { exit 1 }" >nul 2>&1
set "BACKEND_STATUS=%ERRORLEVEL%"
if "%BACKEND_STATUS%"=="2" goto :port_conflict
if not "%BACKEND_STATUS%"=="0" start "Web Backend - Keep Open" /D "%BACKEND_DIR%" "%ComSpec%" /k ""%PYTHON_EXE%" run_server.py --port 8101"

powershell.exe -NoProfile -Command "$limit=(Get-Date).AddSeconds(45); do { try { $r=Invoke-RestMethod 'http://127.0.0.1:8101/health/ready' -TimeoutSec 2; if($r.status -eq 'ready' -and $r.repository -eq 'sqlite'){exit 0} } catch {}; Start-Sleep -Milliseconds 500 } while((Get-Date)-lt $limit); exit 1" >nul 2>&1
if errorlevel 1 goto :backend_failed

echo Starting web client on http://127.0.0.1:3001 ...
start "Web Client - Keep Open" wsl.exe bash -lc "cd '%WEB_WSL_DIR%' && export GAME_BACKEND_URL='http://127.0.0.1:8101' PORT='3001' WRANGLER_LOG_PATH='.wrangler/wrangler.log' && npm run start"
powershell.exe -NoProfile -Command "$limit=(Get-Date).AddSeconds(45); do { try { $r=Invoke-WebRequest 'http://127.0.0.1:3001' -UseBasicParsing -TimeoutSec 2; if($r.StatusCode -eq 200){exit 0} } catch {}; Start-Sleep -Milliseconds 500 } while((Get-Date)-lt $limit); exit 1" >nul 2>&1
if errorlevel 1 goto :web_failed

start "" "http://127.0.0.1:3001"
echo Web game is running. Keep both service windows open.
pause
exit /b 0

:try_python
if defined BASE_PYTHON exit /b 0
if not exist "%~1" exit /b 0
"%~1" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 set "BASE_PYTHON=%~1"
exit /b 0

:missing_project
echo [ERROR] Required web worktree files were not found.
pause
exit /b 1
:missing_python
echo [ERROR] Python 3.11 or newer was not found.
pause
exit /b 1
:venv_failed
echo [ERROR] Could not create the web version's isolated Python environment.
pause
exit /b 1
:missing_wsl
echo [ERROR] WSL is required to run the Vinext web server correctly.
echo Install WSL, then run this launcher again.
pause
exit /b 1
:missing_wsl_node
echo [ERROR] Node.js and npm were not found inside WSL.
pause
exit /b 1
:install_failed
echo [ERROR] Python dependency installation failed.
pause
exit /b 1
:web_prepare_failed
echo [ERROR] WSL web dependency installation or production build failed.
pause
exit /b 1
:backend_failed
echo [ERROR] Web backend did not become ready within 45 seconds.
pause
exit /b 1
:web_failed
echo [ERROR] Web client did not become ready within 45 seconds.
pause
exit /b 1
:port_conflict
echo [ERROR] Port 8101 is occupied by an incompatible backend.
pause
exit /b 1
