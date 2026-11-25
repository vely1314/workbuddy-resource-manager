@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "PY="
set "PYW="
set "TMPPY=%TEMP%\wb_console_python.txt"
set "TMPPYW=%TEMP%\wb_console_pythonw.txt"

rem === 1. WorkBuddy bundled Python (preferred, no installation needed) ===
set "WBPY=%USERPROFILE%\.workbuddy\binaries\python\versions"
if exist "%WBPY%" (
  for /f "delims=" %%D in ('dir /b /ad /o-n "%WBPY%" 2^>nul') do (
    if not defined PY if exist "%WBPY%\%%D\python.exe" set "PY=%WBPY%\%%D\python.exe"
    if not defined PYW if exist "%WBPY%\%%D\pythonw.exe" set "PYW=%WBPY%\%%D\pythonw.exe"
  )
)

rem === 2. python / python3 on PATH ===
if not defined PY (
  where python >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  where python3 >nul 2>nul
  if not errorlevel 1 set "PY=python3"
)

rem === 3. py launcher ===
if not defined PY (
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3 -c "import sys;print(sys.executable)" > "%TMPPY%" 2>nul
    if not errorlevel 1 (
      set /p PY=< "%TMPPY%"
      del "%TMPPY%" >nul 2>nul
    )
  )
)

if not defined PY (
  echo.
  echo [ERROR] Python not found on this computer.
  echo Install Python 3.10+ from https://www.python.org/downloads/
  echo or install WorkBuddy, which ships with a bundled Python.
  echo.
  pause
  exit /b 1
)

rem === derive pythonw so the service runs without a console window ===
if not defined PYW (
  "!PY!" -c "import os,sys;p=sys.executable;w=os.path.join(os.path.dirname(p),'pythonw.exe');print(w if os.path.isfile(w) else '')" > "%TMPPYW%" 2>nul
  set /p PYW=< "%TMPPYW%"
  del "%TMPPYW%" >nul 2>nul
)
if not defined PYW set "PYW=!PY!"

echo Checking local service...
"!PY!" -c "import socket;s=socket.socket();s.settimeout(1);r=s.connect_ex(('127.0.0.1',8765));s.close();exit(0 if r==0 else 1)" >nul 2>nul
if errorlevel 1 goto startsvc
goto openbrowser

:startsvc
echo Starting service with !PYW!
start "" "!PYW!" server.py
:waitloop
timeout /t 1 /nobreak >nul
"!PY!" -c "import socket;s=socket.socket();s.settimeout(1);r=s.connect_ex(('127.0.0.1',8765));s.close();exit(0 if r==0 else 1)" >nul 2>nul
if errorlevel 1 goto waitloop

:openbrowser
echo Service ready at http://127.0.0.1:8765
start "" http://127.0.0.1:8765
timeout /t 2 /nobreak >nul
