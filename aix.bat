@echo off
setlocal EnableExtensions EnableDelayedExpansion
set "HERE=%~dp0"
set "PY=python"
title aiX

if /I "%~1"=="stop"    ( "%PY%" "%HERE%procman.py" stop    & exit /b %errorlevel% )
if /I "%~1"=="status"  ( "%PY%" "%HERE%procman.py" status  & exit /b %errorlevel% )
if /I "%~1"=="recover" ( "%PY%" "%HERE%recover.py"         & exit /b %errorlevel% )
if /I "%~1"=="clean"   ( "%PY%" -c "import storage,json;print(json.dumps(storage.retention(),indent=2))" & exit /b %errorlevel% )
if /I "%~1"=="cli"     ( shift & "%PY%" "%HERE%agent.py" %1 %2 %3 %4 %5 %6 & exit /b %errorlevel% )

if /I "%~1"=="restart" (
  "%PY%" "%HERE%procman.py" stop
  timeout /t 2 /nobreak >nul
)

echo ============================================
echo   aiX  -  starting stack (fast / parallel)
echo ============================================
"%PY%" "%HERE%procman.py" start

echo.
echo   launching web UI ...
start "aiX-web" /min "%PY%" "%HERE%webui.py"

echo.
echo   aiX is running.  Commands:
echo     aix.bat stop      stop all processes (incl. Ollama)
echo     aix.bat status    show process status
echo     aix.bat recover   run recovery after a power cut
echo     aix.bat cli       terminal chat instead of the web UI
echo.
echo   The web window should open at http://127.0.0.1:8765/
echo.
exit /b 0
