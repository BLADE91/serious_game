@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Serious Game Terminal 1.0

rem This launcher is self-contained inside this worktree.
set "ROOT_DIR=%~dp0"
set "BACKEND_DIR=%ROOT_DIR%code\backend"
set "CLIENT_DIR=%ROOT_DIR%code\frontend\terminal"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONNOUSERSITE=1"
set "PYTHONPATH="
set "PYTHONHOME="
set "VENV_DIR=%ROOT_DIR%.venv"
set "GAME_REPOSITORY=sqlite"
set "GAME_DATABASE_PATH=data/serious_game_terminal.db"
set "GAME_PORT=8100"
set "ROLE_LLM_TIMEOUT_SECONDS=15"
set "ROLE_LLM_MAX_RETRIES=1"

if not exist "%BACKEND_DIR%\run_server.py" goto :missing_project
if not exist "%CLIENT_DIR%\main.py" goto :missing_project

set "BASE_PYTHON="
call :try_python "D:\ProgramData\anaconda3\python.exe"
call :try_python "C:\ProgramData\anaconda3\python.exe"
for /d %%D in ("%LocalAppData%\Programs\Python\Python*") do call :try_python "%%~fD\python.exe"
for /f "delims=" %%I in ('where python.exe 2^>nul') do call :try_python "%%I"
for /f "delims=" %%I in ('py.exe -3 -c "import sys; print(sys.executable)" 2^>nul') do call :try_python "%%I"
if not defined BASE_PYTHON goto :missing_python

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Creating an isolated Python environment for the terminal version...
    "%BASE_PYTHON%" -m venv "%VENV_DIR%"
    if errorlevel 1 goto :venv_failed
)
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"

"%PYTHON_EXE%" -c "import fastapi, pydantic, uvicorn, pymysql, cryptography" >nul 2>&1
if errorlevel 1 (
    echo Installing Python dependencies for this version...
    "%PYTHON_EXE%" -m pip install -r "%BACKEND_DIR%\requirements.txt"
    if errorlevel 1 goto :install_failed
)

if /i "%~1"=="--check" (
    echo Terminal worktree check passed.
    echo Backend: %BACKEND_DIR%
    echo Client:  %CLIENT_DIR%
    exit /b 0
)

echo Starting terminal backend on http://127.0.0.1:8100 ...
powershell.exe -NoProfile -Command "try { $r=Invoke-RestMethod 'http://127.0.0.1:8100/health/ready' -TimeoutSec 2; if($r.repository -eq 'sqlite'){exit 0}else{exit 2} } catch { exit 1 }" >nul 2>&1
set "BACKEND_STATUS=%ERRORLEVEL%"
if "%BACKEND_STATUS%"=="2" goto :port_conflict
if not "%BACKEND_STATUS%"=="0" start "Terminal Backend - Keep Open" /D "%BACKEND_DIR%" "%ComSpec%" /k ""%PYTHON_EXE%" run_server.py --port 8100"

powershell.exe -NoProfile -Command "$limit=(Get-Date).AddSeconds(45); do { try { $r=Invoke-RestMethod 'http://127.0.0.1:8100/health/ready' -TimeoutSec 2; if($r.status -eq 'ready' -and $r.repository -eq 'sqlite'){exit 0} } catch {}; Start-Sleep -Milliseconds 500 } while((Get-Date)-lt $limit); exit 1" >nul 2>&1
if errorlevel 1 goto :backend_failed

pushd "%CLIENT_DIR%"
"%PYTHON_EXE%" main.py
set "GAME_EXIT_CODE=%ERRORLEVEL%"
popd
echo.
echo Terminal client closed. The backend window may now be closed.
pause
exit /b %GAME_EXIT_CODE%

:try_python
if defined BASE_PYTHON exit /b 0
if not exist "%~1" exit /b 0
"%~1" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 set "BASE_PYTHON=%~1"
exit /b 0

:missing_project
echo [ERROR] Required terminal worktree files were not found.
pause
exit /b 1
:missing_python
echo [ERROR] Python 3.11 or newer was not found.
pause
exit /b 1
:venv_failed
echo [ERROR] Could not create the terminal version's isolated Python environment.
pause
exit /b 1
:install_failed
echo [ERROR] Python dependency installation failed.
pause
exit /b 1
:backend_failed
echo [ERROR] Terminal backend did not become ready within 45 seconds.
pause
exit /b 1
:port_conflict
echo [ERROR] Port 8100 is occupied by an incompatible backend.
pause
exit /b 1
