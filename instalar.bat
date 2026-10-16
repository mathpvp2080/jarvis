@echo off
chcp 65001 >nul
title Jarvis - instalacao
cd /d "%~dp0"

echo ============================================
echo   Jarvis - instalacao
echo ============================================
echo.

REM ---------------------------------------------------------------- Python
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)

if not defined PY (
    echo [X] O Python nao esta instalado.
    echo.
    echo     Baixe o Python 3.12 em https://www.python.org/downloads/
    echo     IMPORTANTE: marque "Add Python to PATH" na instalacao.
    echo     Depois rode este arquivo de novo.
    echo.
    start "" https://www.python.org/downloads/
    pause
    exit /b 1
)

%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
    echo [X] O Python encontrado e mais antigo que 3.11.
    echo     Instale o Python 3.12 (pode manter o antigo) e rode de novo.
    echo.
    start "" https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [OK] Python encontrado.

REM ------------------------------------------------------- ambiente virtual
if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Criando o ambiente virtual do Jarvis...
    %PY% -m venv .venv
    if errorlevel 1 goto erro
) else (
    echo [1/4] Ambiente virtual ja existe.
)

REM ------------------------------------------------------------ dependencias
echo [2/4] Instalando as dependencias (na primeira vez demora alguns minutos)...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if errorlevel 1 goto erro

REM -------------------------------------------------- modelos e Ollama
echo [3/4] Conferindo modelos e o Ollama...
".venv\Scripts\python.exe" instalar_jarvis.py
if errorlevel 1 goto erro

echo.
echo [4/4] Pronto.
echo.
echo   Para abrir a Jarvis, de dois cliques em:  iniciar.bat
echo.
pause
exit /b 0

:erro
echo.
echo [X] Algo deu errado acima. O texto do erro esta na tela.
echo     Se precisar de ajuda, copie as ultimas linhas e mande.
echo.
pause
exit /b 1
