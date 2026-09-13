@echo off
rem Minis Bridge: delayed Hermes CN Desktop restart (ASCII only, GBK-safe)
timeout /t 25 /nobreak >nul
taskkill /f /im hermes-agent-cn-desktop.exe >nul 2>&1
taskkill /f /im hermes-agent-cn-runtime-win32-x64.exe >nul 2>&1
timeout /t 3 /nobreak >nul
start "" "C:\Users\ABC\AppData\Local\Hermes Agent CN Desktop\hermes-agent-cn-desktop.exe"
echo restarted
