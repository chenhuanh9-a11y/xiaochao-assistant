@echo off
setlocal
set "QT_LAB_ROOT=%~dp0..\.."
if not exist "%QT_LAB_ROOT%\.venv-qt-isolated\Scripts\python.exe" (
  echo Missing task-only Python environment: .venv-qt-isolated
  exit /b 1
)
if /I "%~1"=="replay" (
  "%QT_LAB_ROOT%\.venv-qt-isolated\Scripts\python.exe" "%~dp0preflight.py" --replay "%QT_LAB_ROOT%\work\qt-replay\known-production\replay.json"
  if errorlevel 1 exit /b 1
  start "" "%QT_LAB_ROOT%\.venv-qt-isolated\Scripts\pythonw.exe" "%~dp0main.py" --replay "%QT_LAB_ROOT%\work\qt-replay\known-production\replay.json"
  exit /b 0
)
"%QT_LAB_ROOT%\.venv-qt-isolated\Scripts\python.exe" "%~dp0preflight.py"
if errorlevel 1 exit /b 1
if /I "%~1"=="check" exit /b 0
start "" "%QT_LAB_ROOT%\.venv-qt-isolated\Scripts\pythonw.exe" "%~dp0main.py"
