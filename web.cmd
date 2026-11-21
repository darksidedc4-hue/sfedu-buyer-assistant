@echo off
rem Показ в браузере: запустите этот файл и откройте http://127.0.0.1:8080
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% --version >nul 2>nul || (echo Нужен Python 3.9 или новее: https://www.python.org/downloads/ & pause & exit /b 1)
if not exist .env if "%LLM_API_KEY%"=="" (echo Нет ключа модели: скопируйте .env.example в .env и впишите ключ. & pause & exit /b 1)
start "" http://127.0.0.1:8080
%PY% server\serve.py %*
pause
