@echo off
cd /d "%~dp0"
where pyw >nul 2>nul
if errorlevel 1 (
  start "" pythonw "%~dp0DublaSync.pyw"
) else (
  start "" pyw "%~dp0DublaSync.pyw"
)
