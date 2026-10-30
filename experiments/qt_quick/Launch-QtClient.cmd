@echo off
setlocal
set "QT_CLIENT_ROOT=%~dp0..\.."
if not exist "%QT_CLIENT_ROOT%\.venv-qt-isolated\Scripts\python.exe" (
  echo Missing migration-only Python environment: .venv-qt-isolated
  exit /b 1
)
if /I "%~1"=="check" (
  "%QT_CLIENT_ROOT%\.venv-qt-isolated\Scripts\python.exe" "%~dp0client.py" --self-check
  exit /b %errorlevel%
)
start "" "%QT_CLIENT_ROOT%\.venv-qt-isolated\Scripts\pythonw.exe" "%~dp0client.py"
