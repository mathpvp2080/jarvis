@echo off
chcp 65001 >nul
title Jarvis
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo A Jarvis ainda nao foi instalada.
    echo De dois cliques em instalar.bat primeiro.
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" iniciar_jarvis.py

REM Se a Janela fechar sozinha, o texto acima diz o motivo.
if errorlevel 1 pause
